import asyncio
import copy
import hashlib
import json
import time
import unittest
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
import fakeredis.aioredis
from transport import SameInboxRPC, ReplyVerifier, UnknownOutcome, sign_reply, canonical, channel


class Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.redis=fakeredis.aioredis.FakeRedis(decode_responses=True)
        self.addAsyncCleanup(self.redis.aclose)
        self.key='fixture:original-mailbox';self.private=Ed25519PrivateKey.generate()
        public=self.private.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
        self.rpc=SameInboxRPC(redis=self.redis,inbox_key=self.key,owner='owner',mailbox='mailbox',
            verifier=ReplyVerifier(public),current=lambda:True,max_wait_seconds=3,
            function_budget_seconds=20,connector_budget_seconds=20)
        self.nonce='a'*64;self.request=dict(v=1,id=self.nonce,source='s',method='read',params={})
        self.sha=hashlib.sha256(canonical(self.request).encode()).hexdigest();self.event='c'*64;self.operation='operation'
        self.deadline=time.time()+2
        self.context=self.rpc.context(self.operation,self.nonce,self.sha,self.deadline,self.event)
        self.sent=0;self.reads=0
        original=self.rpc._read
        async def count(*args):self.reads+=1;return await original(*args)
        self.rpc._read=count

    async def seed(self,req):
        self.sent+=1
        msg=dict(event_id=self.event,operation_key=self.operation,event_name='gateway.continuation.ready',
                 message=canonical(req),rpc_request=canonical(self.request),rpc_request_sha256=self.sha,
                 rpc_nonce=self.nonce,rpc_deadline=self.deadline,rpc_state='reserved_query_only',expires_at=time.time()+100,state='available',version=1)
        await self.redis.set(self.key,canonical(dict(messages={self.event:msg},seq=1,subscriptions={},deliveries={})),ex=100)
        return {'event_id':self.event}

    async def wait(self,send):
        return await self.rpc.wait(operation=self.operation,nonce=self.nonce,request_sha=self.sha,
                                   deadline=self.deadline,send_once=send)

    async def complete(self,result=None):
        return await self.rpc.commit(envelope=sign_reply(self.private,self.context,result or {'queueReceipt':{'eventId':'finalizer-event'}}),context=self.context)

    async def test_immediate_response_before_send_returns_no_lost_wake(self):
        async def send(req):
            receipt=await self.seed(req)
            await self.complete()
            return receipt
        result=await self.wait(send)
        self.assertEqual(result['queueReceipt']['eventId'],'finalizer-event')
        self.assertEqual((self.sent,self.reads),(1,2))

    async def test_delayed_response_reads_only_on_notice(self):
        ready=asyncio.Event()
        async def send(req):
            receipt=await self.seed(req);ready.set();return receipt
        task=asyncio.create_task(self.wait(send))
        await ready.wait();await asyncio.sleep(.03)
        self.assertEqual(self.reads,1)
        await self.complete()
        self.assertIn('queueReceipt',await task)
        self.assertEqual(self.reads,3)

    async def test_timeout_no_resend_response_can_be_queried(self):
        self.deadline=time.time()+.1
        self.context=self.rpc.context(self.operation,self.nonce,self.sha,self.deadline,self.event)
        with self.assertRaises(UnknownOutcome):await self.wait(self.seed)
        self.assertEqual((self.sent,self.reads),(1,1))
        self.assertIsNotNone(await self.redis.get(self.key))

    async def test_response_immutable_and_event_state_preserved(self):
        await self.seed(dict(kind='source-finalize-rpc-v1',nonce=self.nonce,requestSHA256=self.sha,deadline=self.deadline))
        ttl=await self.redis.ttl(self.key)
        self.assertEqual(await self.complete(),'saved')
        self.assertEqual(await self.complete(),'existing')
        with self.assertRaises(Exception):await self.complete({'different':True})
        msg=json.loads(await self.redis.get(self.key))['messages'][self.event]
        self.assertEqual((msg['state'],msg['version']),('available',1))
        self.assertLessEqual(await self.redis.ttl(self.key),ttl)

    async def test_work_key_cannot_forge_host_reply(self):
        bad=sign_reply(Ed25519PrivateKey.generate(),self.context,{'forged':True})
        with self.assertRaises(Exception):await self.rpc.commit(envelope=bad,context=self.context)
        self.assertIsNone(await self.redis.get(self.key))

    async def test_wrong_nonce_sha_event_rejected(self):
        await self.seed(dict(kind='source-finalize-rpc-v1',nonce=self.nonce,requestSHA256=self.sha,deadline=self.deadline))
        for field in ('nonce','requestSHA256','eventId'):
            wrong=copy.deepcopy(self.context);wrong[field]='d'*64
            with self.subTest(field=field),self.assertRaises(Exception):
                await self.rpc.commit(envelope=sign_reply(self.private,wrong,{}),context=wrong)

    async def test_forged_pubsub_notice_is_not_reply(self):
        ready=asyncio.Event()
        async def send(req):
            r=await self.seed(req);ready.set();return r
        task=asyncio.create_task(self.wait(send))
        await ready.wait();await asyncio.sleep(.02)
        await self.redis.publish(channel(self.key,self.operation,self.nonce),self.event)
        await asyncio.sleep(.02)
        self.assertFalse(task.done())
        await self.complete();await task

    async def test_commit_readback_unknown_retains_saved_reply(self):
        await self.seed(dict(kind='source-finalize-rpc-v1',nonce=self.nonce,requestSHA256=self.sha,deadline=self.deadline))
        async def failed(*args):raise ConnectionError('readback failed')
        self.rpc._read=failed
        with self.assertRaisesRegex(UnknownOutcome,'COMMIT_UNKNOWN'):await self.complete()
        message=json.loads(await self.redis.get(self.key))['messages'][self.event]
        self.assertIn('rpc_response',message)

    async def test_commit_revoked_afterawait_is_unknown(self):
        await self.seed(dict(kind='source-finalize-rpc-v1',nonce=self.nonce,requestSHA256=self.sha,deadline=self.deadline))
        from transport import COMMIT
        original=self.redis.eval
        async def revoke(script,*args):
            result=await original(script,*args)
            if script==COMMIT:self.rpc.current=lambda:False
            return result
        self.redis.eval=revoke
        with self.assertRaisesRegex(UnknownOutcome,'COMMIT_UNKNOWN'):await self.complete()
        self.assertIn('rpc_response',json.loads(await self.redis.get(self.key))['messages'][self.event])

    async def test_cancellation_cleanup_error_does_not_mask_unknown(self):
        original=self.redis.pubsub
        def bad_cleanup():
            ps=original()
            close=ps.aclose
            async def failing_close():
                await close();raise OSError('cleanup failure')
            ps.aclose=failing_close
            return ps
        self.redis.pubsub=bad_cleanup
        ready=asyncio.Event()
        async def send(req):
            result=await self.seed(req);ready.set();return result
        task=asyncio.create_task(self.wait(send));await ready.wait()
        task.cancel()
        with self.assertRaises(UnknownOutcome):await task
        self.assertEqual(self.sent,1)

    async def test_integrated_full_request_roundtrip(self):
        from request_store import submit_and_wait,RequestStore
        store=RequestStore(self.redis,self.key,lambda:True)
        async def dispatch(event):
            self.assertEqual(await store.read(event),self.request)
            operation=hashlib.sha256((self.key+'|rpc|'+self.request['id']).encode()).hexdigest()
            context=self.rpc.context(operation,self.nonce,self.sha,self.deadline,event)
            await self.rpc.commit(envelope=sign_reply(self.private,context,{'saved':True}),context=context)
        result=await submit_and_wait(self.rpc,self.request,deadline=self.deadline,dispatch=dispatch)
        self.assertEqual(result,{'saved':True})
        self.assertEqual(len(json.loads(await self.redis.get(self.key))['messages']),1)

    def test_utf8_signed_reply_matches_original_canonical(self):
        golden=json.loads((Path(__file__).parent/'fixtures/utf8-golden.json').read_text())['vectors']['reply']
        context={k:v for k,v in golden['value'].items() if k!='result'}
        result=golden['value']['result']
        envelope=sign_reply(self.private,context,result)
        self.assertEqual(canonical(envelope['body']).encode().hex(),golden['canonical_utf8_hex'])
        self.assertEqual(hashlib.sha256(canonical(envelope['body']).encode()).hexdigest(),golden['sha256'])
        self.assertEqual(self.rpc.verifier.verify(envelope,context),result)

    def test_unknown_duration_budget_fails_closed(self):
        with self.assertRaises(ValueError):
            SameInboxRPC(redis=self.redis,inbox_key=self.key,owner='o',mailbox='m',
                verifier=self.rpc.verifier,current=lambda:True,max_wait_seconds=20,
                function_budget_seconds=20,connector_budget_seconds=20)

if __name__=='__main__':unittest.main()
