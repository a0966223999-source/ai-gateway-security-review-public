"""Actual original auth decorator, CSRF, Source IO; credential verifier is a fixture."""
from contextlib import asynccontextmanager
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from gateway_agent_consent import install_gateway_management_consent
from test_deer_management_consent import original_source_modules


class ConsentHttpTests(unittest.TestCase):
    def setUp(self):
        original_source_modules()
        from app.gateway.platform_account_link import PinnedMcpAccount
        from app.gateway.platform_source_http import PlatformSourceRuntime
        from app.gateway.authz import AuthContext
        from app.gateway.csrf_middleware import CSRFMiddleware
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        root.chmod(0o700)
        host = root / "host"
        host.mkdir(mode=0o700)
        config = host / "host.json"
        config.write_text(json.dumps({"schema": "fresh-deer-batch-host-v1",
            "owner": "oauth:" + "a" * 64, "generation": "b" * 32,
            "lifecycle": "owner-platform-service-v1", "expires_at": None}))
        config.chmod(0o600)
        self.runtime = PlatformSourceRuntime(root / "sources", account=PinnedMcpAccount(
            config, hashlib.sha256(config.read_bytes()).hexdigest()))
        self.metadata = {"user_id": "fixture-owner"}
        self.permissions = ["runs:create", "threads:write"]
        async def get(thread_id, *, user_id):
            return self.metadata
        async def authenticate(request):
            return AuthContext(user=request.state.user, permissions=self.permissions)
        self.patches = [patch.dict(sys.modules, {"app.gateway.deps": SimpleNamespace(
            get_thread_store=lambda request: SimpleNamespace(get=get))}),
            patch("app.gateway.authz._authenticate", side_effect=authenticate),
            patch("app.gateway.csrf_middleware.is_auth_disabled", return_value=False)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        @asynccontextmanager
        async def lifespan(app):
            with self.runtime.install(app):
                yield
        self.app = FastAPI(lifespan=lifespan)
        self.app.add_middleware(CSRFMiddleware)
        @self.app.middleware("http")
        async def fixture_identity(request, call_next):
            user = request.headers.get("x-fixture-user")
            request.state.user = SimpleNamespace(id=user,
                system_role=request.headers.get("x-fixture-role", "admin"),
                token_version=3, needs_setup=False) if user else None
            request.state.auth_source = request.headers.get("x-fixture-origin", "session")
            return await call_next(request)
        install_gateway_management_consent(self.app, enabled=True)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.client.cookies.set("csrf_token", "fixture-csrf")
        self.headers = {"x-fixture-user": "fixture-owner", "X-CSRF-Token": "fixture-csrf"}
        self.base = "/api/threads/fixture-thread/agent-management-consents"
        self.body = {"operation_key": "fixture-consent", "names": ["qa-dr-shared"],
            "actions": ["list", "read", "create"], "ttl_seconds": 600,
            "confirmation": "named-definitions-only-no-models-tools-or-delegation"}

    def create(self, **changes):
        return self.client.post(self.base, headers=self.headers, json={**self.body, **changes})

    def test_original_session_approve_query_and_revoke(self):
        result = self.create()
        self.assertEqual(result.status_code, 200, result.text)
        record = result.json()
        self.assertFalse(record["model_execution"])
        self.assertNotIn("gateway_user_id", record)
        self.assertNotIn("mcp_owner", record)
        queried = self.client.get(self.base + "/by-operation/fixture-consent", headers=self.headers)
        self.assertEqual(queried.json(), record)
        self.assertEqual(self.create().status_code, 409)
        revoked = self.client.delete(self.base + "/" + record["grant_id"], headers=self.headers)
        self.assertTrue(revoked.json()["revoked"])

    def test_default_off_publishes_no_management_routes(self):
        app = FastAPI()
        self.assertFalse(install_gateway_management_consent(app))
        self.assertFalse(any("agent-management" in route.path for route in app.routes))

    def test_consent_page_has_exact_review_scope_and_no_automatic_post(self):
        response = self.client.get('/api/v1/auth/agent-management-consent')
        self.assertEqual(response.status_code, 200)
        self.assertIn('qa-dr-mcp-20261112', response.text)
        self.assertIn('ttl_seconds: 600', response.text)
        self.assertIn("byId('approve').onclick", response.text)
        self.assertIn('no-store', response.headers['cache-control'])
        self.assertIn("frame-ancestors 'none'", response.headers['content-security-policy'])
        self.assertEqual(list(self.runtime.root.glob("agent-management-consent-*")), [])

    def test_missing_or_wrong_csrf_is_denied_before_consent(self):
        for headers in ({"x-fixture-user": "fixture-owner"},
                        {**self.headers, "X-CSRF-Token": "wrong"}):
            response = self.client.post(self.base, headers=headers, json=self.body)
            self.assertEqual(response.status_code, 403)
        self.assertEqual(list(self.runtime.root.glob("agent-management-consent-*")), [])

    def test_pat_internal_auth_disabled_and_nonadmin_cannot_issue(self):
        for origin in ("pat", "internal", "auth_disabled"):
            response = self.client.post(self.base,
                headers={**self.headers, "x-fixture-origin": origin}, json=self.body)
            self.assertEqual(response.status_code, 403, response.text)
        response = self.client.post(self.base, headers={**self.headers, "x-fixture-role": "user"}, json=self.body)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(list(self.runtime.root.glob("agent-management-consent-*")), [])

    def test_anonymous_foreign_thread_and_original_policy_denied(self):
        response = self.client.post(self.base, headers={"X-CSRF-Token": "fixture-csrf"}, json=self.body)
        self.assertEqual(response.status_code, 401)
        self.metadata = {"user_id": "foreign"}
        self.assertEqual(self.create().status_code, 403)
        self.metadata = {"user_id": "fixture-owner"}
        self.permissions = ["runs:create"]
        self.assertEqual(self.create().status_code, 403)
        self.assertEqual(list(self.runtime.root.glob("agent-management-consent-*")), [])

    def test_no_wildcards_unbounded_ttl_or_owner_injection(self):
        for changes in ({"names": ["*"]}, {"ttl_seconds": 3601}, {"ttl_seconds": True},
                        {"gateway_user_id": "admin"}, {"actions": ["execute"]},
                        {"confirmation": "yes"}):
            with self.subTest(changes=changes):
                self.assertIn(self.create(**changes).status_code, (403, 422))
        self.assertEqual(list(self.runtime.root.glob("agent-management-consent-*")), [])
