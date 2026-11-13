"""Offline fixtures only: no callback URLs, network, Redis or actual grants."""
import asyncio
import copy
import importlib.util
from pathlib import Path
import sys
import time
import types
import unittest
from unittest.mock import patch

from checkpoint_notice_gate import CheckpointNoticeGate


class InboxError(Exception):
    pass


def load_events():
    inbox = types.SimpleNamespace(InboxError=InboxError, canonical=lambda x: '', identifier=lambda x: x)
    diagnostics = types.SimpleNamespace(emit=lambda *a, **k: None, trace_rpc=lambda *a, **k: None)
    path = Path(__file__).with_name('mcp_events.py')
    if not path.exists():
        path = Path(__file__).resolve().parent.parent / 'mcp_events.py'
    spec = importlib.util.spec_from_file_location('fanout_under_test', path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'inbox': inbox, 'subscription_diagnostics': diagnostics}):
        spec.loader.exec_module(module)
    return module.Events


class FanoutTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.rows = [dict(event_id='new', delivery_id=str(i),
                         event_name='gateway.continuation.ready', state='pending', attempts=0)
                     for i in range(5)]
        self.calls = []
        self.live = 0
        self.maximum = 0
        outer = self

        class Box:
            def for_event(self, name):
                return self

            def delivery_status(self):
                return {'items': copy.deepcopy(outer.rows)}

        self.box = Box()
        events_class = load_events()
        self.events = events_class.__new__(events_class)
        self.events.event_name = 'gateway.continuation.ready'

        async def deliver(inbox, delivery_id, deadline):
            self.calls.append((delivery_id, deadline - time.monotonic()))
            self.live += 1
            self.maximum = max(self.maximum, self.live)
            try:
                await asyncio.sleep(.01)
                return {'delivery_id': delivery_id, 'state': 'delivered'}
            finally:
                self.live -= 1

        self.events.deliver = deliver

    async def test_all_subscribers_start_under_original_ten_subscription_cap(self):
        self.rows = [self.rows[0] | {'delivery_id': str(i)} for i in range(10)]
        result = await self.events.dispatch(self.box, 'new')
        self.assertEqual([r['delivery_id'] for r in result], [str(i) for i in range(10)])
        self.assertEqual(self.maximum, 10)
        self.assertEqual(len(self.calls), 10)
        self.assertTrue(all(9 < budget <= 10 for _, budget in self.calls))

    async def test_slow_first_does_not_block_last_subscribers(self):
        release = asyncio.Event()
        tail_started = asyncio.Event()

        async def deliver(inbox, did, deadline):
            if did == '0':
                await release.wait()
            if did == '4':
                tail_started.set()
            return {'delivery_id': did}

        self.events.deliver = deliver
        task = asyncio.create_task(self.events.dispatch(self.box, 'new'))
        try:
            await asyncio.wait_for(tail_started.wait(), 1)
            self.assertFalse(task.done())
        finally:
            release.set()
            await task

    async def test_old_events_and_delivered_or_terminal_not_replayed(self):
        self.rows.extend([self.rows[0] | {'event_id': 'old', 'delivery_id': 'old'},
                          self.rows[0] | {'state': 'delivered', 'delivery_id': 'done'},
                          self.rows[0] | {'state': 'terminal', 'delivery_id': 'terminal'}])
        await self.events.dispatch(self.box, 'new')
        self.assertEqual({x[0] for x in self.calls}, set('01234'))

    async def test_uncertain_attempts_not_retried(self):
        self.rows[0]['attempts'] = 1
        self.rows[1]['state'] = 'sending'
        result = await self.events.dispatch(self.box, 'new')
        self.assertEqual([r['state'] for r in result[:2]], ['query_only', 'query_only'])
        self.assertEqual({x[0] for x in self.calls}, set('234'))

    async def test_one_delivery_error_does_not_stop_others_or_leak_text(self):
        original = self.events.deliver

        async def deliver(inbox, did, deadline):
            if did == '0':
                raise OSError('PRIVATE_NETWORK_TEXT')
            return await original(inbox, did, deadline)

        self.events.deliver = deliver
        result = await self.events.dispatch(self.box, 'new')
        self.assertEqual(result[0]['error'], 'delivery_unknown_query_only')
        self.assertEqual(len(self.calls), 4)

    async def test_cancellation_joins_workers(self):
        started = asyncio.Event()
        active = set()

        async def deliver(inbox, did, deadline):
            active.add(did)
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(.01)
                active.remove(did)

        self.events.deliver = deliver
        task = asyncio.create_task(self.events.dispatch(self.box, 'new'))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(active, set())

    async def test_original_ten_subscription_cap_and_duplicate_set(self):
        self.rows *= 3
        with self.assertRaises(InboxError):
            await self.events.dispatch(self.box, 'new')
        self.rows = self.rows[:2]
        self.rows[1]['delivery_id'] = self.rows[0]['delivery_id']
        with self.assertRaises(InboxError):
            await self.events.dispatch(self.box, 'new')
        self.assertEqual(self.calls, [])


class LatestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.live = True
        self.event = dict(event_id='event', owner='owner', thread_id='thread', task_id='task',
                          source_key='source', checkpoint_revision='opaque-z',
                          operation_key='operation', checkpoint_digest='digest')
        self.latest = dict(self.event)

        async def read(event_id):
            return {'event': self.event, 'current': self.latest}

        self.gate = CheckpointNoticeGate(read_bound_references=read, current=lambda: self.live)

    async def test_exact_current_handed_back_without_executing(self):
        result = await self.gate.inspect('event')
        self.assertEqual(result['status'], 'CURRENT_CHECKPOINT')
        self.assertFalse(result['execute'])

    async def test_authoritative_changed_revision_supersedes_without_delete_or_ack(self):
        self.latest.update(checkpoint_revision='opaque-a', event_id='new-event')
        result = await self.gate.inspect('event')
        self.assertEqual(result['status'], 'SUPERSEDED_NO_EXECUTION')
        self.assertFalse(result['delete_event'])
        self.assertEqual(result['transport_ack'], 'NOT_RUN')

    async def test_different_tasks_or_owners_not_coalesced(self):
        for key in ['task_id', 'owner', 'thread_id', 'source_key']:
            self.latest = self.event | {key: 'other'}
            self.assertEqual((await self.gate.inspect('event'))['status'], 'DIFFERENT_TASK_OR_SOURCE')

    async def test_same_revision_conflict_is_unknown(self):
        self.latest['checkpoint_digest'] = 'changed'
        self.assertEqual((await self.gate.inspect('event'))['status'], 'SAME_REVISION_CONFLICT_QUERY_ONLY')

    async def test_revocation_after_source_await(self):
        async def read(event_id):
            self.live = False
            return {'event': self.event, 'current': self.latest}

        self.gate.read_bound_references = read
        self.assertEqual((await self.gate.inspect('event'))['status'], 'REVOKED_OR_NOT_BOUND')

    async def test_missing_binding_not_filled_from_payload_or_inbox_order(self):
        self.event.pop('source_key')
        self.assertEqual((await self.gate.inspect('event'))['status'], 'SOURCE_REFERENCE_UNCONFIRMED')


if __name__ == '__main__':
    unittest.main()
