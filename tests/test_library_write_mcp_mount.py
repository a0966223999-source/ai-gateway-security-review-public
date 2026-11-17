"""Real FastMCP create/update/status tool visibility and delegated source guards."""
import hashlib
import json
import os
import time
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.server.auth.provider import AccessToken
import mcp_bridge
from library_catalog_gate import OriginalLibraryScope
from library_write_gate import OriginalLibraryWriteScope, request_digest

OWNER = "oauth:" + hashlib.sha256(
    b"https://fixture.auth0.com/|fixture-owner").hexdigest()
CLIENT = "fixture-client"
REVISION = "a" * 64


class Verifier:
    issuer = "https://fixture.auth0.com/"

    async def verify_token(self, token):
        if token != "fixture-token":
            return None
        return AccessToken(
            token=token, client_id=CLIENT, scopes=["synthetic:events"],
            expires_at=int(time.time()) + 1000, subject="fixture-owner")


class LibraryWriteMountTests(unittest.TestCase):
    def setUp(self):
        self.current = True
        self.writes = []
        self.receipts = {}

    async def reader(self, ctx, owner, client_id, action, ref):
        async def search(query, cursor, limit):
            return {"schema": "chatgpt-library-catalog-page-v1",
                    "source": "chatgpt-library", "owner": OWNER,
                    "revision": REVISION, "items": [], "next_cursor": ""}
        async def read(ref, version):
            raise RuntimeError("not part of write test")
        return OriginalLibraryScope(owner=OWNER, client_id=CLIENT, revision=REVISION,
            is_current=lambda: self.current,
            search_original=search, read_original=read)

    async def writer(self, ctx, owner, client_id, operation,
                     target_ref, expected_version, operation_key):
        async def commit(request):
            self.writes.append(request)
            self.receipts[operation_key] = {
                "schema": "original-library-write-receipt-v1",
                "source": "chatgpt-library",
                "owner": OWNER, "client_id": CLIENT,
                "operation": request["operation"],
                "target_ref": request["target_ref"],
                "expected_version": request["expected_version"],
                "operation_key": request["operation_key"],
                "ref": "new_opaque" if operation == "create" else target_ref,
                "version": "v4", "status": "committed",
                "request_digest": request_digest(request)}
        async def receipt(op_key):
            return self.receipts.get(op_key)
        return OriginalLibraryWriteScope(
            owner=OWNER, client_id=CLIENT, source="chatgpt-library",
            operation=operation, target_ref=target_ref,
            expected_version=expected_version, operation_key=operation_key,
            expires_at=time.time() + 60, is_current=lambda: self.current,
            commit_original=commit, receipt_original=receipt)

    def run_client(self, reader, writer, check):
        env = {
            "VERCEL_URL": "fixture.vercel.app",
            "XJ_MCP_PUBLIC_HOST": "fixture.vercel.app",
            "VERCEL_ENV": "preview",
            "XJ_AUTH0_ALLOWED_SUBJECT": "fixture-owner",
            "XJ_INBOX_SECRET_KEY": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}
        with patch.dict(os.environ, env, clear=True), patch.object(
                mcp_bridge, "Auth0Verifier", Verifier):
            app = FastAPI()
            mcp_bridge.install(
                app, lambda *a: None, lambda *a: None, dict,
                library_catalog_scope_resolver=reader,
                library_write_scope_resolver=writer)
            with TestClient(app, base_url="https://fixture.vercel.app") as client:
                def call(method, params):
                    response = client.post(
                        "/mcp", headers={
                            "Authorization": "Bearer fixture-token",
                            "Accept": "application/json, text/event-stream"},
                        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
                    self.assertEqual(response.status_code, 200)
                    return response.json()
                check(call)

    def test_no_writer_binding_has_no_mutation_tools(self):
        def check(call):
            names = {tool["name"] for tool in call("tools/list", {})["result"]["tools"]}
            self.assertNotIn("library_item_create", names)
            self.assertNotIn("library_item_update", names)
            self.assertNotIn("library_write_status", names)
        self.run_client(self.reader, None, check)

    def test_writer_cannot_be_bound_without_reader(self):
        with self.assertRaisesRegex(ValueError, "ORIGINAL_LIBRARY_WRITE_RESOLVER_REQUIRED"):
            with patch.dict(os.environ, {"XJ_INBOX_SECRET_KEY": "A"*44,
                                          "VERCEL_URL": "fixture.vercel.app",
                                          "XJ_MCP_PUBLIC_HOST": "fixture.vercel.app"}, clear=False):
                # Validation happens before Auth0Verifier is constructed.
                mcp_bridge.install(FastAPI(), lambda *a: None,
                                   lambda *a: None, dict,
                                   library_write_scope_resolver=self.writer)

    def test_bound_write_annotations_and_exact_commit(self):
        def check(call):
            records = {t["name"]: t for t in call("tools/list", {})["result"]["tools"]}
            self.assertFalse(records["library_item_create"]["annotations"]["readOnlyHint"])
            self.assertTrue(records["library_item_update"]["annotations"]["destructiveHint"])
            self.assertTrue(records["library_write_status"]["annotations"]["readOnlyHint"])
            response = call("tools/call", {"name": "library_item_update",
                "arguments": {"ref": "f_007", "expected_version": "v3",
                              "mime_type": "application/json",
                              "content": '{"notes":["ok"]}',
                              "operation_key": "op01"}})["result"]
            self.assertFalse(response.get("isError", False), response)
            contents = response.get("structuredContent") or json.loads(response["content"][0]["text"])
            self.assertEqual(contents["ref"], "f_007")
            self.assertEqual(len(self.writes), 1)
            self.assertEqual(self.writes[0]["expected_version"], "v3")
            status = call("tools/call", {"name": "library_write_status",
                "arguments": {"operation_key": "op01"}})["result"]
            self.assertFalse(status.get("isError", False), status)
            self.assertEqual(len(self.writes), 1)
        self.run_client(self.reader, self.writer, check)

    def test_bound_create_in_authorized_folder(self):
        def check(call):
            response = call("tools/call", {"name": "library_item_create",
                "arguments": {"parent_ref": "parent01", "name": "my-note.md",
                    "mime_type": "text/markdown", "content": "# noted",
                    "operation_key": "op-create"}})["result"]
            self.assertFalse(response.get("isError", False), response)
            self.assertEqual(self.writes[0]["operation"], "create")
            self.assertEqual(self.writes[0]["expected_version"], "create-only")
        self.run_client(self.reader, self.writer, check)

    def test_revoked_writer_returns_uniform_denial(self):
        self.current = False
        def check(call):
            response = call("tools/call", {"name": "library_item_update",
                "arguments": {"ref": "f_007", "expected_version": "v3",
                    "mime_type": "text/plain", "content": "X",
                    "operation_key": "op-revoked"}})["result"]
            self.assertTrue(response.get("isError", False), response)
            self.assertFalse(self.writes)
            self.assertNotIn("f_007", json.dumps(response))
        self.run_client(self.reader, self.writer, check)


if __name__ == "__main__":
    unittest.main()
