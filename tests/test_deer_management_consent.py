"""Real original Source/Store and signed host; auth/policy identities are fixtures."""
import asyncio
import hashlib
import hmac
import importlib.util
import json
import os
import sqlite3
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from sqlalchemy import create_engine
from deerflow.persistence.agents.model import AgentRow
from deerflow.persistence.agents.sql import SqlAgentStore
from deerflow.persistence.base import Base

from deer_agent_management import AgentManagementDenied
from deer_management_consent import ManagementConsentStore
from deer_management_host import ManagementDefinitionService, project_management_user
from deer_management_rpc import ManagementRequest, install_management_rpc_tools
from fresh_batch_host import FreshBatchHost, initialize, canonical, digest


def original_source_modules():
    root = Path(os.environ["DR_ORIGINAL_SOURCE_IO"]).parent
    for name in ("platform_task_source", "platform_source_service", "platform_source_http", "platform_account_link"):
        full = "app.gateway." + name
        if full not in sys.modules:
            spec = importlib.util.spec_from_file_location(full, root / (name + ".py"))
            module = importlib.util.module_from_spec(spec)
            sys.modules[full] = module
            spec.loader.exec_module(module)
    return sys.modules["app.gateway.platform_task_source"]


class ConsentServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.io = original_source_modules()
        from app.gateway.platform_account_link import PinnedMcpAccount
        self.config = initialize(self.root / "host", owner="oauth:" + "a" * 64, platform_service=True)
        # Explicit isolated configuration, NEVER a live enrollment mutation.
        self.config["actions"].append("agent")
        path = self.root / "host/host.json"
        path.write_bytes(canonical(self.config))
        (self.root / "host/config.sha256").write_text(digest(self.config))
        self.host = FreshBatchHost(self.root / "host")
        self.account = PinnedMcpAccount(path, hashlib.sha256(path.read_bytes()).hexdigest())
        self.active, self.allowed, self.now = True, True, time.time()
        self.consents = ManagementConsentStore(self.root, account=self.account,
            current=lambda: self.active, source_io=self.io, clock=lambda: self.now)
        self.grant = self.approve()
        url = "sqlite:///" + str(self.root / "agents.sqlite")
        engine = create_engine(url)
        Base.metadata.create_all(engine, tables=[AgentRow.__table__])
        engine.dispose()
        self.agents = SqlAgentStore(url)
        async def policy(record):
            return self.allowed
        self.service = ManagementDefinitionService(consent_store=self.consents,
            grant_id=self.grant["grant_id"], agent_store=self.agents,
            live_policy=policy, current=lambda: self.active)

    async def asyncTearDown(self):
        self.agents._Session.kw["bind"].dispose()
        self.temp.cleanup()

    def approve(self, **changes):
        return self.consents.approve(**{ "user_id": "fixture-owner", "thread_id": "fixture-thread",
            "token_version": 3, "operation_key": "fixture-consent", "names": ["qa-dr-shared"],
            "actions": ["list", "read", "create"], "ttl_seconds": 600, **changes})

    def envelope(self, kind, key="create-1", **payload):
        body = dict(generation=self.config["generation"], owner=self.config["owner"],
            parent_id=self.config["parent_id"], action="agent", operation_key=key,
            payload={"kind": kind, **payload},
            request_id=digest([self.config["generation"], self.config["owner"], "agent", key]),
            expires_at=time.time() + 30)
        return {"body": body, "mac": hmac.new(self.host.ingress, canonical(body), "sha256").hexdigest()}

    async def call(self, kind, key="create-1", **payload):
        return await self.service.execute(self.envelope(kind, key, **payload)["body"])

    async def test_signed_original_host_create_independent_read_and_receipt(self):
        envelope = self.envelope("create", name="qa-dr-shared", soul="No execution.")
        response = await self.host.execute(envelope, repository=None, agent_management=self.service)
        self.host.key.public_key().verify(__import__('base64').b64decode(response["signature"]), canonical(response["body"]))
        saved = response["body"]["result"]
        self.assertEqual(saved["status"], "REGISTRY_SAVED_READBACK_REQUIRED")
        readback = await self.call("read", "read-1", name="qa-dr-shared")
        self.assertEqual(readback["agent"]["soul"], "No execution.")
        self.assertEqual(readback["agent"]["allowed_subagents"], [])
        receipt = await self.call("receipt", "receipt-1", name="qa-dr-shared",
            create_operation_key="create-1", payload_sha256=saved["payload_sha256"])
        self.assertEqual(receipt["status"], "CONFIRMED")
        self.assertFalse(self.agents.exists("qa-dr-shared", user_id="foreign"))

    async def test_duplicate_consent_never_extends_and_query_recovers(self):
        with self.assertRaisesRegex(AgentManagementDenied, "EXISTS_QUERY_ONLY"):
            self.approve(ttl_seconds=3600)
        result = self.consents.by_operation(user_id="fixture-owner", thread_id="fixture-thread",
            operation_key="fixture-consent", verified_owner=self.config["owner"])
        self.assertEqual(result, self.grant)

    async def test_checkpoint_issuer_is_not_a_management_grant(self):
        path = self.root / ("agent-management-consent-" + self.grant["grant_id"] + ".json")
        data = json.loads(path.read_text())
        data["schema"], data["scope"] = "platform-checkpoint-account-link-v1", "platform-checkpoint-only"
        path.write_bytes(self.io.encode(data))
        with self.assertRaisesRegex(AgentManagementDenied, "RECORD_INVALID"):
            await self.call("read", name="qa-dr-shared")

    async def test_expiry_revocation_stop_and_policy_deny_all_effects(self):
        self.allowed = False
        with self.assertRaisesRegex(AgentManagementDenied, "LIVE_OWNER_POLICY"):
            await self.call("create", name="qa-dr-shared", soul="no")
        self.allowed = True
        self.now += 601
        with self.assertRaisesRegex(AgentManagementDenied, "EXPIRED"):
            await self.call("create", name="qa-dr-shared", soul="no")
        self.now -= 601
        self.consents.revoke(self.grant["grant_id"], verified_owner=self.config["owner"],
            user_id="fixture-owner", thread_id="fixture-thread")
        with self.assertRaisesRegex(AgentManagementDenied, "REVOKED"):
            await self.call("create", name="qa-dr-shared", soul="no")
        self.assertFalse(self.agents.exists("qa-dr-shared", user_id="fixture-owner"))
        self.assertEqual(list(self.root.glob("agent-attempt-*")), [])

    async def test_owner_name_and_extra_capabilities_cannot_be_supplied(self):
        body = self.envelope("read", name="qa-dr-shared")["body"]
        body["owner"] = "oauth:" + "f" * 64
        with self.assertRaisesRegex(AgentManagementDenied, "OWNER_MISMATCH"):
            await self.service.execute(body)
        with self.assertRaisesRegex(AgentManagementDenied, "NAME_DENIED"):
            await self.call("create", name="other-agent", soul="no")
        for extra in ({"gateway_user_id": "admin"}, {"grant_id": self.grant["grant_id"]},
                      {"allowed_subagents": ["general-purpose"]}, {"model": "grok"},
                      {"url": "https://foreign.invalid"}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                await self.call("create", name="qa-dr-shared", soul="no", **extra)

    async def test_scoped_list_does_not_leak_other_definitions(self):
        self.agents.create("other-agent", {"name": "other-agent"}, "secret", user_id="fixture-owner")
        await self.call("create", name="qa-dr-shared", soul="no")
        result = await self.call("list", "list-1")
        self.assertEqual([x["name"] for x in result["agents"]], ["qa-dr-shared"])

    async def test_lost_create_reply_never_replays(self):
        create = self.agents.create
        def lost(*args, **kwargs):
            create(*args, **kwargs)
            raise TimeoutError("fixture lost reply")
        with patch.object(self.agents, "create", side_effect=lost):
            with self.assertRaises(TimeoutError):
                await self.call("create", name="qa-dr-shared", soul="no")
        with self.assertRaisesRegex(AgentManagementDenied, "UNKNOWN_QUERY_ONLY"):
            await self.call("create", name="qa-dr-shared", soul="no")
        self.assertTrue(self.agents.exists("qa-dr-shared", user_id="fixture-owner"))
        self.assertEqual(len(self.agents.list(user_id="fixture-owner")), 1)

    async def test_signed_ingress_tamper_and_unbound_host_reject(self):
        envelope = self.envelope("create", name="qa-dr-shared", soul="no")
        envelope["body"]["payload"]["name"] = "other-agent"
        with self.assertRaisesRegex(ValueError, "SIGNATURE_INVALID"):
            await self.host.execute(envelope, repository=None, agent_management=self.service)
        with self.assertRaisesRegex(ValueError, "NOT_BOUND"):
            await self.host.execute(self.envelope("list"), repository=None)
        self.assertEqual(self.agents.list(user_id="fixture-owner"), [])

    async def test_expiry_after_await_prevents_create(self):
        async def changed(record):
            self.now += 601
            return True
        self.service.live_policy = changed
        with self.assertRaisesRegex(AgentManagementDenied, "EXPIRED"):
            await self.call("create", name="qa-dr-shared", soul="no")
        self.assertEqual(self.agents.list(user_id="fixture-owner"), [])

    async def test_source_stop_and_pin_mutation_prevent_reads(self):
        (self.root / "STOP").touch()
        with self.assertRaisesRegex(AgentManagementDenied, "SOURCE_STOPPED"):
            await self.call("list")
        (self.root / "STOP").unlink()
        path = self.root / "host/host.json"
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "CONFIG_CHANGED"):
            await self.call("list")

    async def test_tools_off_by_default_and_model_inputs_excluded(self):
        self.assertFalse(install_management_rpc_tools(object(), rpc=object(), principal=None, oauth_meta={}))
        from mcp.server.fastmcp import FastMCP
        mcp = FastMCP("fixture-management")
        calls = []
        def call(**kwargs):
            calls.append(kwargs)
            return {"status": "fixture"}
        rpc = SimpleNamespace(config=self.config, call=call, query=call)
        self.assertTrue(install_management_rpc_tools(mcp, rpc=rpc,
            principal=lambda: self.config["owner"], oauth_meta={}, enabled=True))
        tools = {tool.name: tool for tool in await mcp.list_tools()}
        self.assertEqual(set(tools), {"agent_manage", "agent_management_receipt"})
        schema = json.dumps(tools["agent_manage"].inputSchema)
        self.assertNotIn('"gateway_user_id"', schema)
        self.assertNotIn('"grant_id"', schema)
        await mcp.call_tool("agent_manage", {"request": {"operation_key": "read-mcp", "kind": "list"}})
        self.assertEqual(calls[0]["owner"], self.config["owner"])
        self.assertEqual(calls[0]["action"], "agent")

    async def test_original_sql_live_identity_rechecks_token_role_and_thread(self):
        database = self.root / "identity.sqlite"
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE users(id TEXT,system_role TEXT,token_version INTEGER,needs_setup INTEGER,oauth_provider TEXT,oauth_id TEXT)")
            db.execute("INSERT INTO users VALUES('fixture-owner','admin',3,0,NULL,NULL)")
            db.execute("CREATE TABLE threads_meta(thread_id TEXT,user_id TEXT)")
            db.execute("INSERT INTO threads_meta VALUES('fixture-thread','fixture-owner')")
        user = project_management_user(database, self.grant)
        self.assertEqual(user.id, "fixture-owner")
        self.assertNotIn("password_hash", vars(user))
        for update, restore in (("UPDATE users SET token_version=4", "UPDATE users SET token_version=3"),
                ("UPDATE users SET system_role='user'", "UPDATE users SET system_role='admin'"),
                ("UPDATE users SET needs_setup=1", "UPDATE users SET needs_setup=0"),
                ("UPDATE threads_meta SET user_id='foreign'", "UPDATE threads_meta SET user_id='fixture-owner'")):
            with sqlite3.connect(database) as db:
                db.execute(update)
            with self.assertRaisesRegex(AgentManagementDenied, "LIVE_USER_DENIED"):
                project_management_user(database, self.grant)
            with sqlite3.connect(database) as db:
                db.execute(restore)

    async def test_revocation_during_policy_await_prevents_effect(self):
        async def revoke(record):
            self.consents.revoke(self.grant["grant_id"], verified_owner=self.config["owner"],
                user_id="fixture-owner", thread_id="fixture-thread")
            return True
        self.service.live_policy = revoke
        with self.assertRaisesRegex(AgentManagementDenied, "REVOKED"):
            await self.call("create", name="qa-dr-shared", soul="no")
        self.assertEqual(list(self.root.glob("agent-attempt-*")), [])


if __name__ == "__main__":
    unittest.main()
