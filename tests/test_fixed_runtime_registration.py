"""Actual original FastMCP registration; no network or live grants."""
import json
import tempfile
import unittest
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from fresh_batch_host import initialize
from fresh_batch_rpc import install_fresh_batch_tools


class RuntimeRegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_opt_in_runtime_and_original_background_tools_coexist(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'host'
            config=initialize(root,owner='oauth:'+'a'*64,platform_service=True,enable_codex=True)
            mcp=FastMCP('fixture-runtime-registration')
            env=dict(XJ_FRESH_BATCH_HOST_ENABLED='true',XJ_FIXED_AGENT_RUNTIME_ENABLED='true',
                XJ_FRESH_BATCH_HOST_CONFIG=json.dumps(config),
                XJ_FRESH_BATCH_INGRESS_KEY=(root/'ingress.key').read_bytes().hex())
            install_fresh_batch_tools(mcp,principal=lambda:config['owner'],oauth_meta={},
                env=env,connection_factory=lambda:None)
            tools={t.name:t for t in await mcp.list_tools()}
            self.assertTrue({'fixed_agent_task','fixed_agent_receipt','batch_execute_batch','batch_stop'}<=tools.keys())
            schema=json.dumps(tools['fixed_agent_task'].inputSchema)
            self.assertNotIn('grant_id',schema)
            self.assertNotIn('executable',schema)
            self.assertIn('timeout_seconds',schema)
