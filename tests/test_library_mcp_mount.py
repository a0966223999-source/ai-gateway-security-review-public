"""Real FastMCP mount tests using offline fixture OAuth and Reader. No Library I/O."""
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


OWNER = "oauth:" + hashlib.sha256(
    b"https://fixture.auth0.com/|fixture-owner").hexdigest()
REV = "a" * 64


class Verifier:
    issuer = "https://fixture.auth0.com/"

    async def verify_token(self, token):
        if token != "fixture-token":
            return None
        return AccessToken(
            token=token, client_id="fixture-client",
            scopes=["synthetic:events"], expires_at=int(time.time()) + 1000,
            subject="fixture-owner")


class LibraryMountTests(unittest.TestCase):
    def setUp(self):
        self.active = True

        async def search_original(query, cursor, limit):
            return {
                "schema": "chatgpt-library-catalog-page-v1",
                "source": "chatgpt-library", "owner": OWNER, "revision": REV,
                "items": [{"ref": "opaque_1", "name": "diagram.png",
                           "kind": "file", "mime_type": "image/png",
                           "version": "2"}], "next_cursor": ""}

        async def read_original(ref, version):
            return {
                "schema": "chatgpt-library-item-view-v1",
                "source": "chatgpt-library", "owner": OWNER, "revision": REV,
                "ref": ref, "version": version, "mime_type": "image/png",
                "preview_kind": "image_description",
                "preview": "authorized fake diagram"}

        self.scope = OriginalLibraryScope(
            owner=OWNER, client_id="fixture-client", revision=REV,
            is_current=lambda: self.active,
            search_original=search_original, read_original=read_original)

    async def resolve(self, ctx, owner, client_id, action, ref):
        return self.scope

    def client(self, resolver, check):
        env = dict(
            VERCEL_URL="fixture.vercel.app",
            XJ_MCP_PUBLIC_HOST="fixture.vercel.app",
            VERCEL_ENV="preview",
            XJ_AUTH0_ALLOWED_SUBJECT="fixture-owner",
            XJ_INBOX_SECRET_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
        with patch.dict(os.environ, env, clear=True), patch.object(
                mcp_bridge, "Auth0Verifier", Verifier):
            app = FastAPI()
            mcp_bridge.install(
                app, lambda *a: None, lambda *a: None, dict,
                library_catalog_scope_resolver=resolver)
            with TestClient(app, base_url="https://fixture.vercel.app") as client:
                def call(method, params):
                    reply = client.post(
                        "/mcp", headers={
                            "Authorization": "Bearer fixture-token",
                            "Accept": "application/json, text/event-stream"},
                        json={"jsonrpc": "2.0", "id": 1,
                              "method": method, "params": params})
                    self.assertEqual(reply.status_code, 200)
                    return reply.json()
                check(call)

    def test_unbound_has_no_library_tools(self):
        def check(call):
            names = {x["name"] for x in call("tools/list", {})["result"]["tools"]}
            self.assertNotIn("library_catalog_search", names)
            self.assertNotIn("library_item_read", names)
            self.assertIn("redis_test_read", names)
        self.client(None, check)

    def test_bound_readonly_catalog_and_diagram(self):
        def check(call):
            tools = {x["name"]: x for x in call("tools/list", {})["result"]["tools"]}
            self.assertTrue(tools["library_catalog_search"]["annotations"]["readOnlyHint"])
            self.assertTrue(tools["library_item_read"]["annotations"]["readOnlyHint"])
            page = call("tools/call", {"name": "library_catalog_search",
                                     "arguments": {"query": "diagram", "limit": 20}})["result"]
            self.assertFalse(page.get("isError", False), page)
            content = page.get("structuredContent") or json.loads(page["content"][0]["text"])
            self.assertEqual(content["items"][0]["name"], "diagram.png")
            read = call("tools/call", {"name": "library_item_read", "arguments": {
                "ref": "opaque_1", "expected_version": "2"}})["result"]
            self.assertFalse(read.get("isError", False), read)
            view = read.get("structuredContent") or json.loads(read["content"][0]["text"])
            self.assertEqual(view["preview_kind"], "image_description")
        self.client(self.resolve, check)

    def test_bound_revoked_reader_denies_without_metadata(self):
        self.active = False
        def check(call):
            outcome = call("tools/call", {"name": "library_catalog_search",
                                         "arguments": {"query": ""}})["result"]
            self.assertTrue(outcome.get("isError", False), outcome)
            self.assertNotIn("diagram.png", json.dumps(outcome))
        self.client(self.resolve, check)


if __name__ == "__main__":
    unittest.main()
