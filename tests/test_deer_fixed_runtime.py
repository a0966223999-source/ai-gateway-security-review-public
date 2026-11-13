"""Original SQL/Registry/Source and real OS processes; synthetic identity/CLI."""
import asyncio
import hashlib
import hmac
import json
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

from deer_agent_management import AgentManagementDenied
from deer_runtime_consent import RuntimeConsentStore
from deer_fixed_runtime_host import FixedAgentRuntimeService, definition_snapshot
from deer_fixed_runtime_rpc import FixedRequest, MODE, wire_key, action_for, install_fixed_runtime_tools
from fresh_batch_host import canonical, digest, HostDenied
from test_deer_management_consent import original_source_modules
import test_platform_pending_cancel as fixture


class RuntimeSchemaTests(unittest.TestCase):
    def test_request_has_no_identity_or_definition_overrides(self):
        for key in ('owner','grant_id','soul','command','pid','definition_sha256'):
            with self.assertRaises(ValueError):
                FixedRequest(kind='prepare',name='fixture',operation_key='op',task='calc',**{key:'x'})
        with self.assertRaises(ValueError):
            FixedRequest(kind='execute',name='fixture',operation_key='op',task='override',
                         batch_id='b',item_id='i').scoped()
        with self.assertRaises(ValueError):
            FixedRequest(kind='prepare',name='fixture',operation_key='op',task='x',timeout_seconds=10).scoped()

    def test_runtime_mcp_default_off(self):
        self.assertFalse(install_fixed_runtime_tools(None,rpc=None,principal=None,oauth_meta=None))

    def test_historical_receipt_only_recovers_identifiers(self):
        from deer_fixed_runtime_rpc import public_reply
        result=public_reply(dict(request_id='fixture',result=dict(kind=MODE,status='PREPARED_READBACK_REQUIRED',
            batch_id='saved-batch',definition_sha256='c'*64,original_batch={'private':'role contents'})),historical=True)
        self.assertEqual(result['batch_id'],'saved-batch')
        self.assertFalse(result['authority'])
        self.assertNotIn('original_batch',result)


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.PendingCancelTests.asyncSetUp(self)
        self.root.chmod(0o700)
        self.io = original_source_modules()
        from app.gateway.platform_account_link import PinnedMcpAccount
        from deerflow.persistence.agents.sql import SqlAgentStore
        from sqlalchemy import create_engine
        from deerflow.persistence.base import Base
        from deerflow.persistence.agents.model import AgentRow
        url='sqlite:///'+str(self.root/'agents.sqlite')
        engine=create_engine(url)
        Base.metadata.create_all(engine,tables=[AgentRow.__table__]);engine.dispose()
        self.agents=SqlAgentStore(url)
        self.agents.create('runtime-fixture',{'name':'runtime-fixture','description':'Synthetic arithmetic',
            'tool_groups':[],'skills':[],'allowed_subagents':[]},'Calculate only.',user_id='fixture-owner')
        _, self.role_sha=await definition_snapshot(self.agents,'runtime-fixture','fixture-owner')
        account=PinnedMcpAccount(self.host.root/'host.json',self.host.pin)
        self.now=time.time();self.allowed=True
        self.consents=RuntimeConsentStore(self.root,account=account,current=lambda:True,
            source_io=self.io,clock=lambda:self.now)
        self.record=self.consents.approve(user_id='fixture-owner',thread_id='fixture-thread',
            token_version=3,operation_key='runtime-consent',names=['runtime-fixture'],
            actions=['prepare','read','execute','stop'],ttl_seconds=600,definition_sha256=self.role_sha)
        async def policy(record):return self.allowed
        self.service=FixedAgentRuntimeService(consent_store=self.consents,grant_id=self.record['grant_id'],
            agent_store=self.agents,live_policy=policy,current=lambda:True)

    async def asyncTearDown(self):
        self.agents._Session.kw['bind'].dispose()
        await fixture.PendingCancelTests.asyncTearDown(self)

    def envelope(self,kind,key,**payload):
        action=action_for(kind);wire=wire_key(kind,key)
        body=dict(generation=self.cfg['generation'],owner=self.cfg['owner'],parent_id=self.cfg['parent_id'],
            action=action,operation_key=wire,payload=dict(kind=kind,name='runtime-fixture',**payload,
                execution_mode=MODE,runtime_operation_key=key),expires_at=time.time()+60,
            request_id=digest([self.cfg['generation'],self.cfg['owner'],action,wire]))
        return dict(body=body,mac=hmac.new(self.host.ingress,canonical(body),'sha256').hexdigest())

    async def call(self,kind,key,**payload):
        response=await self.host.execute(self.envelope(kind,key,**payload),repository=self.repo,
                                         agent_runtime=self.service)
        self.host.key.public_key().verify(__import__('base64').b64decode(response['signature']),canonical(response['body']))
        return response['body']['result']

    async def prepare(self,key='prepare-1'):
        result=await self.call('prepare',key,task='Calculate 705+98 and output 803.')
        self.assertEqual(result['status'],'PREPARED_READBACK_REQUIRED',result)
        from sqlalchemy import select
        async with self.sf() as session:
            batches=list((await session.execute(select(self.Batch))).scalars())
            batch=next(b for b in batches if b.execution_spec.get('fixed_agent_runtime'))
            self.assertEqual(result['batch_id'],batch.id)
            items=list((await session.execute(select(self.Item).where(self.Item.batch_id==batch.id))).scalars())
            self.assertEqual(len(items),1)
            self.assertEqual((items[0].status,items[0].attempt),('pending',0))
            self.assertEqual(batch.execution_spec['fixed_agent_runtime']['definition_sha256'],self.role_sha)
            return batch.id,items[0].id

    def cli(self,wait=False):
        path=self.root/'synthetic-cli'
        path.write_text('#!'+sys.executable+'\nimport sys,json,time,os\nfrom pathlib import Path\n'
            'prompt=sys.stdin.read()\n'
            f'Path({str(self.root/"started")!r}).write_text(str(os.getpid()))\n'
            'print(json.dumps({"type":"thread.started","thread_id":"synthetic-fixed-role"}),flush=True)\n'+
            ('time.sleep(60)\n' if wait else '')+
            'print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"803"}}),flush=True)\n'
            'print(json.dumps({"type":"turn.completed"}),flush=True)\n')
        path.chmod(0o700);return str(path)

    async def started(self):
        for _ in range(150):
            if (self.root/'started').exists():return int((self.root/'started').read_text())
            await asyncio.sleep(.05)
        self.fail('synthetic CLI never started')

    async def test_prepare_execute_independent_readback_and_once_fence(self):
        batch,item=await self.prepare()
        with patch('platform_codex_executor.shutil.which',return_value=self.cli()):
            result=await self.call('execute','execute-1',batch_id=batch,item_id=item)
            self.assertTrue(result['original_result']['original_result_saved'],result)
            readback=await self.call('status','read-1',batch_id=batch)
            self.assertIn('803',json.dumps(readback))
            with self.assertRaisesRegex(HostDenied,'ATTEMPTED_QUERY_ONLY'):
                await self.call('execute','execute-1',batch_id=batch,item_id=item)
            with self.assertRaisesRegex(HostDenied,'PENDING_ITEM_REQUIRED'):
                await self.call('execute','execute-new-key',batch_id=batch,item_id=item)
        self.assertEqual(self.host.pin,digest(self.cfg))

    async def test_management_grant_type_is_not_runtime_authority(self):
        from deer_management_consent import ManagementConsentStore
        with self.assertRaisesRegex(AgentManagementDenied,'COMPOSITION'):
            FixedAgentRuntimeService(consent_store=object.__new__(ManagementConsentStore),
                grant_id=self.record['grant_id'],agent_store=self.agents,current=lambda:True,live_policy=lambda _:True)

    async def test_general_batch_execute_cannot_bypass_role_lease(self):
        batch,item=await self.prepare()
        body=dict(generation=self.cfg['generation'],owner=self.cfg['owner'],parent_id=self.cfg['parent_id'],
            action='execute',operation_key='ordinary-bypass',payload=dict(execution_mode='codex_once',
            batch_id=batch,item_id=item),expires_at=time.time()+60)
        body['request_id']=digest([self.cfg['generation'],self.cfg['owner'],'execute','ordinary-bypass'])
        with patch('platform_codex_executor.shutil.which',return_value=self.cli()):
            with self.assertRaisesRegex(HostDenied,'FIXED_RUNTIME_LEASE_REQUIRED'):
                await self.host.execute(dict(body=body,mac=hmac.new(self.host.ingress,canonical(body),'sha256').hexdigest()),repository=self.repo)
        self.assertFalse((self.root/'started').exists())

    async def test_background_batch_cannot_bypass_role_lease(self):
        from platform_codex_batch import ExecuteBatch, execute_batch
        batch,item=await self.prepare()
        with patch('platform_codex_executor.shutil.which',return_value=self.cli()):
            result=await execute_batch(self.host,self.repo,ExecuteBatch(
                parent_id=self.cfg['parent_id'],operation_key='background-bypass',
                execution_mode='codex_batch_once',batch_id=batch,item_ids=[item]))
        self.assertEqual(result['status'],'CODEX_BATCH_HALTED_QUERY_ONLY')
        self.assertEqual(result['reason'],'FIXED_RUNTIME_LEASE_REQUIRED')
        self.assertFalse((self.root/'started').exists())

    async def test_two_preparation_slots_persist_and_no_replay(self):
        await self.prepare()
        with self.assertRaisesRegex(HostDenied,'ATTEMPTED_QUERY_ONLY'):
            await self.prepare()
        self.assertEqual((await self.call('prepare','prepare-2',task='Second synthetic task'))['status'],'PREPARED_READBACK_REQUIRED')
        result=await self.call('prepare','prepare-3',task='Disallowed third task')
        self.assertEqual(result['reason'],'RUNTIME_RUN_LIMIT_QUERY_ONLY')

    async def test_revoked_or_changed_role_denied_before_launch(self):
        batch,item=await self.prepare()
        self.now+=601
        result=await self.call('execute','expired',batch_id=batch,item_id=item)
        self.assertIn('REVOKED_OR_EXPIRED',result['reason'])
        self.now-=601;self.allowed=False
        result=await self.call('execute','policy-denied',batch_id=batch,item_id=item)
        self.assertIn('POLICY_DENIED',result['reason'])

    async def test_role_changed_after_prepare_blocks_execution(self):
        batch,item=await self.prepare()
        # Replace the original stored soul through its actual SQL row.
        from deerflow.persistence.agents.model import AgentRow
        with self.agents._Session() as session:
            row=session.query(AgentRow).filter_by(name='runtime-fixture').one()
            row.soul='Changed role after approval.'
            session.commit()
        result=await self.call('execute','changed-role',batch_id=batch,item_id=item)
        self.assertIn('DEFINITION_CHANGED',result['reason'])
        self.assertFalse((self.root/'started').exists())

    async def test_revocation_during_execution_kills_owned_process(self):
        from platform_codex_stop import live_group_members
        batch,item=await self.prepare()
        with patch('platform_codex_executor.shutil.which',return_value=self.cli(wait=True)):
            task=asyncio.create_task(self.call('execute','revoking',batch_id=batch,item_id=item))
            try:
                pid=await self.started()
                self.consents.revoke(self.record['grant_id'],verified_owner=self.cfg['owner'],
                    user_id='fixture-owner',thread_id='fixture-thread')
                result=await asyncio.wait_for(task,12)
                self.assertIn('REVOKED_OR_EXPIRED',result['reason'])
                self.assertEqual(live_group_members(pid),[])
            finally:
                if not task.done():task.cancel()
                await asyncio.gather(task,return_exceptions=True)

    async def test_expiry_during_execution_kills_owned_process_without_claiming_user_stop(self):
        from platform_codex_stop import live_group_members
        batch,item=await self.prepare()
        with patch('platform_codex_executor.shutil.which',return_value=self.cli(wait=True)):
            task=asyncio.create_task(self.call('execute','expiring',batch_id=batch,item_id=item))
            try:
                pid=await self.started();self.now+=601
                result=await asyncio.wait_for(task,12)
                self.assertIn('REVOKED_OR_EXPIRED',result['reason'])
                self.assertEqual(live_group_members(pid),[])
                async with self.sf() as session:
                    row=await session.get(self.Item,item)
                    self.assertEqual(row.attempt,1)
                    self.assertNotEqual(row.stop_reason,'stopped_by_request')
            finally:
                if not task.done():task.cancel()
                await asyncio.gather(task,return_exceptions=True)

    async def test_explicit_stop_saves_original_terminal_state(self):
        from platform_codex_stop import live_group_members
        batch,item=await self.prepare()
        with patch('platform_codex_executor.shutil.which',return_value=self.cli(wait=True)):
            task=asyncio.create_task(self.call('execute','running',batch_id=batch,item_id=item))
            try:
                pid=await self.started()
                stopped=await self.call('stop','stop-1',batch_id=batch,item_id=item)
                self.assertEqual(stopped['original_result']['status'],'STOPPED',stopped)
                self.assertEqual(live_group_members(pid),[])
                await task
                async with self.sf() as session:
                    row=await session.get(self.Item,item)
                    self.assertEqual((row.status,row.attempt,row.stop_reason),('cancelled',1,'stopped_by_request'))
            finally:
                if not task.done():task.cancel()
                await asyncio.gather(task,return_exceptions=True)
