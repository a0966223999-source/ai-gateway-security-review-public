"""Fixture transport verifies bounded waits; no model or live authority."""
import unittest
from types import SimpleNamespace
from mcp.server.fastmcp import FastMCP
from deer_fixed_runtime_rpc import install_fixed_runtime_tools

class FixedWaitTests(unittest.IsolatedAsyncioTestCase):
    async def test_short_execute_and_bounded_control_waits(self):
        calls=[]
        def call(**kw):
            calls.append(kw)
            return {'request_id':'fixture','result':{'kind':'original-deer-fixed-runtime-v1','status':'fixture'}}
        rpc=SimpleNamespace(config={'actions':['read','submit','execute'],'parent_id':'fixture'},
                            query=lambda **kw:{'status':'NOT_FOUND_QUERY_ONLY'},call=call)
        mcp=FastMCP('fixture-fixed-wait')
        install_fixed_runtime_tools(mcp,rpc=rpc,principal=lambda:'owner',oauth_meta={},enabled=True)
        for kind,extra in [('prepare',{'task':'arithmetic'}),('status',{'batch_id':'batch'}),
                           ('stop',{'batch_id':'batch','item_id':'item'}),
                           ('execute',{'batch_id':'batch','item_id':'item'})]:
            reply=await mcp.call_tool('fixed_agent_task',{'request':{'kind':kind,'name':'qa-role','operation_key':kind,**extra}})
            self.assertNotIn('isError=True',str(reply))
        self.assertEqual([c['response_timeout'] for c in calls],[12,12,12,3])
        self.assertEqual([c['action'] for c in calls],['submit','read','execute','execute'])
        self.assertEqual(len(calls),4)
