"""MCP bridge status is authenticated and never lists private Library files."""
import json
import os
import time
import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.server.auth.provider import AccessToken
import mcp_bridge


class FixtureVerifier:
    issuer = "https://fixture.auth0.com/"

    async def verify_token(self, bearer):
        if bearer != "fixture-token":
            return None
        return AccessToken(
            token=bearer, client_id="tpc_client",
            subject="fixture-owner", scopes=["synthetic:events"],
            expires_at=int(time.time())+1000)


class StatusContract(unittest.TestCase):
    def test_unbound_status_and_no_library_mutation_tools(self):
        env = {"VERCEL_URL":"fixture.vercel.app",
               "XJ_MCP_PUBLIC_HOST":"fixture.vercel.app",
               "VERCEL_ENV":"preview",
               "XJ_AUTH0_ALLOWED_SUBJECT":"fixture-owner",
               "XJ_INBOX_SECRET_KEY":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}
        with patch.dict(os.environ,env,clear=True), patch.object(
                mcp_bridge,"Auth0Verifier",FixtureVerifier):
            app=FastAPI()
            mcp_bridge.install(app,lambda *a:None,lambda *a:None,dict)
            with TestClient(app,base_url="https://fixture.vercel.app") as client:
                def call(method,params,token="fixture-token"):
                    return client.post("/mcp",headers={
                        "Authorization":"Bearer "+token,
                        "Accept":"application/json, text/event-stream"},
                        json={"jsonrpc":"2.0","id":1,"method":method,"params":params})
                tools=call("tools/list",{})
                self.assertEqual(tools.status_code,200)
                names={t["name"] for t in tools.json()["result"]["tools"]}
                self.assertIn("library_bridge_status",names)
                self.assertNotIn("library_item_create",names)
                self.assertNotIn("library_item_update",names)
                self.assertNotIn("library_catalog_search",names)
                r=call("tools/call",{"name":"library_bridge_status","arguments":{}})
                self.assertEqual(r.status_code,200)
                result=r.json()["result"]
                self.assertFalse(result.get("isError"),result)
                val=result.get("structuredContent") or json.loads(result["content"][0]["text"])
                self.assertEqual(val["state"],"NOT_BOUND")
                self.assertFalse(val["source_live_verified"])
                self.assertFalse(val["native_chatgpt_library_api_available"])
                raw=json.dumps(r.json())
                self.assertNotIn("fixture-owner",raw)
                self.assertNotIn("tpc_client",raw)
                denied=call("tools/call",{"name":"library_bridge_status","arguments":{}},token="invalid")
                self.assertNotEqual(denied.status_code,200)


if __name__=="__main__":
    unittest.main()
