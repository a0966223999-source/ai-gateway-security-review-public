"""Public-service tests use synthetic host signatures, never real native Work."""
import copy
import unittest

from support import Fixture, accepted_report
from protocol import digest


def watched():
    return {'mode':'resumed','thread_id':'producer-thread','turn_id':'resumed-turn',
            'platform_receipt_sha256':digest({'threadId':'producer-thread',
                'turnId':'resumed-turn','admissionOutcome':'started'})}


def evidence(**change):
    out={'once_state':'started','watch':watched(),'native_thread_id':'producer-thread',
         'native_turn_id':'resumed-turn'}
    out.update(change)
    return out


class StartedWatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_started_watch_active_cycles_without_work_reexecution(self):
        reads=[]
        async def adapter(**kw):reads.append(1);return accepted_report('verified_agent_reported_complete')
        f=Fixture(self,report_adapter=adapter)
        await f.controller.begin(f.inbox,f.proof('begin',**evidence()))
        for generation in (0,1):
            f.advance()
            out=await f.observe(generation,**evidence())
            self.assertEqual(out['phase'],'post_admission_read_only')
            self.assertFalse(out['task_completed'])
            await f.controller.publish(f.inbox,f.proof('publish',generation+1,
                f'receiver-{generation}',**evidence()))
            await f.controller.begin(f.inbox,f.proof('begin',generation+1,
                f'receiver-{generation+1}',**evidence()))
        self.assertEqual(reads,[])
        self.assertEqual(len(f.sent),2)
        self.assertEqual(f.store.original(f.event),f.original['messages'][f.event])

    async def test_ended_resumed_reads_original_result_adapter_not_started_boolean(self):
        observations=[]
        async def adapter(**kw):
            observations.append(kw['observation'])
            self.assertEqual(kw['binding']['producer_turn_id'],'producer-turn')
            return accepted_report('verified_agent_reported_complete')
        f=Fixture(self,report_adapter=adapter)
        await f.controller.begin(f.inbox,f.proof('begin',**evidence()))
        f.advance();out=await f.observe(**evidence(native_status='completed'))
        self.assertEqual(out['status'],'agent_reported_complete')
        self.assertEqual(observations[0]['watch']['turn_id'],'resumed-turn')
        self.assertFalse(out['businessAcceptanceVerified'])
        self.assertFalse(out['parentCloseAllowed']);self.assertEqual(f.sent,[])

    async def test_missing_started_receipt_hash_and_unverified_once_refused(self):
        for change in ({'once_state':'unknown'}, {'once_state':'fresh'},
                       {'watch':dict(watched(),platform_receipt_sha256=None)},
                       {'watch':dict(watched(),turn_id='producer-turn')},
                       {'watch':dict(watched(),thread_id='other-thread')}):
            f=Fixture(self)
            with self.subTest(change=change),self.assertRaises(ValueError):
                await f.controller.begin(f.inbox,f.proof('begin',**evidence(**change)))
            self.assertEqual(f.store.transition('query',f.event)['status'],'CYCLE_MISSING')

    async def test_watched_receipt_or_turn_cannot_change_after_begin(self):
        for watch in (dict(watched(),turn_id='another-turn'),
                      dict(watched(),platform_receipt_sha256='f'*64)):
            f=Fixture(self);await f.controller.begin(f.inbox,f.proof('begin',**evidence()))
            f.advance();out=await f.observe(**evidence(watch=watch,native_turn_id=watch['turn_id']))
            self.assertEqual(out['status'],'safety_stopped');self.assertFalse(out['task_completed'])

    async def test_receipt_watch_cannot_roll_back_to_original_producer(self):
        f=Fixture(self);await f.controller.begin(f.inbox,f.proof('begin',**evidence()))
        f.advance();out=await f.observe()
        self.assertEqual(out['reason'],'WATCH_CHANGED');self.assertEqual(f.sent,[])

    async def test_once_unknown_does_not_reopen_legally_started_work(self):
        f=Fixture(self);await f.controller.begin(f.inbox,f.proof('begin',**evidence()))
        f.advance();out=await f.observe(**evidence(once_state='unknown'))
        self.assertEqual(out['status'],'safety_stopped');self.assertFalse(out['task_completed'])
        again=await f.controller.begin(f.inbox,f.proof('begin',**evidence()))
        self.assertEqual(again['status'],'safety_stopped')

    async def test_verified_continuation_admission_opens_only_one_result_phase(self):
        async def adapter(**kw):return accepted_report('verified_continue')
        f=Fixture(self,report_adapter=adapter);await f.begin();f.advance()
        out=await f.observe(native_status='completed')
        self.assertEqual(out['status'],'original_continuation_required')
        initial_expiry=out['scope_expires_at']; f.advance(10)
        out=await f.controller.begin(f.inbox,f.proof('begin',**evidence()))
        self.assertEqual(out['phase'],'post_admission_read_only')
        self.assertEqual(out['generation'],0)
        self.assertEqual(out['scope_expires_at'],initial_expiry)
        start=out['started_at'];f.advance(4)
        again=await f.controller.begin(f.inbox,f.proof('begin',**evidence()))
        self.assertEqual(again['started_at'],start)
        with self.assertRaisesRegex(ValueError,'EIGHTY_SECONDS'):
            await f.observe(**evidence())

    async def test_safety_stop_never_reopens_as_result_watch(self):
        f=Fixture(self);await f.begin();f.advance();await f.observe(native_status='unknown')
        out=await f.controller.begin(f.inbox,f.proof('begin',**evidence()))
        self.assertEqual(out['status'],'safety_stopped');self.assertFalse(out['task_completed'])

    async def test_wrong_native_turn_in_started_watch_is_unknown(self):
        f=Fixture(self);await f.controller.begin(f.inbox,f.proof('begin',**evidence()))
        f.advance();out=await f.observe(**evidence(native_turn_id='producer-turn'))
        self.assertEqual(out['reason'],'NATIVE_STATE_UNKNOWN');self.assertEqual(f.sent,[])

    async def test_publish_cannot_use_a_new_receipt(self):
        f=Fixture(self);await f.controller.begin(f.inbox,f.proof('begin',**evidence()))
        f.advance();await f.observe(**evidence())
        out=await f.controller.publish(f.inbox,f.proof('publish',1,**evidence(
            watch=dict(watched(),platform_receipt_sha256='f'*64))))
        self.assertEqual(out['reason'],'WATCH_CHANGED');self.assertEqual(f.sent,[])
