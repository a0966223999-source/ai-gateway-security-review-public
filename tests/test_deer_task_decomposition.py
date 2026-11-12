"""Goal planning contract plus original Holder/SQL integration when available."""
import asyncio
import hmac
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

from pydantic import ValidationError
from mcp.server.fastmcp import FastMCP
from batch_mcp_entry import Submit, planning_response
from fresh_batch_host import FreshBatchHost, HostDenied, canonical, digest, initialize
from fresh_batch_rpc import install_fresh_batch_tools


class PlanningContractTests(unittest.TestCase):
    def test_goal_only_requests_current_gpt_without_claiming_saved_work(self):
        request = Submit(parent_id='parent', operation_key='plan-one', goal='Prepare a three-part report')
        reply = planning_response(request)
        self.assertEqual(reply['planner'], 'current_gpt_work')
        self.assertFalse(reply['saved'])
        self.assertFalse(reply['automatic_dispatch'])
        self.assertEqual(reply['operation_key'], request.operation_key)
        self.assertIn('batch_receipt', reply['next_action'])

    def test_missing_goal_and_items_is_not_a_decomposition(self):
        with self.assertRaises(ValidationError):
            Submit(parent_id='parent', operation_key='empty')

    def test_duplicate_keys_and_injected_runtime_are_rejected(self):
        for change in [dict(items=[dict(key='a', prompt='one'), dict(key='a', prompt='two')]),
                       dict(runtime={'owner': 'forged'})]:
            with self.assertRaises(ValidationError):
                Submit(parent_id='parent', operation_key='invalid', goal='one goal', **change)

    def test_goal_only_authenticated_tool_does_not_touch_transport(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmp:
                config = initialize(Path(tmp) / 'host', owner='oauth:' + 'a' * 64)
                mcp = FastMCP('original-entry')
                env = dict(XJ_FRESH_BATCH_HOST_ENABLED='true',
                    XJ_FRESH_BATCH_HOST_CONFIG=json.dumps(config), XJ_FRESH_BATCH_INGRESS_KEY='11' * 32)
                def no_transport():
                    raise AssertionError('planning must not enqueue or call Redis')
                install_fresh_batch_tools(mcp, principal=lambda: config['owner'], oauth_meta={},
                    env=env, connection_factory=no_transport)
                fn = mcp._tool_manager.get_tool('batch_task').fn
                request = Submit(parent_id=config['parent_id'], operation_key='goal-only', goal='Plan a report')
                response = await fn(request)
                self.assertEqual(response['status'], 'AWAITING_CURRENT_GPT_DECOMPOSITION')
                with self.assertRaisesRegex(Exception, 'OWNER_PARENT'):
                    await fn(request.model_copy(update={'parent_id': 'some-other-parent'}))
        asyncio.run(run())


@unittest.skipUnless(os.environ.get('XJ_DEER_BACKEND'), 'original Gateway backend not configured')
class OriginalSQLPlanTests(unittest.TestCase):
    def test_goal_and_three_children_survive_restart_same_original_sql(self):
        backend = Path(os.environ['XJ_DEER_BACKEND']).resolve(strict=True)
        sys.path[:0] = [str(backend), str(backend / 'packages/harness'), str(backend / 'packages/extension-api')]
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from deerflow.persistence.subagent_batches.sql import SubagentBatchRepository
        from deerflow.persistence.subagent_batches.model import SubagentBatchRow
        from deerflow.persistence.base import Base

        async def run():
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                cfg = initialize(root / 'host', owner='oauth:' + 'a' * 64)
                host = FreshBatchHost(root / 'host')
                engine = create_async_engine('sqlite+aiosqlite:///' + str(root / 'isolated.sqlite'))
                sf = async_sessionmaker(engine, expire_on_commit=False)
                repo = SubagentBatchRepository(sf)
                async with engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
                def envelope(action, key, payload):
                    body = dict(generation=cfg['generation'], owner=cfg['owner'], parent_id=cfg['parent_id'],
                        action=action, operation_key=key, payload=payload,
                        request_id=digest([cfg['generation'], cfg['owner'], action, key]), expires_at=time.time()+60)
                    return dict(body=body, mac=hmac.new(host.ingress, canonical(body), 'sha256').hexdigest())
                request = envelope('submit', 'three-children', dict(goal='Compare three independent provider documents',
                    title='Original goal with three parts', items=[dict(key=str(i), prompt='Read provider '+str(i),
                    acceptance_criteria=['Return source and limitations']) for i in range(3)]))
                previous_cwd = Path.cwd()
                try:
                    os.chdir(backend)
                    response = await host.execute(request, repository=repo)
                    self.assertEqual(response['body']['result']['decomposition']['goal'], request['body']['payload']['goal'])
                    async with sf() as session:
                        from sqlalchemy import select
                        rows = (await session.execute(select(SubagentBatchRow))).scalars().all()
                        self.assertEqual(len(rows), 1)
                        batch_id = rows[0].id
                        self.assertEqual(rows[0].total_items, 3)
                    resumed = FreshBatchHost(root / 'host')
                    with self.assertRaisesRegex(HostDenied, 'ATTEMPTED_QUERY_ONLY'):
                        resumed.reserve(request['body'])
                    read = await resumed.execute(envelope('read', 'read-plan', dict(batch_id=batch_id,
                        include_plan=True, item_offset=0, item_limit=3)), repository=repo)
                    plan = read['body']['result']['decomposition']
                    self.assertEqual(plan['goal'], request['body']['payload']['goal'])
                    self.assertEqual(plan['parent_id'], cfg['parent_id'])
                    self.assertEqual(len(plan['children']), 3)
                    self.assertTrue(all(i['status']=='pending' and i['attempt']==0 for i in plan['children']))
                    await resumed.execute(envelope('cancel', 'cancel-plan', dict(batch_id=batch_id)), repository=repo)
                finally:
                    os.chdir(previous_cwd)
                    await engine.dispose()
        asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
