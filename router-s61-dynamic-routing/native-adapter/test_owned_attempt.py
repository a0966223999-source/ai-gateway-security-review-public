"""Full original pre-launch executor functions; fake SQL/authority/Popen.

This never invokes Codex or asserts production authority/Stop/SQL acceptance.
"""
import ast
import asyncio
import hashlib
import json
from pathlib import Path
import secrets
import sys
import tempfile
from datetime import datetime, timezone
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from owned_attempt import OwnedCodexAttempt, AttemptQueryOnly

candidate = ast.parse(Path(sys.argv.pop(1)).read_text())
functions = ast.fix_missing_locations(ast.Module(body=[n for n in candidate.body
    if isinstance(n, ast.AsyncFunctionDef) and n.name in ('execute_item', '_execute_item')], type_ignores=[]))


class Denied(RuntimeError):
    pass


class LaunchObserved(RuntimeError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


class OwnedAttemptTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.binding = dict(taskRef='task', version='1', operationRef='op',
            ownerRef='owner', sourceRevision='1', sourceDigest='a'*64, payloadDigest='b'*64)
        self.prompt = '705+98'
        self.expected = dict(batch_id='batch', item_id='item', binding=self.binding,
            prompt_sha256=sha(self.prompt.encode()), dependency_input_sha256=sha(canonical(None)))
        self.current = True
        self.send = True
        self.reservations = 0
        self.launches = 0
        self.stops = 0
        self.reads = 0
        self.request = SimpleNamespace(batch_id='batch', item_id='item', operation_key='original-execute-key', timeout_seconds=1)
        self.host = SimpleNamespace(root=Path(self.temp.name), config={'owner':'owner'},
            check=self.host_check, binding=lambda action:{k:v for k,v in self.binding.items() if k!='payloadDigest'})
        batch_type, item_type = type('Batch', (), {}), type('Item', (), {})
        batch = SimpleNamespace(user_id='owner', execution_spec={'native_control':{'binding':self.binding}})
        item = SimpleNamespace(batch_id='batch', status='pending', prompt=self.prompt)
        async def get(cls, key): return batch if cls is batch_type else item
        class Session:
            async def __aenter__(self): return SimpleNamespace(get=get)
            async def __aexit__(self, *args): pass
        async def get_batch(*args, **kwargs): return batch
        async def reserve(*args, **kwargs):
            self.reservations += 1
            return {'sendAllowed':self.send}
        self.repo = SimpleNamespace(_sf=Session, get_bound_native_batch=get_batch,
            _native_control=lambda *args, **kwargs:None, reserve_native_attempt=reserve)
        models = ModuleType('deerflow.persistence.subagent_batches.model')
        models.SubagentBatchRow, models.SubagentBatchItemRow = batch_type, item_type
        roles = ModuleType('deer_fixed_runtime_host')
        roles.OriginalFixedAgentLease = type('Lease', (), {})
        dag = ModuleType('platform_codex_dag')
        async def dependency_inputs(*args): return None
        dag.dependency_inputs = dependency_inputs
        self.modules = patch.dict(sys.modules, {models.__name__:models, roles.__name__:roles, dag.__name__:dag})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        def popen(*args, **kwargs):
            self.launches += 1
            raise LaunchObserved('Popen boundary observed; no child created')
        def write(path, data):
            with path.open('xb') as stream: stream.write(data)
        scope = dict(asyncio=asyncio, datetime=datetime, timezone=timezone,
            json=json, secrets=secrets, time=__import__('time'), os=__import__('os'),
            shutil=SimpleNamespace(which=lambda name:'/fixture/codex'),
            subprocess=SimpleNamespace(Popen=popen, PIPE=-1),
            canonical=canonical, sha=sha, exclusive_write=write, HostDenied=Denied)
        exec(compile(functions, '<full-original-executor-functions>', 'exec'), scope)
        async def execute(gate):
            return await scope['execute_item'](self.host, self.repo, self.request, native_start_gate=gate)
        async def current(action, expected): return self.current
        async def read(expected):
            self.reads += 1
            return {'fixture_original_sql':'running', 'attempt':self.reservations}
        async def stop(expected):
            self.stops += 1
            return {'status':'STOP_NOT_OWNED_QUERY_ONLY', 'stopped_by_this_request':False}
        self.factory = lambda expected=None:OwnedCodexAttempt(expected=expected or self.expected,
            execute_owned=execute, check_current=current, read_original=read, stop_owned=stop)
        self.attempt = self.factory()

    def host_check(self, action):
        if not self.current: raise Denied('host revoked')

    async def asyncTearDown(self):
        await self.attempt.stop()

    async def test_real_executor_waits_for_exact_release(self):
        ready = await self.attempt.reserve()
        self.assertEqual((self.reservations,self.launches,self.host._codex_execution_count), (1,0,1))
        released = await self.attempt.release(ready['context_digest'])
        self.assertFalse(released['native_started'])
        with self.assertRaises(LaunchObserved): await self.attempt.wait()
        self.assertEqual((self.reservations,self.launches,self.host._codex_execution_count), (1,1,0))

    async def test_stop_waiting_cancels_owned_task_without_fabricating_sql_terminal(self):
        await self.attempt.reserve()
        result = await self.attempt.stop()
        self.assertFalse(result['original_sql_terminal_state_verified'])
        self.assertEqual((self.reservations,self.launches,self.host._codex_execution_count),(1,0,0))
        await self.attempt.stop()
        self.assertEqual(self.stops,0)

    async def test_revoked_release_cancels_and_never_relaunches(self):
        ready = await self.attempt.reserve()
        self.current = False
        with self.assertRaisesRegex(AttemptQueryOnly,'NOT_CURRENT'):
            await self.attempt.release(ready['context_digest'])
        with self.assertRaises(AttemptQueryOnly): await self.attempt.reserve()
        self.assertEqual((self.reservations,self.launches), (1,0))

    async def test_changed_prompt_mapping_refuses_after_native_reservation(self):
        wrong = dict(self.expected, prompt_sha256='c'*64)
        self.attempt = self.factory(wrong)
        with self.assertRaisesRegex(AttemptQueryOnly,'MAPPING_CHANGED'): await self.attempt.reserve()
        with self.assertRaises(AttemptQueryOnly): await self.attempt.reserve()
        self.assertEqual((self.reservations,self.launches), (1,0))

    async def test_unknown_native_fence_does_not_retry(self):
        self.send = False
        with self.assertRaisesRegex(Denied,'ATTEMPT_QUERY_ONLY'): await self.attempt.reserve()
        with self.assertRaises(Denied): await self.attempt.reserve()
        self.assertEqual((self.reservations,self.launches), (1,0))
        self.assertEqual((await self.attempt.query())['attempt'],1)

    async def test_original_gate_timeout_keeps_once_and_prevents_new_task(self):
        self.request.timeout_seconds = 0.02
        await self.attempt.reserve()
        with self.assertRaises(TimeoutError): await self.attempt.wait()
        with self.assertRaises(AttemptQueryOnly): await self.attempt.reserve()
        self.assertEqual((self.reservations,self.launches), (1,0))

    async def test_two_releases_share_one_original_task(self):
        ready = await self.attempt.reserve()
        results = await asyncio.gather(*[self.attempt.release(ready['context_digest']) for _ in range(2)])
        self.assertEqual(sorted(r['status'] for r in results), ['query-only','released-await-original-ack'])
        with self.assertRaises(LaunchObserved): await self.attempt.wait()
        self.assertEqual((self.reservations,self.launches),(1,1))

    async def test_stop_release_race_never_claims_native_stop(self):
        ready = await self.attempt.reserve()
        await self.attempt.release(ready['context_digest'])
        result = await self.attempt.stop()
        self.assertFalse(result['stopped_by_this_request'])
        # Closure wins before the gate returns, so no ActiveRun ever exists.
        self.assertEqual((self.launches,self.stops,self.host._codex_execution_count),(0,0,0))

    async def test_read_revocation_does_not_return_observation(self):
        async def read(expected):
            self.current = False
            return {'result':'not-authorized-after-await'}
        self.attempt._read = read
        with self.assertRaises(AttemptQueryOnly): await self.attempt.query()


unittest.main()
