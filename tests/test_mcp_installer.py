import hashlib
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import fakeredis
import fakeredis.aioredis
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from mcp_installer import install_rpc_tools
from transport import canonical,sign_reply


class FakeMCP:
    def __init__(self):self.tools={};self.metadata={}
    def tool(self,**kwargs):
        def decorate(fn):self.tools[fn.__name__]=fn;self.metadata[fn.__name__]=kwargs;return fn
        return decorate


class Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.server=fakeredis.FakeServer();self.mcp=FakeMCP();self.owner='oauth:fixture-owner'
        self.private=Ed25519PrivateKey.generate()
        public=self.private.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex()
        self.env=patch.dict(os.environ,{'XJ_SOURCE_RPC_HOST_PUBLIC_KEY':public});self.env.start();self.addCleanup(self.env.stop)
        self.events=[];self.reply=True
        def mailbox(mid):
            key='original:{'+hashlib.sha256((self.owner+'|'+mid).encode()).hexdigest()+'}'
            box=SimpleNamespace(owner=self.owner,mailbox_id=mid,key=key)
            box.for_event=lambda _:box
            return box
        self.mailbox=mailbox
        async def dispatch(box,event):
            self.events.append(event)
            if self.reply:
                read=await self.mcp.tools['gateway_source_rpc_read']('gateway_internal_v1',event)
                self.assertEqual(read['request'],self.request)
                envelope=sign_reply(self.private,read['context'],{'queueReceipt':{'eventId':'synthetic-finalizer'}})
                await self.mcp.tools['gateway_source_rpc_respond']('gateway_internal_v1',event,canonical(envelope))
        self.formal=SimpleNamespace(dispatch=dispatch)
        self.config=dict(budgets_verified=True,max_wait_seconds=.15,function_budget_seconds=20,connector_budget_seconds=20)
        self.factory=lambda _:fakeredis.aioredis.FakeRedis(server=self.server,decode_responses=True)
        self.install()
        self.request=dict(v=1,id='a'*64,source='synthetic-source',method='read',params={})

    def install(self,config=None):
        return install_rpc_tools(self.mcp,self.mailbox,self.formal,lambda:self.owner,{},
            host_config=config or self.config,connection_factory=self.factory)

    async def request_tool(self,raw=None,mailbox='gateway_internal_v1'):
        return await self.mcp.tools['gateway_source_rpc_request'](mailbox,raw or canonical(self.request))

    def test_tool_annotations_match_effects(self):
        for name in ('gateway_source_rpc_read','gateway_source_rpc_query'):
            a=self.mcp.metadata[name]['annotations']
            self.assertTrue(a.readOnlyHint);self.assertTrue(a.idempotentHint)
        request=self.mcp.metadata['gateway_source_rpc_request']['annotations']
        self.assertFalse(request.readOnlyHint);self.assertFalse(request.idempotentHint)
        self.assertTrue(request.openWorldHint)
        response=self.mcp.metadata['gateway_source_rpc_respond']['annotations']
        self.assertFalse(response.readOnlyHint);self.assertTrue(response.idempotentHint)

    async def test_original_same_instance_roundtrip(self):
        self.assertEqual(len(self.mcp.tools),4)
        result=await self.request_tool()
        self.assertEqual(result['status'],'verified_host_response')
        self.assertEqual(len(self.events),1)
        query=await self.mcp.tools['gateway_source_rpc_query']('gateway_internal_v1',request_id=self.request['id'])
        self.assertEqual(query['result'],result['result'])

    async def test_duplicate_never_dispatches_query_existing(self):
        await self.request_tool()
        await self.request_tool()  # new wait deadline cannot refresh original record
        self.assertEqual(len(self.events),1)
        result=await self.mcp.tools['gateway_source_rpc_query']('gateway_internal_v1',event_id=self.events[0])
        self.assertEqual(result['status'],'verified_host_response')

    async def test_timeout_unknown_query_by_request_id_no_send(self):
        self.reply=False
        result=await self.request_tool()
        self.assertEqual(result['status'],'UNKNOWN_QUERY_ONLY')
        result=await self.mcp.tools['gateway_source_rpc_query']('gateway_internal_v1',request_id=self.request['id'])
        self.assertEqual(result['status'],'UNKNOWN_QUERY_ONLY')
        self.assertEqual(len(self.events),1)

    async def test_other_owner_cannot_read_or_query(self):
        await self.request_tool();event=self.events[0];self.owner='different-owner'
        with self.assertRaises(ValueError):await self.mcp.tools['gateway_source_rpc_read']('gateway_internal_v1',event)
        q=await self.mcp.tools['gateway_source_rpc_query']('gateway_internal_v1',request_id=self.request['id'])
        self.assertEqual(q['status'],'UNKNOWN_QUERY_ONLY')

    async def test_ordinary_work_signature_not_host_proof(self):
        self.reply=False;await self.request_tool()
        # Use retained context directly; wait expiry does not grant signing authority.
        client=self.factory(1);key=self.mailbox('gateway_internal_v1').key
        doc=json.loads(await client.get(key));await client.aclose()
        m=doc['messages'][self.events[0]];q=json.loads(m['message'])
        context=dict(domain='source-finalize-rpc-reply-v1',owner=self.owner,mailbox='gateway_internal_v1',
            operation=m['operation_key'],nonce=q['nonce'],requestSHA256=q['requestSHA256'],deadline=q['deadline'],eventId=m['event_id'])
        forged=sign_reply(Ed25519PrivateKey.generate(),context,{'forged':True})
        # expired event is also denied; cryptographic rejection separately covered
        # in transport tests. Test immediate live signature below uses auto dispatch.
        with self.assertRaises(Exception):await self.mcp.tools['gateway_source_rpc_respond']('gateway_internal_v1',self.events[0],canonical(forged))

    async def test_wrong_mailbox_and_schema_limit(self):
        with self.assertRaises(ValueError):await self.request_tool(mailbox='other')
        with self.assertRaisesRegex(ValueError,'SIZE'):await self.request_tool('x'*65537)
        with self.assertRaises(ValueError):await self.request_tool('{"v":1,"v":1}')
        self.assertEqual(self.events,[])

    def test_absent_key_or_budget_disables_only_new_tools(self):
        for remove in ('key','budget'):
            m=FakeMCP();m.tools['old_tool']='preserved'
            with patch.dict(os.environ,{'XJ_SOURCE_RPC_HOST_PUBLIC_KEY':'' if remove=='key' else os.environ['XJ_SOURCE_RPC_HOST_PUBLIC_KEY']}):
                config=dict(self.config)
                if remove=='budget':config['budgets_verified']=False
                result=install_rpc_tools(m,self.mailbox,self.formal,lambda:self.owner,{},host_config=config,connection_factory=self.factory)
                self.assertFalse(result['enabled']);self.assertEqual(m.tools,{'old_tool':'preserved'})

    async def test_forged_reply_rejected_while_request_live(self):
        async def dispatch(box,event):
            self.events.append(event)
            read=await self.mcp.tools['gateway_source_rpc_read']('gateway_internal_v1',event)
            forged=sign_reply(Ed25519PrivateKey.generate(),read['context'],{'forged':True})
            with self.assertRaises(Exception):await self.mcp.tools['gateway_source_rpc_respond']('gateway_internal_v1',event,canonical(forged))
            good=sign_reply(self.private,read['context'],{'valid':True})
            await self.mcp.tools['gateway_source_rpc_respond']('gateway_internal_v1',event,canonical(good))
        self.formal.dispatch=dispatch
        self.assertEqual((await self.request_tool())['result'],{'valid':True})

if __name__=='__main__':unittest.main()
