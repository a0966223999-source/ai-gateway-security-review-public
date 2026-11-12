"""MCP registration is tested without network, Redis or execution effects."""
import json
from pathlib import Path
import tempfile
import unittest

from fresh_batch_host import initialize
from fresh_batch_rpc import install_fresh_batch_tools
from mcp.server.fastmcp import FastMCP


class RegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_tool_and_union_preserve_single_item_schema(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'host'
            config = initialize(root, owner='oauth:' + 'a' * 64,
                                platform_service=True, enable_codex=True)
            mcp = FastMCP('isolated-batch-registration')
            env = dict(XJ_FRESH_BATCH_HOST_ENABLED='true',
                       XJ_FRESH_BATCH_HOST_CONFIG=json.dumps(config),
                       XJ_FRESH_BATCH_INGRESS_KEY=(root / 'ingress.key').read_bytes().hex())
            install_fresh_batch_tools(mcp, principal=lambda: config['owner'], oauth_meta={},
                                     env=env, connection_factory=lambda: None)
            tools = {tool.name: tool for tool in await mcp.list_tools()}
            self.assertIn('batch_execute_batch', tools)
            self.assertIn('batch_stop', tools)
            single = json.dumps(tools['batch_execute'].inputSchema)
            self.assertIn('codex_once', single)
            self.assertNotIn('codex_batch_once', single)
            union = json.dumps(tools['batch_task'].inputSchema)
            self.assertIn('codex_batch_once', union)
            schema = json.dumps(tools['batch_execute_batch'].inputSchema)
            self.assertIn('item_ids', schema)
            self.assertNotIn('executable', schema)
            self.assertNotIn('prompt', schema)
