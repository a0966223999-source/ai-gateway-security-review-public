"""Exercise the staged executor's actual reservation-to-launch boundary.

SQL, host authority and CLI are fixtures. No native dispatch or QA claim.
"""
import ast
import asyncio
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

candidate = ast.parse(Path(sys.argv.pop(1)).read_text())
inner = next(n for n in candidate.body if isinstance(n, ast.AsyncFunctionDef) and n.name == '_execute_item')
first = next(i for i, n in enumerate(inner.body) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'reserved' for t in n.targets))
last = next(i for i, n in enumerate(inner.body) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'cmd' for t in n.targets))
boundary = ast.AsyncFunctionDef(name='boundary', args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]), body=copy.deepcopy(inner.body[first:last]) + [ast.Return(ast.Constant('launch-boundary-reached'))], decorator_list=[])
module = ast.fix_missing_locations(ast.Module(body=[boundary], type_ignores=[]))


class Denied(Exception):
    pass


class GateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events = []
        self.allowed = True
        self.sent = True
        self.calls = 0
        async def reserve(*args, **kwargs):
            self.calls += 1
            self.events.append('reserve')
            return {'sendAllowed': self.sent}
        def check(action):
            self.events.append('authority')
            if not self.allowed:
                raise Denied('revoked')
        async def runtime():
            self.events.append('role')
        self.scope = dict(asyncio=asyncio, json=json, canonical=lambda v: json.dumps(v, sort_keys=True).encode(), sha=lambda b: hashlib.sha256(b).hexdigest(), repository=SimpleNamespace(reserve_native_attempt=reserve), request=SimpleNamespace(batch_id='batch', item_id='item', timeout_seconds=0.02), owner='owner', binding={'payloadDigest':'a'*64}, intent='intent', now=lambda:0, run=Path('/fixture'), exclusive_write=lambda *args:self.events.append('reservation-saved'), HostDenied=Denied, host=SimpleNamespace(check=check), check_runtime=runtime, prompt='705+98', dependency_data=None, native_start_gate=None)
        exec(compile(module, '<actual-executor-boundary>', 'exec'), self.scope)

    async def test_waits_after_original_reservation(self):
        reached, release = asyncio.Event(), asyncio.Event()
        async def gate(ctx):
            self.assertEqual(ctx['intent_id'], 'intent')
            self.assertEqual(ctx['prompt_sha256'], hashlib.sha256(b'705+98').hexdigest())
            ctx['binding']['payloadDigest'] = 'mutated-copy'
            reached.set()
            await release.wait()
            return True
        self.scope['native_start_gate'] = gate
        self.scope['request'].timeout_seconds = 1
        task = asyncio.create_task(self.scope['boundary']())
        await reached.wait()
        self.assertFalse(task.done())
        self.assertEqual(self.events, ['reserve', 'reservation-saved'])
        release.set()
        self.assertEqual(await task, 'launch-boundary-reached')
        self.assertEqual(self.scope['binding']['payloadDigest'], 'a'*64)

    async def test_refusal_is_query_only(self):
        async def gate(ctx): return False
        self.scope['native_start_gate'] = gate
        with self.assertRaisesRegex(Denied, 'S61_START_REFUSED_QUERY_ONLY'):
            await self.scope['boundary']()
        self.assertEqual(self.calls, 1)

    async def test_unknown_native_reservation_never_calls_gate(self):
        self.sent = False
        async def gate(ctx): self.fail('must not call gate')
        self.scope['native_start_gate'] = gate
        with self.assertRaisesRegex(Denied, 'CODEX_ATTEMPT_QUERY_ONLY'):
            await self.scope['boundary']()

    async def test_revocation_after_gate_refuses_launch(self):
        async def gate(ctx):
            self.allowed = False
            return True
        self.scope['native_start_gate'] = gate
        with self.assertRaisesRegex(Denied, 'revoked'):
            await self.scope['boundary']()

    async def test_timeout_does_not_reinvoke_or_reach_launch(self):
        async def gate(ctx): await asyncio.Event().wait()
        self.scope['native_start_gate'] = gate
        with self.assertRaises(TimeoutError): await self.scope['boundary']()
        self.assertEqual(self.calls, 1)

    async def test_ordinary_path_keeps_original_boundary(self):
        self.assertEqual(await self.scope['boundary'](), 'launch-boundary-reached')
        self.assertEqual(self.events, ['reserve', 'reservation-saved', 'authority', 'role'])

    async def test_capacity_slot_released_on_gate_cancellation(self):
        outer = copy.deepcopy(next(n for n in candidate.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'execute_item'))
        tree = ast.fix_missing_locations(ast.Module(body=[outer], type_ignores=[]))
        entered = asyncio.Event()
        async def waiting(*args, **kwargs):
            self.assertIs(kwargs['native_start_gate'], self)
            entered.set()
            await asyncio.Event().wait()
        scope = dict(_execute_item=waiting, HostDenied=Denied)
        exec(compile(tree, '<actual-capacity-wrapper>', 'exec'), scope)
        host = SimpleNamespace()
        task = asyncio.create_task(scope['execute_item'](host, None, None, native_start_gate=self))
        await entered.wait()
        self.assertEqual(host._codex_execution_count, 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(host._codex_execution_count, 0)


unittest.main()
