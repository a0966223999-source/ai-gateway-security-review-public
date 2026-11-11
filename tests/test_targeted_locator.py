"""Offline integration: fakeredis executes the actual mailbox Lua via lupa."""
import copy
import json
import unittest
import test_mcp_installer
import test_request_store
from targeted_locator import parse_target_locator, validate_target_locator
from transport import canonical

TARGET = {'v': 2, 'kind': 'ordinary-work', 'threadId': 'thread-fixture', 'environmentId': 'environment-fixture'}


class ValidatorTests(unittest.TestCase):
    def test_exact_canonical(self):
        self.assertEqual(parse_target_locator(canonical(TARGET)), TARGET)
        self.assertIsNone(parse_target_locator(''))
        invalid = [None, '{}', 'null', json.dumps(TARGET), canonical(dict(TARGET, v=True)),
                   canonical(dict(TARGET, v=2.0)), canonical(dict(TARGET, extra=1)),
                   canonical(dict(TARGET, environmentId='')), canonical(dict(TARGET, environmentId=None)),
                   canonical(dict(TARGET, threadId='x' * 257)), canonical(dict(TARGET, threadId='\n')),
                   canonical(dict(TARGET, environmentId=' ')),
                   canonical(dict(TARGET, threadId='中' * 86)),
                   '{"environmentId":"e","kind":"ordinary-work","threadId":"t","v":2,"v":2}']
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_target_locator(raw)
        for method in ['read', 'save']:
            with self.assertRaisesRegex(ValueError, 'REGISTER_ONLY'):
                parse_target_locator(canonical(TARGET), method)


class TargetStoreTests(test_request_store.Tests):
    async def target_submit(self, target=TARGET):
        self.request['method'] = 'register'
        self.locator['requestSHA256'] = self.store.exact(self.request)[1]
        return await self.store.submit(self.request, self.locator, dispatch=self.dispatch,
                                      target_locator_json='' if target is None else canonical(target))

    async def test_target_preserved_and_conflicts(self):
        receipt = await self.target_submit()
        before = await self.redis.get(self.key)
        event = json.loads(before)['messages'][receipt['event_id']]
        self.assertEqual(event['rpc_target_locator'], canonical(TARGET))
        self.assertEqual(event['rpc_request'], canonical(self.request))
        self.assertEqual(event['rpc_nonce'], self.request['id'])
        self.assertEqual(event['rpc_request_sha256'], self.locator['requestSHA256'])
        self.locator['deadline'] += 1
        duplicate = await self.target_submit()
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(await self.redis.get(self.key), before)
        for target in [dict(TARGET, threadId='other'), dict(TARGET, environmentId='other'), None]:
            with self.assertRaisesRegex(ValueError, 'rpc_operation_conflict'):
                await self.target_submit(target)
            self.assertEqual(await self.redis.get(self.key), before)
        self.assertEqual(len(self.dispatches), 1)
        self.assertEqual(await self.redis.keys('*'), [self.key])

    async def test_legacy_to_target_conflicts(self):
        await self.target_submit(None)
        before = await self.redis.get(self.key)
        with self.assertRaisesRegex(ValueError, 'rpc_operation_conflict'):
            await self.target_submit()
        self.assertEqual(await self.redis.get(self.key), before)

    async def test_invalid_never_submitted(self):
        for method, target in [('register', dict(TARGET, environmentId='')), ('save', TARGET), ('read', TARGET)]:
            self.request['method'] = method
            self.locator['requestSHA256'] = self.store.exact(self.request)[1]
            with self.assertRaises(ValueError):
                await self.store.submit(self.request, self.locator, dispatch=self.dispatch,
                                        target_locator_json=canonical(target))
        self.assertIsNone(await self.redis.get(self.key))
        self.assertEqual(self.dispatches, [])

    async def test_stored_locator_tampering_fails_read(self):
        receipt = await self.target_submit()
        doc = json.loads(await self.redis.get(self.key))
        doc['messages'][receipt['event_id']]['rpc_target_locator'] = '{}'
        await self.redis.set(self.key, canonical(doc))
        with self.assertRaises(ValueError):
            await self.store.read(receipt['event_id'])


class TargetInstallerTests(test_mcp_installer.Tests):
    async def test_public_optional_locator_and_exact_legacy_response(self):
        await self.request_tool()
        old = await self.mcp.tools['gateway_source_rpc_read']('gateway_internal_v1', self.events[0])
        self.assertEqual(set(old), {'request', 'context', 'authority'})
        self.request['id'] = 'b' * 64
        self.request['method'] = 'register'
        await self.mcp.tools['gateway_source_rpc_request']('gateway_internal_v1', canonical(self.request), canonical(TARGET))
        new = await self.mcp.tools['gateway_source_rpc_read']('gateway_internal_v1', self.events[-1])
        self.assertEqual(new['targetLocator'], TARGET)
        self.assertEqual(new['request'], self.request)
        self.assertEqual(set(new), {'request', 'context', 'authority', 'targetLocator'})

    async def test_invalid_locator_rejected_before_session(self):
        self.request['method'] = 'register'
        with self.assertRaises(ValueError):
            await self.mcp.tools['gateway_source_rpc_request']('gateway_internal_v1', canonical(self.request), canonical(dict(TARGET, environmentId='')))
        self.assertEqual(self.events, [])
        client = self.factory(1)
        try:
            self.assertEqual(await client.keys('*'), [])
        finally:
            await client.aclose()
