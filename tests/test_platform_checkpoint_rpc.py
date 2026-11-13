"""Original FastMCP schema/call tests with a disclosed transport fixture."""
import json
import unittest
from unittest.mock import patch
from mcp.server.fastmcp import FastMCP

from platform_checkpoint_rpc import install_checkpoint_rpc_tools,MODE

class FakeRPC:
    def __init__(self,**kwargs):
        self.config=kwargs['config'];self.calls=[];self.queries=[];self.query_value=None
        self.value={'request_id':'a'*64,'result':{'kind':MODE,'scope':'platform-checkpoint-only','native_work_authority':False,'status':'fixture-only'}}
    def call(self,**kw):self.calls.append(kw);return self.value
    def query(self,**kw):self.queries.append(kw);return self.query_value or {"request_id":"a"*64,"status":"NOT_FOUND_QUERY_ONLY"}

class Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.owner='fixture-owner';self.server=FastMCP('fixture-original-mcp')
        self.rpc=None
        def make(**kw):self.rpc=FakeRPC(**kw);return self.rpc
        self.env={'XJ_FRESH_BATCH_HOST_ENABLED':'true','XJ_FRESH_BATCH_HOST_CONFIG':json.dumps({'parent_id':'fixture-parent'}),'XJ_FRESH_BATCH_INGRESS_KEY':'a'*64}
        with patch('fresh_batch_rpc.BatchRPC',make):
            install_checkpoint_rpc_tools(self.server,principal=lambda:self.owner,oauth_meta={},env=self.env,connection_factory=lambda:None)
        self.base={'thread_id':'fixture-thread','link_id':'b'*64,'operation_key':'fixture-op'}

    async def call(self,name,**kw):
        result=await self.server.call_tool('gateway_checkpoint_'+name,{'request':{**self.base,**kw}})
        content=result[0] if isinstance(result,tuple) else result
        return '\n'.join(x.text for x in content if hasattr(x,'text'))

    async def test_seven_tools_and_no_identity_credentials_or_route_in_schema(self):
        tools=await self.server.list_tools();self.assertEqual(len(tools),7)
        for t in tools:
            raw=json.dumps(t.inputSchema)
            for name in ['gateway_user_id','mcp_owner','password','credential','endpoint','native_work_authority']:
                self.assertNotIn('"'+name+'"',raw)
        self.assertEqual(self.rpc.calls,[])

    async def test_create_uses_original_owner_and_parent_not_model_fields(self):
        await self.call('create',checkpoint={'status':'IN_PROGRESS'})
        c=self.rpc.calls[0]
        self.assertEqual((c['owner'],c['parent'],c['action']),('fixture-owner','fixture-parent','execute'))
        self.assertEqual(c['payload'],dict(execution_mode=MODE,checkpoint_action='create',thread_id='fixture-thread',link_id='b'*64,checkpoint={'status':'IN_PROGRESS'},ttl_seconds=600))

    async def test_query_is_read_only_and_preserves_original_source_operation(self):
        await self.call('query',source_operation_key='fixture-original')
        self.assertEqual(self.rpc.calls[0]['action'],'read')
        self.assertEqual(self.rpc.calls[0]['payload']['source_operation_key'],'fixture-original')

    async def test_cas_fields_pass_unchanged(self):
        await self.call('save',source_key='c'*64,expected_revision='d'*64,checkpoint={'next':'fixture'})
        self.assertEqual(self.rpc.calls[0]['payload']['expected_revision'],'d'*64)
        self.assertEqual(self.rpc.calls[0]['payload']['checkpoint'],{'next':'fixture'})

    async def test_identity_injection_rejected_before_transport(self):
        with self.assertRaises(Exception):await self.call('create',checkpoint={},gateway_user_id='other')
        self.assertEqual(self.rpc.calls,[])

    async def test_receipt_queries_only_original_key_never_enqueues(self):
        self.rpc.query_value=self.rpc.value
        await self.server.call_tool('gateway_checkpoint_receipt',{'request':{'operation_key':'fixture-original','operation_kind':'execute'}})
        self.assertEqual(self.rpc.calls,[])
        self.assertEqual(self.rpc.queries[0]['operation_key'],'fixture-original')

    async def test_pending_is_not_success_and_foreign_reply_denied(self):
        self.rpc.value={'request_id':'a'*64,'status':'PENDING_OR_UNKNOWN_QUERY_SAME_KEY'}
        out=await self.call('link_status');self.assertIn('PENDING_OR_UNKNOWN_QUERY_SAME_KEY',out)
        self.rpc.value={'request_id':'a'*64,'result':{'kind':'batch-result'}}
        with self.assertRaises(Exception):await self.call('link_status')

    async def test_changed_principal_refuses_result(self):
        def call(**kw):self.owner='other';return self.rpc.value
        self.rpc.call=call
        with self.assertRaises(Exception):await self.call('link_status')

    async def test_existing_receipt_is_history_only_without_checkpoint_disclosure_or_enqueue(self):
        self.rpc.query_value={'request_id':'a'*64,'result':{'kind':MODE,'checkpoint':{'private':'fixture'},'source_key':'c'*64,'status':'fixture-only'}}
        result=await self.call('link_status')
        self.assertEqual(self.rpc.calls,[])
        self.assertIn('historical_only',result)
        self.assertNotIn('private',result)
