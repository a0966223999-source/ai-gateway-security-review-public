"""Actual MCP registry/schema; authority and transport are explicit fixtures."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mcp.server.fastmcp import FastMCP
from fresh_batch_host import initialize
from fresh_batch_rpc import install_fresh_batch_tools


class StopMcpSchemaTests(unittest.TestCase):
    def test_stop_registered_at_install_and_compatible_mode_reaches_original_action(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)/'host'
            config = initialize(root,owner='oauth:'+'a'*64,platform_service=True,enable_codex=True)
            server = FastMCP('isolated-stop-schema')
            install_fresh_batch_tools(server,principal=lambda:config['owner'],oauth_meta={},
                env={'XJ_FRESH_BATCH_HOST_ENABLED':'true','XJ_FRESH_BATCH_HOST_CONFIG':json.dumps(config),
                     'XJ_FRESH_BATCH_INGRESS_KEY':(root/'ingress.key').read_bytes().hex()},
                connection_factory=lambda:None)
            tools = asyncio.run(server.list_tools())
            self.assertIn('batch_stop',[tool.name for tool in tools])
            batch_schema = next(tool.inputSchema for tool in tools if tool.name=='batch_task')
            self.assertIn('stop_codex_once',json.dumps(batch_schema))
            with patch('fresh_batch_rpc.BatchRPC.call',return_value={'status':'synthetic transport fixture'}) as call:
                asyncio.run(server.call_tool('batch_task',{'request':{
                    'parent_id':config['parent_id'],'operation_key':'fixture-stop',
                    'batch_id':'fixture-batch','item_id':'fixture-item','execution_mode':'stop_codex_once'}}))
                self.assertEqual(call.call_args.kwargs['action'],'execute')
                self.assertEqual(call.call_args.kwargs['payload'],{
                    'batch_id':'fixture-batch','item_id':'fixture-item','execution_mode':'stop_codex_once'})


if __name__ == '__main__':
    unittest.main()
