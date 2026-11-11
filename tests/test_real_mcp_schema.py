"""Offline actual FastMCP schema, not a decorator mock. No live calls."""
import unittest,os
from unittest.mock import patch
from types import SimpleNamespace
from mcp.server.fastmcp import FastMCP
from mcp_installer import install_rpc_tools
class SchemaTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_tools_list_optional_locator_string(self):
        mcp=FastMCP('offline-route-schema')
        with patch.dict(os.environ,{'XJ_SOURCE_RPC_HOST_PUBLIC_KEY':'0'*64}):
            installed=install_rpc_tools(mcp,lambda _:None,SimpleNamespace(dispatch=lambda *a:None),lambda:'offline',{},host_config=dict(budgets_verified=True,max_wait_seconds=80,function_budget_seconds=180,connector_budget_seconds=100))
        self.assertTrue(installed['enabled'])
        listing=await mcp.list_tools();tool=next(x for x in listing if x.name=='gateway_source_rpc_request')
        schema=tool.inputSchema
        self.assertEqual(set(schema['properties']),{'mailbox_id','request_json','target_locator_json'})
        self.assertEqual(set(schema['required']),{'mailbox_id','request_json'})
        self.assertEqual(schema['properties']['target_locator_json']['type'],'string')
        self.assertEqual(schema['properties']['target_locator_json']['default'],'')
        self.assertFalse(tool.annotations.readOnlyHint);self.assertFalse(tool.annotations.idempotentHint)
