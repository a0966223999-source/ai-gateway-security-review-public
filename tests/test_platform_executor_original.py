"""Original temporary SQL plus a synthetic CLI process; NOT live Codex evidence."""
import asyncio
import hmac
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from fresh_batch_host import FreshBatchHost, HostDenied, canonical, digest, initialize
from platform_codex_executor import Execute, execute_item


@unittest.skipUnless(os.environ.get('XJ_DEER_BACKEND'), 'original backend not configured')
class OriginalExecutorTests(unittest.TestCase):
    def test_full_saved_payload_binding_once_and_stop_owned_process(self):
        backend = Path(os.environ['XJ_DEER_BACKEND']).resolve(strict=True)
        sys.path[:0] = [str(backend), str(backend/'packages/harness'), str(backend/'packages/extension-api')]
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from sqlalchemy import select
        from deerflow.persistence.subagent_batches.sql import SubagentBatchRepository
        from deerflow.persistence.subagent_batches.model import SubagentBatchRow, SubagentBatchItemRow
        from deerflow.persistence.base import Base

        async def run(stop):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                cfg = initialize(root/'host', owner='oauth:'+'a'*64, platform_service=True, enable_codex=True)
                host = FreshBatchHost(root/'host')
                engine = create_async_engine('sqlite+aiosqlite:///'+str(root/'test.sqlite'))
                sf = async_sessionmaker(engine, expire_on_commit=False)
                repo = SubagentBatchRepository(sf)
                async with engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
                body = dict(generation=cfg['generation'], owner=cfg['owner'], parent_id=cfg['parent_id'],
                    action='submit', operation_key='fixture-submit', payload=dict(title='fixture',
                    items=[dict(key='fixture', prompt='synthetic test prompt')]), expires_at=time.time()+60)
                body['request_id'] = digest([cfg['generation'], cfg['owner'], 'submit', 'fixture-submit'])
                env = dict(body=body, mac=hmac.new(host.ingress, canonical(body), 'sha256').hexdigest())
                previous = Path.cwd()
                try:
                    os.chdir(backend)
                    await host.execute(env, repository=repo)
                    async with sf() as session:
                        batch = (await session.execute(select(SubagentBatchRow))).scalar_one()
                        item = (await session.execute(select(SubagentBatchItemRow))).scalar_one()
                        batch_id, item_id = batch.id, item.id
                    # A real local child process, but an explicitly synthetic CLI.
                    stub = root/'synthetic-cli'
                    marker = root/'started'
                    stub.write_text('#!'+sys.executable+'\nimport json,os,sys,time\n'
                        'sys.stdin.read()\n'
                        'open('+repr(str(marker))+',"w").write(str(os.getpid()))\n'
                        'print(json.dumps({"type":"thread.started","thread_id":"synthetic-only"}),flush=True)\n'
                        + ('time.sleep(60)\n' if stop else
                           'print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"synthetic report"}}),flush=True)\n'
                           'print(json.dumps({"type":"turn.completed"}),flush=True)\n'))
                    stub.chmod(0o700)
                    req = Execute(parent_id=cfg['parent_id'], operation_key='fixture-execute',
                                  batch_id=batch_id, item_id=item_id, execution_mode='codex_once')
                    with patch('platform_codex_executor.shutil.which', return_value=str(stub)):
                        task = asyncio.create_task(execute_item(host, repo, req))
                        if stop:
                            for _ in range(100):
                                if marker.exists() and marker.read_text():
                                    break
                                await asyncio.sleep(.02)
                            self.assertTrue(marker.exists())
                            pid = int(marker.read_text())
                            (host.root/'STOP').touch(mode=0o600)
                            with self.assertRaises(HostDenied):
                                await task
                            with self.assertRaises(ProcessLookupError):
                                os.kill(pid, 0)
                        else:
                            result = await task
                            self.assertTrue(result['original_result_saved'])
                            async with sf() as session:
                                saved = await session.get(SubagentBatchItemRow, item_id)
                                self.assertEqual((saved.status, saved.attempt, saved.result),
                                                 ('succeeded', 1, 'synthetic report'))
                            with self.assertRaisesRegex(HostDenied, 'PENDING_ITEM'):
                                await execute_item(host, repo, req)
                finally:
                    os.chdir(previous)
                    await engine.dispose()
        asyncio.run(run(False))
        asyncio.run(run(True))


if __name__ == '__main__':
    unittest.main()
