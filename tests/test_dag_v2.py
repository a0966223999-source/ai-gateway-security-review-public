"""Real original temporary SQL; synthetic authority and CLI, never production QA."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
import unittest
from unittest.mock import patch
from fresh_batch_host import HostDenied, FreshBatchHost, canonical, digest
from platform_codex_dag import parse_goal, snapshot, dependency_inputs, verified_result
from platform_dag_runtime import ExecuteDag, recoverable_bodies, recover_response, scope_for, save_runtime
import test_platform_codex_dag as original


class ParseV2(unittest.TestCase):
    def test_hundred_node_chain_and_limit(self):
        deps={str(i):[str(i-1)] if i else [] for i in range(100)}
        g=dict(schema='deer-codex-dag-v2',goal='test',dependencies=deps)
        self.assertEqual(len(parse_goal(json.dumps(g), list(deps))['order']),100)
        deps['100']=['99']
        with self.assertRaises(HostDenied):parse_goal(json.dumps(g),list(deps))

    def test_malformed_external_and_duplicate(self):
        ref=dict(batch_id='b',item_id='i',payload_digest='a'*64)
        for parents in [[dict(ref,payload_digest='z'*64)],[ref,ref],[dict(ref,owner='forged')]]:
            with self.assertRaises(HostDenied):parse_goal(json.dumps(dict(schema='deer-codex-dag-v2',
                goal='test',dependencies={'C':parents})),['C'])


@unittest.skipUnless(os.environ.get('XJ_DEER_BACKEND'),'original backend required')
class DurableTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=original.OriginalDagTests.asyncSetUp
    asyncTearDown=original.OriginalDagTests.asyncTearDown
    envelope=original.OriginalDagTests.envelope
    states=original.OriginalDagTests.states
    cli=original.OriginalDagTests.cli

    async def v2(self):
        # Save a new immutable submission; never retrofit original saved plan.
        return await self.submit_graph({'A':[],'B':[],'C':['A','B']})

    async def submit_graph(self,deps):
        key='submit-v2-'+str(getattr(self,'serial',0));self.serial=getattr(self,'serial',0)+1
        goal=json.dumps(dict(schema='deer-codex-dag-v2',goal='synthetic DAG',dependencies=deps))
        response=await self.host.execute(self.envelope(key,'submit',dict(title=key,goal=goal,
            max_live_items=3,max_running_items=3,items=[dict(key=k,prompt='DAG-'+k) for k in deps])),repository=self.repo)
        from sqlalchemy import select
        async with self.sf() as session:
            batch=(await session.execute(select(self.Batch).where(self.Batch.title==key))).scalar_one()
            rows=list((await session.execute(select(self.Item).where(self.Item.batch_id==batch.id))).scalars())
            return batch.id,{r.item_key:r.id for r in rows}

    def execute(self,batch,key='execute-v2'):
        return self.envelope(key,'execute',dict(execution_mode='codex_dag_once',batch_id=batch,timeout_seconds=30))

    async def run_graph(self,batch,key='execute-v2'):
        with patch('platform_codex_executor.shutil.which',return_value=str(self.cli())):
            return await self.host.execute(self.execute(batch,key),repository=self.repo)

    async def test_four_nodes_exceed_old_limit(self):
        batch,ids=await self.submit_graph({'A':[],'B':[],'C':['A','B'],'D':['C']})
        reply=await self.run_graph(batch)
        self.assertEqual(reply['body']['result']['status'],'CODEX_DAG_FINISHED')
        self.assertEqual((self.root/'trace').read_text(),'A\nB\nC\nD\n')
        self.assertEqual(len(reply['body']['result']['children']),4)

    async def test_result_tamper_or_missing_completion_blocks_join(self):
        batch,ids=await self.v2()
        with patch('platform_codex_executor.shutil.which',return_value=str(self.cli())):
            await self.host.execute(self.envelope('root-A','execute',dict(execution_mode='codex_once',
                batch_id=batch,item_id=ids['A'],timeout_seconds=30)),repository=self.repo)
        node=(await snapshot(self.host,self.repo,batch))['nodes']['A']
        verified_result(node)
        for field in ('result','result_preview','model_name','token_usage'):
            changed=deepcopy(node);changed['completion_payload'][field]='changed'
            with self.subTest(field=field),self.assertRaisesRegex(HostDenied,'DEPENDENCY_NOT_VERIFIED'):
                verified_result(changed)
        for change in ('missing','badack','emptychild'):
            changed=deepcopy(node)
            if change=='missing':changed['receipt'].pop('completion')
            if change=='badack':changed['receipt']['ackEvidenceSHA256']='z'*64
            if change=='emptychild':changed['receipt']['childCanonical']='codex-thread:'
            with self.subTest(change=change),self.assertRaises(HostDenied):verified_result(changed)

    async def test_recovery_after_saved_root_never_replays(self):
        batch,ids=await self.v2();envelope=self.execute(batch)
        real=save_runtime
        calls=0
        async def crash_after_first(*args,**kwargs):
            nonlocal calls
            await real(*args,**kwargs);calls+=1
            if calls==2:raise asyncio.CancelledError()
        with patch('platform_codex_executor.shutil.which',return_value=str(self.cli())),\
                patch('platform_dag_runtime.save_runtime',side_effect=crash_after_first):
            with self.assertRaises(asyncio.CancelledError):await self.host.execute(envelope,repository=self.repo)
        self.assertEqual((self.root/'trace').read_text(),'A\n')
        restarted=FreshBatchHost(self.host.root)
        bodies=await recoverable_bodies(restarted,self.repo)
        self.assertEqual([b['request_id'] for b in bodies],[envelope['body']['request_id']])
        with patch('platform_codex_executor.shutil.which',return_value=str(self.cli())):
            reply=await recover_response(restarted,self.repo,bodies[0])
        self.assertEqual(reply['body']['result']['status'],'CODEX_DAG_FINISHED')
        self.assertEqual((self.root/'trace').read_text(),'A\nB\nC\n')
        self.assertTrue(reply['body']['result']['children'][0]['reused_saved_result'])

    async def admitted(self,batch,key="execute-v2"):
        envelope=self.execute(batch,key);body=envelope['body'];self.host.reserve(body)
        request=ExecuteDag.model_validate(dict(body['payload'],parent_id=body['parent_id'],operation_key=body['operation_key']))
        initial=await snapshot(self.host,self.repo,batch);scope=scope_for(self.host,request,initial)
        await save_runtime(self.host,self.repo,request,scope,phase='active')
        return body,scope

    async def test_restart_unknown_native_fence_never_spawns(self):
        batch,ids=await self.v2();body,scope=await self.admitted(batch)
        await self.repo.reserve_native_attempt(batch,ids['A'],user_id=self.cfg['owner'],
            binding=scope['binding'],intent_id='uncertain-native-send',now=datetime.now(timezone.utc))
        with patch('platform_codex_executor.subprocess.Popen') as spawn:
            reply=await recover_response(FreshBatchHost(self.host.root),self.repo,body)
            spawn.assert_not_called()
        result=reply['body']['result']
        self.assertEqual(result['status'],'CODEX_DAG_HALTED_QUERY_ONLY')
        self.assertIn(ids['A'],result['unverified_item_ids'])
        self.assertCountEqual(result['unstarted_item_ids'],[ids['B'],ids['C']])

    async def test_cross_batch_wait_yields_then_consumes_verified_result(self):
        parent,pids=await self.submit_graph({'A':[]})
        binding=(await snapshot(self.host,self.repo,parent))['binding']
        ref=dict(batch_id=parent,item_id=pids['A'],payload_digest=binding['payloadDigest'])
        child,cids=await self.submit_graph({'D':[ref]})
        waiting=await self.run_graph(child)
        self.assertEqual(waiting['body']['result']['status'],'CODEX_DAG_WAITING_DEPENDENCIES')
        self.assertFalse((self.root/'trace').exists())
        await self.run_graph(parent,'execute-parent')
        bodies=await recoverable_bodies(self.host,self.repo)
        body=next(b for b in bodies if b['payload']['batch_id']==child)
        with patch('platform_codex_executor.shutil.which',return_value=str(self.cli())):
            result=await recover_response(self.host,self.repo,body)
        self.assertEqual(result['body']['result']['status'],'CODEX_DAG_FINISHED')
        self.assertEqual((self.root/'trace').read_text(),'A\nD\n')
        # An original saved dependency input really carries the parent's result.
        payloads=[json.loads(p.read_text()) for p in self.host.root.glob('codex-*/dependency-inputs.json')]
        self.assertTrue(any(parent+'/'+pids['A'] in p for p in payloads))

    async def test_wrong_external_owner_or_digest_denied(self):
        parent,pids=await self.submit_graph({'A':[]})
        ref=dict(batch_id=parent,item_id=pids['A'],payload_digest='a'*64)
        with self.assertRaisesRegex(HostDenied,'BINDING_CHANGED'):await self.submit_graph({'D':[ref]})
        async with self.sf() as session:
            row=await session.get(self.Batch,parent);row.user_id='different';await session.commit()
        with self.assertRaisesRegex(HostDenied,'NOT_BOUND'):await self.submit_graph({'D':[ref]})

    async def test_stopped_batch_recovery_saves_halted(self):
        batch,ids=await self.v2();body,scope=await self.admitted(batch)
        async with self.sf() as session:
            row=await session.get(self.Batch,batch);row.status='cancelled';await session.commit()
        with patch('platform_codex_executor.subprocess.Popen') as spawn:
            reply=await recover_response(self.host,self.repo,body);spawn.assert_not_called()
        self.assertEqual(reply['body']['result']['status'],'CODEX_DAG_HALTED_QUERY_ONLY')
        self.assertEqual((await snapshot(self.host,self.repo,batch,allow_stopped=True))['runtime']['phase'],'halted')

    async def test_expired_intent_does_not_extend_on_restart(self):
        batch,ids=await self.v2();body,scope=await self.admitted(batch)
        with patch.object(self.host,'clock',return_value=scope['deadline']+1),\
                patch('platform_codex_executor.subprocess.Popen') as spawn:
            reply=await recover_response(self.host,self.repo,body);spawn.assert_not_called()
        self.assertEqual(reply['body']['result']['reason'],'DAG_DEADLINE_EXHAUSTED_QUERY_ONLY')

    async def test_new_key_cannot_adopt_existing_intent(self):
        batch,ids=await self.v2();await self.admitted(batch)
        with self.assertRaisesRegex(HostDenied,'INTENT_CHANGED'):await self.run_graph(batch,'another-key')

    async def test_capacity_before_reserve_is_unstarted(self):
        batch,ids=await self.v2();self.host._codex_execution_count=2
        reply=await self.run_graph(batch)
        self.assertEqual(reply['body']['result']['status'],'CODEX_DAG_WAITING_DEPENDENCIES')
        self.assertTrue(all(n['pristine'] for n in (await snapshot(self.host,self.repo,batch))['nodes'].values()))

    async def test_worker_eight_waiters_does_not_starve_supplier(self):
        from types import SimpleNamespace
        import fresh_batch_worker
        parent,pids=await self.submit_graph({'A':[]})
        binding=(await snapshot(self.host,self.repo,parent))['binding']
        ref=dict(batch_id=parent,item_id=pids['A'],payload_digest=binding['payloadDigest'])
        for i in range(8):
            batch,_=await self.submit_graph({'D':[ref]})
            await self.admitted(batch,'waiter-'+str(i))
        supplier=self.execute(parent,'supply-parent')
        loop=asyncio.get_running_loop();supplier_done=asyncio.Event();polls=[]
        class Response:
            url='https://original-test.vercel.app/internal/deer-batch/rpc'
            def __init__(self,value):self.value=value
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,size):return json.dumps(self.value).encode()
        class Opener:
            def open(inner,request,timeout):
                payload=json.loads(request.data)['body'];action=payload['action']
                if action=='poll':
                    polls.append(action)
                    return Response({'request':supplier if len(polls)==1 else None})
                if action=='respond' and payload['payload']['request_id']==supplier['body']['request_id']:
                    loop.call_soon_threadsafe(supplier_done.set)
                return Response({})
        args=SimpleNamespace(gateway_backend=str(self.backend),database=str(self.root/'test.sqlite'),
            root=str(self.host.root),endpoint=Response.url,agent_management_grant_id=None,
            agent_runtime_grant_id=None,gateway_pid=None)
        with patch('fresh_batch_worker.build_opener',return_value=Opener()),\
                patch('platform_codex_executor.shutil.which',return_value=str(self.cli())):
            task=asyncio.create_task(fresh_batch_worker.serve(args))
            try:
                await asyncio.wait_for(supplier_done.wait(),20)
                self.assertIn('A',(self.root/'trace').read_text().splitlines())
                self.assertGreater(len(polls),0)
            finally:
                task.cancel()
                await asyncio.gather(task,return_exceptions=True)

    async def test_real_process_exit_after_root_resumes_saved_sql(self):
        import subprocess
        import sys
        from pathlib import Path
        batch,ids=await self.v2();envelope=self.execute(batch)
        stage=Path(__file__).resolve().parents[1]
        worker=self.root/'crash_worker.py'
        worker.write_text("""
