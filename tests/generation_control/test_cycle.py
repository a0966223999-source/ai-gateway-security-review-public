import asyncio
import base64
import copy
import json
import unittest

from support import Fixture, accepted_report
from cycle_store import CycleUnknownOutcome
from protocol import canonical, digest, CONTROL_DOMAIN


class CycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_three_rounds_same_key_no_business_mutation(self):
        f=Fixture(self); await f.begin()
        for generation in (0,1):
            f.advance(); arm=await f.observe(generation)
            self.assertEqual(arm['successor']['state'],'armed')
            self.assertEqual(len(f.sent),generation)
            sent=await f.publish(generation+1); self.assertEqual(sent['status'],'delivered')
            await f.begin(generation+1)
        state=f.store.transition('query',f.event)
        self.assertEqual(state['generation'],2)
        document=json.loads(f.db.get(f.inbox.key))
        self.assertEqual(document['messages'],f.original['messages'])
        self.assertEqual(document['deliveries'],f.original['deliveries'])
        self.assertEqual(f.db.dbsize(),1); self.assertEqual(len(f.sent),2)
        self.assertNotEqual(f.sent[0]['body']['eventId'],f.event)
        self.assertEqual(f.sent[0]['body']['data']['logical_event_id'],f.event)

    async def test_eighty_seconds_required_before_observation_and_report_read(self):
        reads=[]
        async def adapter(**kwargs): reads.append(1); return accepted_report('verified_agent_reported_complete')
        f=Fixture(self,report_adapter=adapter);await f.begin();f.advance(79)
        with self.assertRaisesRegex(ValueError,'EIGHTY_SECONDS'):
            await f.observe(native_status='completed')
        self.assertEqual(reads,[]);self.assertEqual(f.sent,[])

    async def test_active_ignores_done_report_and_arms_successor(self):
        async def adapter(**kwargs): raise AssertionError('ACTIVE_MUST_NOT_READ_MESSAGE')
        f=Fixture(self,report_adapter=adapter);await f.begin();f.advance()
        out=await f.observe()
        self.assertEqual(out['successor']['state'],'armed');self.assertFalse(out['task_completed'])

    async def test_native_completed_without_mailbox_adapter_is_not_task_done(self):
        f=Fixture(self);await f.begin();f.advance()
        out=await f.observe(native_status='completed')
        self.assertEqual(out['status'],'safety_stopped');self.assertFalse(out['task_completed'])
        self.assertEqual(f.sent,[])

    async def test_ended_verified_agent_complete_stops_not_business_acceptance(self):
        async def adapter(**kwargs):return accepted_report('verified_agent_reported_complete')
        f=Fixture(self,report_adapter=adapter);await f.begin();f.advance()
        out=await f.observe(native_status='completed')
        self.assertEqual(out['status'],'agent_reported_complete');self.assertTrue(out['agent_reported_complete'])
        self.assertFalse(out['businessAcceptanceVerified']);self.assertFalse(out['parentCloseAllowed'])
        self.assertEqual(f.sent,[])

    async def test_ended_continue_requires_separate_original_receiver_phase(self):
        async def adapter(**kwargs):return accepted_report('verified_continue')
        f=Fixture(self,report_adapter=adapter);await f.begin();f.advance()
        out=await f.observe(native_status='completed')
        self.assertEqual(out['status'],'original_continuation_required');self.assertFalse(out['task_completed'])
        self.assertIsNone(out.get('successor'));self.assertEqual(f.sent,[])

    async def test_failed_report_is_not_completed(self):
        async def adapter(**kwargs):return accepted_report('failed_requires_review')
        f=Fixture(self,report_adapter=adapter);await f.begin();f.advance()
        out=await f.observe(native_status='failed')
        self.assertEqual(out['status'],'failed_requires_review');self.assertFalse(out['task_completed'])

    async def test_unverified_report_fields_rejected(self):
        async def adapter(**kwargs):return {'decision':'verified_agent_reported_complete'}
        f=Fixture(self,report_adapter=adapter);await f.begin();f.advance()
        out=await f.observe(native_status='completed')
        self.assertEqual(out['status'],'safety_stopped');self.assertFalse(out['task_completed'])

    async def test_direct_publish_before_return_needs_no_completion_callback(self):
        f=Fixture(self);await f.begin();f.advance();await f.observe()
        self.assertEqual((await f.publish())['status'],'delivered')
        self.assertEqual(len(f.sent),1)
        self.assertIsNone(f.store.transition('query',f.event).get('completed_round'))

    async def test_repeated_and_concurrent_arm_publish_are_single_flight(self):
        f=Fixture(self);await f.begin();f.advance()
        a,b=await asyncio.gather(f.observe(),f.observe())
        self.assertEqual(a['successor']['notification_id'],b['successor']['notification_id'])
        await asyncio.gather(f.publish(),f.publish())
        self.assertEqual(len(f.sent),1)
        await f.publish();self.assertEqual(len(f.sent),1)

    async def test_publish_cannot_outlive_control_round_budget(self):
        f=Fixture(self);await f.begin();f.advance();await f.observe();f.advance(40)
        out=await f.publish()
        self.assertEqual(out['status'],'safety_stopped')
        self.assertEqual(out['reason'],'ROUND_DEADLINE');self.assertEqual(f.sent,[])

    async def test_query_never_exposes_callback_mutation_lock(self):
        f=Fixture(self);await f.begin();f.advance();await f.observe()
        original=f.db.eval
        def lose_reply(script,keys,key,raw):
            result=original(script,keys,key,raw)
            if json.loads(raw)['action']=='publish_acquire':raise OSError('OFFLINE_AFTER_COMMIT')
            return result
        f.db.eval=lose_reply
        with self.assertRaises(CycleUnknownOutcome):await f.publish()
        f.db.eval=original
        out=f.store.transition('query',f.event)
        self.assertNotIn('lock',out['successor'])
        self.assertEqual(out['successor']['state'],'sending')

    async def test_unknown_callback_never_resends(self):
        f=Fixture(self);await f.begin();f.advance();await f.observe()
        f.sender_unknown=True
        self.assertEqual((await f.publish())['status'],'unknown')
        self.assertEqual((await f.publish())['status'],'UNKNOWN_QUERY_ONLY')
        self.assertEqual(len(f.sent),1)

    async def test_unknown_mutation_queries_same_event_without_repeat_send(self):
        f=Fixture(self);await f.begin();f.advance();await f.observe()
        original=f.db.eval
        def lose_reply(script,keys,key,raw):
            result=original(script,keys,key,raw)
            if json.loads(raw)['action']=='publish_acquire':raise OSError('OFFLINE_AFTER_COMMIT')
            return result
        f.db.eval=lose_reply
        with self.assertRaises(CycleUnknownOutcome):await f.publish()
        f.db.eval=original
        self.assertEqual((await f.publish())['status'],'UNKNOWN_QUERY_ONLY')
        self.assertEqual(f.sent,[])

    async def test_changed_native_turn_is_unknown(self):
        f=Fixture(self);await f.begin();f.advance()
        out=await f.observe(native_turn_id='replacement')
        self.assertEqual(out['status'],'safety_stopped');self.assertFalse(out['task_completed'])

    async def test_stop_revocation_started_and_unknown_effect_fail_closed(self):
        for change in ({'stop':True},{'authority_current':False},{'once_state':'started'},
                       {'once_state':'unknown'},{'zero_effects':False}):
            with self.subTest(change=change):
                f=Fixture(self);await f.begin();f.advance()
                out=await f.observe(**change)
                self.assertEqual(out['status'],'safety_stopped');self.assertFalse(out['task_completed'])
                self.assertEqual(f.sent,[])

    async def test_scope_cannot_extend_and_round_budget_is_real(self):
        f=Fixture(self);await f.begin();initial=f.store.transition('query',f.event)['scope_expires_at']
        f.advance();await f.observe(scope_expires_at=initial+9999)
        self.assertEqual(f.store.transition('query',f.event)['scope_expires_at'],initial)
        f.advance(41)
        out=await f.observe();self.assertEqual(out['status'],'safety_stopped')

    async def test_bad_signature_and_rpc_domain_never_authorize_control(self):
        f=Fixture(self);raw=f.proof('begin');value=json.loads(raw)
        value['body']['domain']='source-finalize-rpc-reply-v1'
        value['signature']=base64.b64encode(f.private.sign(canonical(value['body']).encode())).decode()
        with self.assertRaisesRegex(ValueError,'PROOF_SCOPE'):
            await f.controller.begin(f.inbox,canonical(value))
        value=json.loads(raw);value['body']['evidence']['receiver_run_id']='changed'
        with self.assertRaises(Exception):await f.controller.begin(f.inbox,canonical(value))
        self.assertEqual(f.store.transition('query',f.event)['status'],'CYCLE_MISSING')

    async def test_source_rpc_payload_is_never_a_control_round(self):
        f=Fixture(self);doc=json.loads(f.db.get(f.inbox.key))
        doc['messages'][f.event]['message']=canonical({'kind':'source-finalize-rpc-v1'})
        f.db.set(f.inbox.key,json.dumps(doc),keepttl=True)
        with self.assertRaisesRegex(ValueError,'NOT_ORIGINAL_CONTINUATION'):await f.begin()

    async def test_generation_cost_budget_is_multi_round_but_finite(self):
        f=Fixture(self);f.policy['max_generations']=2;f.controller.policy=dict(f.policy)
        await f.begin()
        for generation in (0,1):
            f.advance();await f.observe(generation)
            await f.publish(generation+1);await f.begin(generation+1)
        f.advance();out=await f.observe(2)
        self.assertEqual(out['status'],'safety_stopped');self.assertEqual(out['reason'],'GENERATION_COST_BUDGET')
        self.assertEqual(len(f.sent),2);self.assertFalse(out['task_completed'])

    async def test_rate_limit_does_not_invent_delayed_wake(self):
        f=Fixture(self);f.policy['min_interval_seconds']=100;f.controller.policy=dict(f.policy)
        await f.begin();f.advance();await f.observe()
        with self.assertRaisesRegex(ValueError,'RATE_LIMIT_NO_AUTOMATIC_WAKE'):await f.publish()
        self.assertEqual(f.sent,[])

    async def test_original_expiry_and_ttl_are_not_refreshed(self):
        f=Fixture(self);before=f.db.ttl(f.inbox.key);original_expiry=f.original['messages'][f.event]['expires_at']
        await f.begin();f.advance();await f.observe();await f.publish()
        self.assertEqual(f.db.ttl(f.inbox.key),before-80)
        self.assertEqual(json.loads(f.db.get(f.inbox.key))['messages'][f.event]['expires_at'],original_expiry)

    async def test_changed_binding_never_reads_a_report(self):
        reads=[]
        async def adapter(**kwargs):reads.append(1);return accepted_report('verified_agent_reported_complete')
        f=Fixture(self,report_adapter=adapter);await f.begin();f.advance()
        changed=copy.deepcopy(f.binding);changed['source_ref']='wrong-source'
        with self.assertRaisesRegex(ValueError,'ORIGINAL_PAYLOAD_BINDING'):
            await f.observe(native_status='completed',binding=changed)
        self.assertEqual(reads,[])

    async def test_old_receiver_cannot_start_parallel_generation(self):
        f=Fixture(self);await f.begin();f.advance();await f.observe();await f.publish()
        with self.assertRaisesRegex(ValueError,'NEW_RECEIVER_RUN_REQUIRED'):
            await f.controller.begin(f.inbox,f.proof('begin',1,'receiver-0'))
        await f.begin(1)
        with self.assertRaisesRegex(ValueError,'NEXT_ROUND_NOT_DELIVERED'):
            await f.controller.begin(f.inbox,f.proof('begin',1,'another-receiver'))


if __name__=='__main__':unittest.main()
