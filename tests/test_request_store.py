import copy
import hashlib
import json
import time
import unittest
from pathlib import Path
import fakeredis.aioredis
from request_store import RequestStore, SCRIPT
from transport import canonical, UnknownOutcome

ORIGINAL_PATH=Path(__file__).parent/'fixtures/inbox-9b800a.lua'
assert hashlib.sha256(ORIGINAL_PATH.read_bytes()).hexdigest()=='cd975b23255d9cc51f21d0c2f5dba1b4c0efe0b90d879a4a46a120f801f81707'
ORIGINAL=ORIGINAL_PATH.read_text()

class Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.redis=fakeredis.aioredis.FakeRedis(decode_responses=True)
        self.addAsyncCleanup(self.redis.aclose)
        self.live=True;self.key='original-key';self.dispatches=[]
        self.store=RequestStore(self.redis,self.key,lambda:self.live)
        self.request=dict(v=1,id='a'*64,source='s',method='save',params={'revision':'old','document':{'checkpoint':'synthetic'}})
        raw,sha=self.store.exact(self.request)
        self.locator=dict(kind='source-finalize-rpc-v1',nonce=self.request['id'],requestSHA256=sha,deadline=time.time()+30)

    async def dispatch(self,event):self.dispatches.append(event)
    async def submit(self):return await self.store.submit(self.request,self.locator,dispatch=self.dispatch)

    async def test_full_request_atomic_immutable_and_no_new_key(self):
        receipt=await self.submit()
        self.assertEqual(await self.store.read(receipt['event_id']),self.request)
        doc=json.loads(await self.redis.get(self.key));m=doc['messages'][receipt['event_id']]
        self.assertEqual(m['rpc_request'],canonical(self.request))
        self.assertEqual(m['rpc_state'],'reserved_query_only')
        self.assertEqual(await self.redis.keys('*'),[self.key])
        second=await self.submit()
        self.assertTrue(second['duplicate']);self.assertEqual(len(self.dispatches),1)

    async def test_changed_body_same_id_rejected(self):
        await self.submit();before=await self.redis.get(self.key)
        self.request['params']['document']={'changed':True}
        self.locator['requestSHA256']=self.store.exact(self.request)[1]
        with self.assertRaisesRegex(ValueError,'rpc_operation_conflict'):await self.submit()
        self.assertEqual(await self.redis.get(self.key),before)

    async def test_wrong_exact_sha_rejected_without_write(self):
        self.locator['requestSHA256']='0'*64
        with self.assertRaisesRegex(ValueError,'LOCATOR'):await self.submit()
        self.assertIsNone(await self.redis.get(self.key))

    async def test_tampered_original_request_read_denied(self):
        r=await self.submit();doc=json.loads(await self.redis.get(self.key))
        doc['messages'][r['event_id']]['rpc_request']='{}'
        await self.redis.set(self.key,canonical(doc))
        with self.assertRaises(ValueError):await self.store.read(r['event_id'])

    async def test_unknown_save_retry_never_redispatch(self):
        original=self.redis.eval
        async def uncertain(*args):
            await original(*args);raise ConnectionError('committed but reply lost')
        self.redis.eval=uncertain
        with self.assertRaises(UnknownOutcome):await self.submit()
        self.redis.eval=original
        result=await self.submit()
        self.assertTrue(result['duplicate']);self.assertEqual(self.dispatches,[])

    async def test_revoked_after_atomic_write_no_dispatch(self):
        original=self.redis.eval
        async def revoke(*args):
            result=await original(*args);self.live=False;return result
        self.redis.eval=revoke
        with self.assertRaisesRegex(ValueError,'AUTHORITY'):await self.submit()
        self.assertEqual(self.dispatches,[])
        self.assertIsNotNone(await self.redis.get(self.key))

    async def test_revoked_read_afterawait_denied(self):
        r=await self.submit();original=self.redis.eval
        async def revoke(*args):
            result=await original(*args);self.live=False;return result
        self.redis.eval=revoke
        with self.assertRaisesRegex(ValueError,'AUTHORITY'):await self.store.read(r['event_id'])

    async def test_chinese_checkpoint_matches_original_rpc_digest(self):
        golden=json.loads((Path(__file__).parent/'fixtures/utf8-golden.json').read_text())['vectors']['request']
        self.request['params']['document']={'checkpoint':{'nextAction':'核對七步續點：原任務、來源、本人回件。'}}
        exact,sha=self.store.exact(self.request)
        self.assertIn('七步續點',exact)
        self.assertEqual(self.request,golden['value'])
        self.assertEqual(exact.encode().hex(),golden['canonical_utf8_hex'])
        self.assertEqual(sha,golden['sha256'])
        self.locator['requestSHA256']=sha
        receipt=await self.submit()
        restored=await self.store.read(receipt['event_id'])
        self.assertEqual(hashlib.sha256(canonical(restored).encode()).hexdigest(),golden['sha256'])
        self.assertEqual(restored,self.request)

    async def test_live_subscription_contracts_differential(self):
        other=fakeredis.aioredis.FakeRedis(decode_responses=True);self.addAsyncCleanup(other.aclose)
        sid='sub_'+'d'*64
        subscription=dict(id=sid,event_name='gateway.continuation.ready',expires_at=int(time.time())+500,
                          access_until=int(time.time())+500,revision='fixture-revision')
        operations=[('subscription_get',dict(id=sid)),
                    ('subscription_status',dict(event_name='gateway.continuation.ready')),
                    ('subscribe',dict(subscription=subscription,expected_revision='')),
                    ('subscription_get',dict(id=sid)),
                    ('subscription_status',dict(event_name='gateway.continuation.ready')),
                    ('subscription_status',dict(event_name='inbox.message.created')),
                    ('unsubscribe',dict(id=sid)),
                    ('subscription_get',dict(id=sid))]
        results=[]
        for action,req in operations:
            req=dict(req,epoch='fixture-epoch')
            with self.subTest(action=action):
                a=json.loads(await self.redis.eval(SCRIPT,1,self.key,action,canonical(req)))
                b=json.loads(await other.eval(ORIGINAL,1,self.key,action,canonical(req)))
                self.assertEqual(a,b);results.append(a)
                self.assertEqual(await self.redis.get(self.key),await other.get(self.key))
        self.assertEqual(results[0],{'found':False})
        self.assertEqual(results[3],{'found':True,'subscription':subscription})
        self.assertEqual(results[4]['count'],1)
        self.assertEqual(results[-1],{'found':False})

    async def test_original_send_actions_differential(self):
        other=fakeredis.aioredis.FakeRedis(decode_responses=True);self.addAsyncCleanup(other.aclose)
        args=dict(event_name='gateway.continuation.ready',operation_key='ordinary',event_id='e'*64,
            message='original message',digest='digest',epoch='epoch')
        operations=[('send',args),('send',args),('send',dict(args,digest='conflict')),
            ('list',dict(event_name='gateway.continuation.ready',cursor_epoch='',after=0,limit=20,epoch='epoch')),
            ('claim',dict(event_name='gateway.continuation.ready',event_id='e'*64,lease_digest='lease',epoch='epoch')),
            ('ack',dict(event_name='gateway.continuation.ready',event_id='e'*64,lease_digest='lease',version=1,epoch='epoch'))]
        for action,req in operations:
            with self.subTest(action=action):
                a=json.loads(await self.redis.eval(SCRIPT,1,self.key,action,canonical(req)))
                b=json.loads(await other.eval(ORIGINAL,1,self.key,action,canonical(req)))
                self.assertEqual(a,b)
                self.assertEqual(json.loads(await self.redis.get(self.key)),json.loads(await other.get(self.key)))

if __name__=='__main__':unittest.main()
