"""Real Lua + fake callback concurrency; signed receipt proofs are synthetic."""
import asyncio
import json
import unittest
from support import Fixture


class DeliveryRaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_next_receiver_before_callback_finish_uses_trusted_receipt_once(self):
        for callback_status in (200,503):
            f=Fixture(self);await f.begin();f.advance();await f.observe()
            original_sender=f.formal.sender
            async def receiver_first(url,headers,body,timeout):
                await original_sender(url,headers,body,timeout)
                new=await f.begin(1)
                self.assertEqual(new['status'],'active');self.assertEqual(new['generation'],1)
                return callback_status,b'{}'
            f.formal.sender=receiver_first
            sent=await f.publish()
            self.assertEqual(sent['status'],'admitted')
            self.assertEqual(sent['delivery_outcome'],'delivered' if callback_status==200 else 'unknown')
            state=f.store.transition('query',f.event)
            self.assertEqual(state['generation'],1);self.assertEqual(state['status'],'active')
            self.assertEqual(state['receiver_run_id'],'receiver-1')
            self.assertEqual(len(f.sent),1)
            with self.assertRaisesRegex(ValueError,'PUBLISH_RECEIVER_RUN_BINDING'):await f.publish()
            self.assertEqual(len(f.sent),1)

    async def test_concurrent_next_receivers_only_one_wins(self):
        f=Fixture(self);await f.begin();f.advance();await f.observe()
        original_sender=f.formal.sender
        async def receiver_first(url,headers,body,timeout):
            await original_sender(url,headers,body,timeout)
            results=await asyncio.gather(f.begin(1),f.controller.begin(f.inbox,
                f.proof('begin',1,'other-receiver')),return_exceptions=True)
            self.assertEqual(sum(isinstance(value,dict) and value['status']=='active' for value in results),1)
            self.assertEqual(sum(isinstance(value,ValueError) for value in results),1)
            return 200,b'{}'
        f.formal.sender=receiver_first
        self.assertEqual((await f.publish())['status'],'admitted')
        self.assertEqual(len(f.sent),1)

    async def test_armed_but_never_sent_cannot_create_next_round(self):
        f=Fixture(self);await f.begin();f.advance();await f.observe()
        with self.assertRaisesRegex(ValueError,'NEXT_ROUND_NOT_DELIVERED'):await f.begin(1)
        self.assertEqual(f.store.transition('query',f.event)['generation'],0)

    async def test_unknown_delivery_is_not_reopened_even_with_new_begin(self):
        f=Fixture(self);await f.begin();f.advance();await f.observe();f.sender_unknown=True
        self.assertEqual((await f.publish())['status'],'unknown')
        with self.assertRaisesRegex(ValueError,'NEXT_ROUND_NOT_DELIVERED'):await f.begin(1)
        self.assertEqual(len(f.sent),1)

    async def test_initial_original_sending_delivery_admits_without_mutating_it(self):
        f=Fixture(self);doc=json.loads(f.db.get(f.inbox.key));did=f.event+':'+f.sid
        doc['deliveries'][did].update(state='sending',lock='OFFLINE_ORIGINAL_LOCK',lock_until=f.time+20)
        f.db.set(f.inbox.key,json.dumps(doc),keepttl=True)
        before=doc['deliveries'][did].copy()
        out=await f.begin();self.assertEqual(out['status'],'active')
        self.assertEqual(json.loads(f.db.get(f.inbox.key))['deliveries'][did],before)
        # Original formal delivery_finish still owns its original lock.
        f.inbox.run('delivery_finish',delivery_id=did,lock='OFFLINE_ORIGINAL_LOCK',
                    state='delivered',attempts=1,status=200)
        self.assertEqual(f.store.transition('query',f.event)['status'],'active')

    async def test_initial_pending_expired_lock_or_absent_delivery_not_invented(self):
        for kind in ('pending','expired','absent'):
            f=Fixture(self);doc=json.loads(f.db.get(f.inbox.key));did=f.event+':'+f.sid
            if kind=='pending':doc['deliveries'][did]['state']='pending'
            elif kind=='expired':doc['deliveries'][did].update(state='sending',lock='OLD',lock_until=f.time-1)
            else:del doc['deliveries'][did]
            f.db.set(f.inbox.key,json.dumps(doc),keepttl=True)
            with self.subTest(kind=kind),self.assertRaisesRegex(ValueError,'ORIGINAL_SUBSCRIPTION_DELIVERY_REQUIRED'):
                await f.begin()
            self.assertEqual(f.store.transition('query',f.event)['status'],'CYCLE_MISSING')

    async def test_late_finish_after_next_round_completion_does_not_reopen(self):
        f=Fixture(self);await f.begin();f.advance();await f.observe()
        original_sender=f.formal.sender
        async def receiver_first(url,headers,body,timeout):
            await original_sender(url,headers,body,timeout)
            await f.begin(1);f.advance()
            done=await f.observe(1,native_status='unknown')
            self.assertEqual(done['status'],'safety_stopped')
            return 200,b'{}'
        f.formal.sender=receiver_first
        out=await f.publish();self.assertEqual(out['status'],'admitted')
        self.assertEqual(f.store.transition('query',f.event)['status'],'safety_stopped')