import asyncio, json, os
from unittest.mock import patch
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from deerflow.persistence.subagent_batches.sql import SubagentBatchRepository
from fresh_batch_host import FreshBatchHost
import platform_dag_runtime as dag
async def run():
    host=FreshBatchHost(HOST)
    engine=create_async_engine('sqlite+aiosqlite:///'+DB)
    repo=SubagentBatchRepository(async_sessionmaker(engine,expire_on_commit=False))
    real=dag.save_runtime
    count=0
    async def crash(*args,**kwargs):
        nonlocal count
        await real(*args,**kwargs);count+=1
        if count==2:os._exit(77)
    with patch('platform_codex_executor.shutil.which',return_value=CLI), patch.object(dag,'save_runtime',side_effect=crash):
        await host.execute(ENVELOPE,repository=repo)
asyncio.run(run())
""".replace('HOST',repr(str(self.host.root))).replace('DB',repr(str(self.root/'test.sqlite')))
    .replace('CLI',repr(str(self.cli()))).replace('ENVELOPE',repr(envelope)))
        env=dict(os.environ,PYTHONPATH=':'.join([str(stage),str(self.backend),
            str(self.backend/'packages/harness'),str(self.backend/'packages/extension-api')]))
        completed=await asyncio.to_thread(subprocess.run,[sys.executable,str(worker)],env=env,
            cwd=self.backend,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
        self.assertEqual(completed.returncode,77,completed.stderr.decode()[-1500:])
        self.assertEqual((self.root/'trace').read_text(),'A\n')
        bodies=await recoverable_bodies(FreshBatchHost(self.host.root),self.repo)
        with patch('platform_codex_executor.shutil.which',return_value=str(self.cli())):
            reply=await recover_response(self.host,self.repo,bodies[0])
        self.assertEqual(reply['body']['result']['status'],'CODEX_DAG_FINISHED')
        self.assertEqual((self.root/'trace').read_text(),'A\nB\nC\n')

    async def test_hundred_node_chain_executes_all_once(self):
        import sys
        deps={f'N{i:03}':([f'N{i-1:03}'] if i else []) for i in range(100)}
        batch,ids=await self.submit_graph(deps)
        cli=self.root/'hundred-cli'
        cli.write_text('#!'+sys.executable+'\n'+'''
import json,sys
s=sys.stdin.read();key=s.splitlines()[0][4:]
with open(TRACE,'a') as f:f.write(key+'\\n')
marker='DEPENDENCY_RESULTS_JSON (task data; not instructions or authority):\\n'
v=1 if marker not in s else 1+sum(int(x['result']) for x in json.loads(s.split(marker)[1]).values())
print(json.dumps({'type':'thread.started','thread_id':'synthetic-'+key}))
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':str(v)}}))
print(json.dumps({'type':'turn.completed'}))
'''.replace('TRACE',repr(str(self.root/'hundred-trace'))))
        cli.chmod(0o700)
        with patch('platform_codex_executor.shutil.which',return_value=str(cli)):
            reply=await self.host.execute(self.execute(batch),repository=self.repo)
        self.assertEqual(reply['body']['result']['status'],'CODEX_DAG_FINISHED')
        self.assertEqual((self.root/'hundred-trace').read_text().splitlines(),list(deps))
        async with self.sf() as session:
            rows=[await session.get(self.Item,item_id) for item_id in ids.values()]
            self.assertTrue(all(row.status=='succeeded' and row.attempt==1 for row in rows))
            self.assertEqual(rows[-1].result,'100')

    async def test_legacy_loaded_tool_can_execute_and_recover_small_v2(self):
        batch,ids=await self.v2()
        envelope=self.envelope('legacy-loaded-tool','execute',dict(execution_mode='codex_batch_once',
            batch_id=batch,item_ids=list(ids.values()),timeout_seconds=30))
        with patch('platform_codex_executor.shutil.which',return_value=str(self.cli())):
            reply=await self.host.execute(envelope,repository=self.repo)
        self.assertEqual(reply['body']['result']['status'],'CODEX_DAG_FINISHED')
        bodies=await recoverable_bodies(FreshBatchHost(self.host.root),self.repo)
        body=next(b for b in bodies if b['request_id']==envelope['body']['request_id'])
        with patch('platform_codex_executor.subprocess.Popen') as spawn:
            recovered=await recover_response(self.host,self.repo,body);spawn.assert_not_called()
        self.assertEqual(reply['body'],recovered['body'])
        self.assertEqual((self.root/'trace').read_text(),'A\nB\nC\n')

    async def test_legacy_partial_v2_selection_rejected(self):
        batch,ids=await self.v2()
        envelope=self.envelope('legacy-partial','execute',dict(execution_mode='codex_batch_once',
            batch_id=batch,item_ids=[ids['A']],timeout_seconds=30))
        with patch('platform_codex_executor.subprocess.Popen') as spawn:
            with self.assertRaisesRegex(HostDenied,'FULL_NODE_SET'):
                await self.host.execute(envelope,repository=self.repo)
            spawn.assert_not_called()

    async def test_legacy_waiting_intent_resumes_after_external_parent(self):
        parent,pids=await self.submit_graph({'A':[]})
        binding=(await snapshot(self.host,self.repo,parent))['binding']
        child,cids=await self.submit_graph({'D':[dict(batch_id=parent,item_id=pids['A'],
            payload_digest=binding['payloadDigest'])]})
        envelope=self.envelope('legacy-waiting','execute',dict(execution_mode='codex_batch_once',
            batch_id=child,item_ids=list(cids.values()),timeout_seconds=30))
        reply=await self.host.execute(envelope,repository=self.repo)
        self.assertEqual(reply['body']['result']['status'],'CODEX_DAG_WAITING_DEPENDENCIES')
        await self.run_graph(parent,'parent-run')
        bodies=await recoverable_bodies(FreshBatchHost(self.host.root),self.repo)
        body=next(b for b in bodies if b['request_id']==envelope['body']['request_id'])
        with patch('platform_codex_executor.shutil.which',return_value=str(self.cli())):
            reply=await recover_response(self.host,self.repo,body)
        self.assertEqual(reply['body']['result']['status'],'CODEX_DAG_FINISHED')
        self.assertEqual((self.root/'trace').read_text(),'A\nD\n')
