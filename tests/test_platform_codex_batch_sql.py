"""Original temporary SQL + owned OS processes; fixture CLI is NOT live Codex."""
import asyncio
import hmac
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

from fresh_batch_host import HostDenied, canonical, digest
import test_platform_pending_cancel as fixture


@unittest.skipUnless(os.environ.get('XJ_DEER_BACKEND'), 'original backend required')
class OriginalBatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.PendingCancelTests.asyncSetUp(self)

    asyncTearDown = fixture.PendingCancelTests.asyncTearDown

    def envelope(self, key='batch-run', **changes):
        payload = dict(execution_mode='codex_batch_once', batch_id=self.batch_id,
                       item_ids=[item.id for item in self.items], timeout_seconds=30)
        payload.update(changes)
        body = dict(generation=self.cfg['generation'], owner=self.cfg['owner'],
            parent_id=self.cfg['parent_id'], action='execute', operation_key=key,
            payload=payload, expires_at=time.time() + 60)
        body['request_id'] = digest([self.cfg['generation'], self.cfg['owner'], 'execute', key])
        return dict(body=body, mac=hmac.new(self.host.ingress, canonical(body), 'sha256').hexdigest())

    def cli(self, slow=False):
        cli = self.root / 'synthetic-cli'
        cli.write_text('#!' + sys.executable + '\nimport json,sys,time\n'
            'sys.stdin.read()\n'
            f'open({str(self.root / "started")!r}, "w").write("started")\n'
            f'time.sleep({30 if slow else 0})\n'
            'print(json.dumps({"type":"thread.started","thread_id":"synthetic-child"}))\n'
            'print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"synthetic result"}}))\n'
            'print(json.dumps({"type":"turn.completed"}))\n')
        cli.chmod(0o700)
        return cli

    async def states(self):
        async with self.sf() as session:
            return [(row.status, row.attempt, row.result) for item in self.items
                    if (row := await session.get(self.Item, item.id))]

    async def test_signed_batch_saves_original_sql_and_replay_never_reruns(self):
        envelope = self.envelope()
        with patch('platform_codex_executor.shutil.which', return_value=str(self.cli())):
            reply = await self.host.execute(envelope, repository=self.repo)
            self.assertEqual(reply['body']['result']['status'], 'CODEX_BATCH_FINISHED')
            self.assertEqual(await self.states(), [('succeeded', 1, 'synthetic result')] * 3)
            import base64
            self.host.key.public_key().verify(base64.b64decode(reply['signature']), canonical(reply['body']))
            with self.assertRaisesRegex(HostDenied, 'ATTEMPTED_QUERY_ONLY'):
                await self.host.execute(envelope, repository=self.repo)
            with self.assertRaisesRegex(HostDenied, 'UNTOUCHED_PENDING'):
                await self.host.execute(self.envelope('changed-key'), repository=self.repo)
            self.assertEqual(await self.states(), [('succeeded', 1, 'synthetic result')] * 3)

    async def test_foreign_item_preflight_has_zero_process_and_zero_attempt(self):
        with patch('platform_codex_executor.subprocess.Popen') as spawn:
            with self.assertRaisesRegex(HostDenied, 'UNTOUCHED_PENDING'):
                await self.host.execute(self.envelope(item_ids=[self.items[0].id, 'foreign']),
                                        repository=self.repo)
            spawn.assert_not_called()
            self.assertEqual(await self.states(), [('pending', 0, None)] * 3)

    async def test_stop_current_child_keeps_remaining_unstarted(self):
        from platform_codex_stop import Stop, stop_item
        with patch('platform_codex_executor.shutil.which', return_value=str(self.cli(slow=True))):
            task = asyncio.create_task(self.host.execute(self.envelope(), repository=self.repo))
            try:
                for _ in range(100):
                    if (self.root / 'started').exists():
                        break
                    await asyncio.sleep(.05)
                else:
                    self.fail('owned fixture process did not start')
                result = await stop_item(self.host, self.repo, Stop(parent_id=self.cfg['parent_id'],
                    operation_key='stop-first', execution_mode='stop_codex_once',
                    batch_id=self.batch_id, item_id=self.items[0].id))
                self.assertEqual(result['status'], 'STOPPED')
                outcome = (await task)['body']['result']
                self.assertEqual(outcome['status'], 'CODEX_BATCH_HALTED_QUERY_ONLY')
                self.assertEqual(outcome['unstarted_item_ids'], [item.id for item in self.items[1:]])
                self.assertEqual(await self.states(), [('cancelled', 1, None), ('pending', 0, None), ('pending', 0, None)])
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)


if __name__ == '__main__':
    unittest.main()

