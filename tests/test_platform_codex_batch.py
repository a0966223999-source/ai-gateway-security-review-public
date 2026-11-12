"""Coordinator isolation checks; synthetic executor is not live dispatch proof."""
import asyncio
import tempfile
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

from platform_codex_batch import ExecuteBatch, execute_batch, child_key
from fresh_batch_host import FreshBatchHost, initialize, HostDenied


class SchemaTests(unittest.TestCase):
    def request(self, **changes):
        return dict(parent_id='p', operation_key='op', execution_mode='codex_batch_once',
                    batch_id='b', item_ids=['one', 'two'], **changes)

    def test_rejects_commands_authority_new_prompts_and_other_profiles(self):
        for key in ('prompt', 'items', 'owner', 'executable', 'path', 'profile', 'retry'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                ExecuteBatch(**self.request(**{key: 'injected'}))

    def test_rejects_empty_duplicate_oversize_and_non_strict_timeouts(self):
        for field, values in [('item_ids', [[], ['one', 'one'], ['1', '2', '3', '4']]),
                              ('timeout_seconds', [True, '30', 9, 91])]:
            for value in values:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    data = self.request()
                    data[field] = value
                    ExecuteBatch(**data)

    def test_child_keys_are_stable_scoped_and_bounded(self):
        request = ExecuteBatch(**self.request())
        self.assertEqual(child_key(request, 'one'), child_key(request, 'one'))
        self.assertNotEqual(child_key(request, 'one'), child_key(request, 'two'))
        self.assertLess(len(child_key(request, 'one')), 128)


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        cfg = initialize(self.root / 'host', owner='oauth:' + 'a' * 64,
                         platform_service=True, enable_codex=True)
        self.host = FreshBatchHost(self.root / 'host')
        self.request = ExecuteBatch(parent_id=cfg['parent_id'], operation_key='batch-op',
            execution_mode='codex_batch_once', batch_id='batch', item_ids=['one', 'two', 'three'])
        self.repo = object()
        self.validation = patch('platform_codex_batch.validate_pending', new_callable=AsyncMock)
        self.validate = self.validation.start()
        self.execution = patch('platform_codex_batch.execute_item', new_callable=AsyncMock)
        self.execute = self.execution.start()
        self.execute.return_value = dict(original_result_saved=True, observed_execution_verified=True)

    async def asyncTearDown(self):
        self.execution.stop()
        self.validation.stop()
        self.temp.cleanup()

    async def test_all_children_run_sequentially_and_audit_is_saved(self):
        seen = []
        async def run(host, repo, request):
            seen.append(request.item_id)
            await asyncio.sleep(0)
            self.assertEqual(self.execute.await_count, len(seen))
            return dict(original_result_saved=True, observed_execution_verified=True)
        self.execute.side_effect = run
        result = await execute_batch(self.host, self.repo, self.request)
        self.assertEqual(seen, self.request.item_ids)
        self.assertEqual(result['status'], 'CODEX_BATCH_FINISHED')
        self.assertEqual(result['unstarted_item_ids'], [])
        self.assertEqual(self.validate.await_count, 4)
        self.assertEqual(len(list(self.host.root.glob('codex-batch-run-*/receipt.json'))), 1)
        self.assertFalse(result['automatic_dispatch'])
        self.assertFalse(result['native_work_dispatch'])

    async def test_preflight_rejection_starts_nothing(self):
        self.validate.side_effect = HostDenied('CODEX_BATCH_NOT_BOUND')
        with self.assertRaises(HostDenied):
            await execute_batch(self.host, self.repo, self.request)
        self.execute.assert_not_awaited()

    async def test_unknown_first_item_never_launches_remaining_or_retries(self):
        self.execute.side_effect = HostDenied('CODEX_TIMEOUT_QUERY_ONLY')
        result = await execute_batch(self.host, self.repo, self.request)
        self.assertEqual(result['status'], 'CODEX_BATCH_HALTED_QUERY_ONLY')
        self.assertEqual(result['unstarted_item_ids'], ['two', 'three'])
        self.assertEqual(self.execute.await_count, 1)
        self.assertFalse(result['retry_performed'])

    async def test_stopped_child_halts_remaining_items(self):
        self.execute.return_value = dict(status='STOPPED', original_terminal_state_saved=True)
        result = await execute_batch(self.host, self.repo, self.request)
        self.assertEqual(result['unstarted_item_ids'], ['two', 'three'])
        self.assertEqual(self.execute.await_count, 1)

    async def test_cancellation_before_next_item_is_rechecked(self):
        self.validate.side_effect = [None, None, HostDenied('PENDING_REQUIRED')]
        result = await execute_batch(self.host, self.repo, self.request)
        self.assertEqual(self.execute.await_count, 1)
        self.assertEqual(result['reason'], 'PENDING_REQUIRED')

    async def test_revoked_host_starts_nothing(self):
        (self.host.root / 'STOP').touch()
        with self.assertRaises(HostDenied):
            await execute_batch(self.host, self.repo, self.request)
        self.execute.assert_not_awaited()

    async def test_concurrent_coordinator_does_not_duplicate_dispatch(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def run(*args):
            started.set()
            await release.wait()
            return dict(status='STOPPED')
        self.execute.side_effect = run
        first = asyncio.create_task(execute_batch(self.host, self.repo, self.request))
        try:
            await started.wait()
            with self.assertRaisesRegex(HostDenied, 'COORDINATOR_BUSY'):
                await execute_batch(self.host, self.repo, self.request)
            self.assertEqual(self.execute.await_count, 1)
        finally:
            release.set()
            await first

    async def test_worker_cancellation_does_not_start_next_item(self):
        started = asyncio.Event()
        async def run(*args):
            started.set()
            await asyncio.Event().wait()
        self.execute.side_effect = run
        task = asyncio.create_task(execute_batch(self.host, self.repo, self.request))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.execute.await_count, 1)


if __name__ == '__main__':
    unittest.main()
