"""Original SQL and real owned OS process groups; CLI/authority explicitly synthetic."""
import asyncio
import hmac
import json
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

from fresh_batch_host import HostDenied, canonical, digest
from platform_codex_stop import Stop, live_group_members
import test_platform_pending_cancel as pending_fixture


class StopSchemaTests(unittest.TestCase):
    def test_pid_command_or_path_cannot_be_supplied(self):
        base = dict(parent_id='parent',operation_key='op',execution_mode='stop_codex_once',
                    batch_id='batch',item_id='item')
        self.assertEqual(Stop(**base).item_id,'item')
        for name in ('pid','command','path','owner','intent_id'):
            with self.assertRaises(ValueError):
                Stop(**base,**{name:'caller-supplied'})


@unittest.skipUnless(os.environ.get('XJ_DEER_BACKEND'),'original backend not configured')
class OriginalStopTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture_items = [{'key':'target','prompt':'synthetic-stop-target'+'.'*47000},
                              {'key':'sibling','prompt':'synthetic-sibling'},
                              {'key':'pending','prompt':'never-launch'}]
        await pending_fixture.PendingCancelTests.asyncSetUp(self)

    asyncTearDown = pending_fixture.PendingCancelTests.asyncTearDown

    def envelope(self, key, item, mode='codex_once'):
        payload = dict(batch_id=self.batch_id,item_id=item,execution_mode=mode)
        if mode == 'codex_once':
            payload['timeout_seconds'] = 30
        body = dict(generation=self.cfg['generation'],owner=self.cfg['owner'],
            parent_id=self.cfg['parent_id'],action='execute',operation_key=key,
            payload=payload,expires_at=time.time()+60)
        body['request_id'] = digest([self.cfg['generation'],self.cfg['owner'],'execute',key])
        return {'body':body,'mac':hmac.new(self.host.ingress,canonical(body),'sha256').hexdigest()}

    def cli(self):
        stub = self.root/'synthetic-cli'
        child_code = ('import os,signal,time;from pathlib import Path;'
            'signal.signal(signal.SIGTERM,signal.SIG_IGN);'
            f'Path({str(self.root/"child-ready")!r}).write_text(str(os.getpid()));time.sleep(60)')
        stub.write_text('#!'+sys.executable+'\nimport json,os,sys,time,subprocess,signal\nfrom pathlib import Path\n'
            'signal.signal(signal.SIGTERM,signal.SIG_IGN)\n'
            'p=sys.stdin.read()\n'
            f'root=Path({str(self.root)!r})\n'
            'target="stop-target" in p\n'
            '(root/("target-started" if target else "sibling-started")).write_text(str(os.getpid()))\n'
            'print(json.dumps({"type":"thread.started","thread_id":"fixture-target" if target else "fixture-sibling"}),flush=True)\n'
            f'if target: subprocess.Popen([sys.executable,"-c",{child_code!r}],start_new_session=True);time.sleep(60)\n'
            'else:\n time.sleep(6)\n print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"synthetic sibling completed"}}),flush=True)\n print(json.dumps({"type":"turn.completed"}),flush=True)\n')
        stub.chmod(0o700)
        return stub

    async def wait_for_start(self, sibling=False):
        for _ in range(200):
            if (self.root/'child-ready').exists() and (not sibling or (self.root/'sibling-started').exists()):
                return
            await asyncio.sleep(.05)
        self.fail('owned synthetic child did not start')

    async def test_stop_one_group_with_term_ignoring_child_preserves_sibling_and_once_fence(self):
        env = self.envelope('stop-target',self.items[0].id,'stop_codex_once')
        with patch('platform_codex_executor.shutil.which',return_value=str(self.cli())):
            first = asyncio.create_task(self.host.execute(self.envelope('run-target',self.items[0].id),repository=self.repo))
            second = asyncio.create_task(self.host.execute(self.envelope('run-sibling',self.items[1].id),repository=self.repo))
            try:
                await self.wait_for_start(sibling=True)
                target_pid = int((self.root/'target-started').read_text())
                detached_pid = int((self.root/'child-ready').read_text())
                stopped = (await self.host.execute(env,repository=self.repo))['body']['result']
                self.assertEqual(stopped['status'],'STOPPED')
                self.assertEqual(stopped['signals_sent'],['SIGTERM','SIGKILL'])
                self.assertTrue(stopped['original_terminal_state_saved'])
                self.assertEqual(live_group_members(target_pid),[])
                self.assertEqual(live_group_members(detached_pid),[])
                self.assertEqual((await first)['body']['result'],stopped)
                with self.assertRaisesRegex(HostDenied,'ATTEMPTED_QUERY_ONLY'):
                    await self.host.execute(env,repository=self.repo)
                sibling = (await second)['body']['result']
                self.assertTrue(sibling['original_result_saved'])
                async with self.sf() as session:
                    rows = [await session.get(self.Item,item.id) for item in self.items]
                    self.assertEqual([(r.status,r.attempt) for r in rows],
                                     [('cancelled',1),('succeeded',1),('pending',0)])
                    self.assertEqual(rows[0].stop_reason,'stopped_by_request')
                    self.assertIsNone(rows[0].result)
                    self.assertEqual(rows[1].result,'synthetic sibling completed')
                    batch = await session.get(self.Batch,self.batch_id)
                    self.assertEqual(batch.execution_spec['native_control']['binding'],self.binding)
                    self.assertEqual(batch.execution_spec['native_control']['items'][rows[0].id]['phase'],'stopped')
                no_op = (await self.host.execute(self.envelope('stop-terminal',self.items[0].id,'stop_codex_once'),repository=self.repo))['body']['result']
                self.assertEqual(no_op['status'],'ALREADY_TERMINAL_NOT_STOPPED')
            finally:
                for task in (first,second):
                    if not task.done(): task.cancel()
                await asyncio.gather(first,second,return_exceptions=True)

    async def test_wrong_item_stop_never_signals_active_target(self):
        with patch('platform_codex_executor.shutil.which',return_value=str(self.cli())):
            task = asyncio.create_task(self.host.execute(self.envelope('run-target',self.items[0].id),repository=self.repo))
            try:
                await self.wait_for_start()
                pid = int((self.root/'target-started').read_text())
                bad = self.envelope('wrong-stop','not-an-item','stop_codex_once')
                with self.assertRaisesRegex(HostDenied,'STOP_ITEM_NOT_BOUND'):
                    await self.host.execute(bad,repository=self.repo)
                self.assertIn(pid,live_group_members(pid))
                stopped = (await self.host.execute(self.envelope('right-stop',self.items[0].id,'stop_codex_once'),repository=self.repo))['body']['result']
                self.assertEqual(stopped['status'],'STOPPED')
                await task
            finally:
                if not task.done(): task.cancel()
                await asyncio.gather(task,return_exceptions=True)

    async def test_unknown_attempt_has_no_process_to_recover_or_signal(self):
        from datetime import datetime, timezone
        await self.repo.reserve_native_attempt(self.batch_id,self.items[0].id,
            user_id=self.cfg['owner'],binding=self.binding,intent_id='unknown-fixture',
            now=datetime.now(timezone.utc))
        result = (await self.host.execute(self.envelope('unknown-stop',self.items[0].id,'stop_codex_once'),repository=self.repo))['body']['result']
        self.assertEqual(result['status'],'STOP_NOT_OWNED_QUERY_ONLY')
        async with self.sf() as session:
            item = await session.get(self.Item,self.items[0].id)
            self.assertEqual((item.status,item.attempt),('running',1))

    async def test_stop_works_while_child_never_reads_large_stdin(self):
        stub = self.root/'synthetic-no-stdin-cli'
        marker = self.root/'no-stdin-started'
        stub.write_text('#!'+sys.executable+'\nimport time\nfrom pathlib import Path\n'
                        f'Path({str(marker)!r}).write_text("started")\ntime.sleep(60)\n')
        stub.chmod(0o700)
        with patch('platform_codex_executor.shutil.which',return_value=str(stub)):
            task = asyncio.create_task(self.host.execute(self.envelope('large-input',self.items[0].id),repository=self.repo))
            try:
                for _ in range(200):
                    if marker.exists():break
                    await asyncio.sleep(.05)
                self.assertTrue(marker.exists())
                stopped = (await self.host.execute(self.envelope('stop-large-input',self.items[0].id,'stop_codex_once'),repository=self.repo))['body']['result']
                self.assertEqual(stopped['status'],'STOPPED')
                self.assertTrue(stopped['process_group_stopped'])
                await task
            finally:
                if not task.done():task.cancel()
                await asyncio.gather(task,return_exceptions=True)


if __name__ == '__main__':
    unittest.main()
