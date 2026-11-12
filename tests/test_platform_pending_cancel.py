"""Real temporary original SQL; synthetic authority, never a live grant."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone

from fresh_batch_host import FreshBatchHost, HostDenied, initialize, canonical, digest
from platform_pending_cancel import PendingCancel, cancel_pending_items


@unittest.skipUnless(os.environ.get('XJ_DEER_BACKEND'), 'original backend not configured')
class PendingCancelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.backend = Path(os.environ['XJ_DEER_BACKEND']).resolve(strict=True)
        sys.path[:0] = [str(self.backend), str(self.backend/'packages/harness'),
                       str(self.backend/'packages/extension-api')]
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from sqlalchemy import select
        from deerflow.persistence.subagent_batches.sql import SubagentBatchRepository
        from deerflow.persistence.subagent_batches.model import SubagentBatchRow, SubagentBatchItemRow
        from deerflow.persistence.base import Base
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_cwd = Path.cwd()
        os.chdir(self.backend)
        self.cfg = initialize(self.root/'host', owner='oauth:'+'a'*64,
                              platform_service=True, enable_codex=True)
        self.host = FreshBatchHost(self.root/'host')
        self.engine = create_async_engine('sqlite+aiosqlite:///'+str(self.root/'test.sqlite'))
        self.sf = async_sessionmaker(self.engine, expire_on_commit=False)
        self.repo = SubagentBatchRepository(self.sf)
        self.Batch, self.Item = SubagentBatchRow, SubagentBatchItemRow
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        body = dict(generation=self.cfg['generation'], owner=self.cfg['owner'],
            parent_id=self.cfg['parent_id'], action='submit', operation_key='fixture-submit',
            payload={'title':'fixture', 'items':[{'key':str(i),'prompt':'synthetic'} for i in range(3)]},
            expires_at=time.time()+60)
        body['request_id'] = digest([self.cfg['generation'], self.cfg['owner'], 'submit', 'fixture-submit'])
        await self.host.execute({'body':body,'mac':hmac.new(self.host.ingress,canonical(body),'sha256').hexdigest()}, repository=self.repo)
        async with self.sf() as session:
            batch = (await session.execute(select(self.Batch))).scalar_one()
            self.batch_id = batch.id
            self.binding = batch.execution_spec['native_control']['binding']
            self.items = list((await session.execute(select(self.Item).order_by(self.Item.position))).scalars())
        self.request = PendingCancel(parent_id=self.cfg['parent_id'], operation_key='cancel-pending',
                                     batch_id=self.batch_id, pending_only=True)

    async def asyncTearDown(self):
        os.chdir(self.old_cwd)
        await self.engine.dispose()
        self.temp.cleanup()

    async def test_mixed_completed_running_pending_preserves_native_fences_and_results(self):
        for index in (0,1):
            item = self.items[index]
            await self.repo.reserve_native_attempt(self.batch_id,item.id,user_id=self.cfg['owner'],
                binding=self.binding,intent_id=f'intent-{index}',now=datetime.now(timezone.utc))
            await self.repo.record_native_ack(self.batch_id,item.id,user_id=self.cfg['owner'],
                binding=self.binding,intent_id=f'intent-{index}',child_canonical=f'fixture-{index}',
                evidence_sha256='b'*64,now=datetime.now(timezone.utc))
        await self.repo.finalize_item(self.items[0].id,lease_owner=self.cfg['owner'],succeeded=True,
            result='synthetic result',result_preview='synthetic result',result_truncated=False,error=None,
            stop_reason='completed',token_usage=None,model_name='fixture',completed_at=datetime.now(timezone.utc),
            native_context=dict(binding=self.binding,intentId='intent-0',childCanonical='fixture-0',
                ackEvidenceSHA256='b'*64,reportSHA256=hashlib.sha256(b'synthetic result').hexdigest()))
        async with self.sf() as session:
            before = json.dumps((await session.get(self.Batch,self.batch_id)).execution_spec,sort_keys=True)
        result = await cancel_pending_items(self.host,self.repo,self.request)
        self.assertEqual(result['cancelled_pending_item_ids'],[self.items[2].id])
        self.assertFalse(result['running_items_stopped'])
        async with self.sf() as session:
            rows = [await session.get(self.Item,item.id) for item in self.items]
            self.assertEqual([(r.status,r.attempt) for r in rows], [('succeeded',1),('running',1),('cancelled',0)])
            self.assertEqual(rows[0].result,'synthetic result')
            self.assertEqual(json.dumps((await session.get(self.Batch,self.batch_id)).execution_spec,sort_keys=True),before)
        again = await cancel_pending_items(self.host,self.repo,self.request)
        self.assertEqual(again['cancelled_pending_item_ids'],[])

    async def test_revoked_host_cannot_cancel(self):
        (self.host.root/'STOP').touch(mode=0o600)
        with self.assertRaises(HostDenied):
            await cancel_pending_items(self.host,self.repo,self.request)

    async def test_changed_owner_cannot_cancel(self):
        async with self.sf() as session:
            batch = await session.get(self.Batch,self.batch_id)
            batch.user_id = 'another-owner'
            await session.commit()
        with self.assertRaises(HostDenied):
            await cancel_pending_items(self.host,self.repo,self.request)

    async def test_attempt_marked_pending_row_is_rejected_without_partial_cancel(self):
        async with self.sf() as session:
            item = await session.get(self.Item,self.items[2].id)
            item.attempt = 1
            await session.commit()
        with self.assertRaises(HostDenied):
            await cancel_pending_items(self.host,self.repo,self.request)
        async with self.sf() as session:
            states = [(await session.get(self.Item,item.id)).status for item in self.items]
            self.assertEqual(states, ['pending'] * 3)


if __name__ == '__main__':
    unittest.main()
