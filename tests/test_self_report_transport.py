"""Offline untrusted sideband contracts using the actual original-mailbox Lua."""
import copy
import hashlib
import json
import unittest

import test_mcp_installer
import test_request_store
from request_store import SCRIPT
from transport import UnknownOutcome, canonical
from self_report import parse_registration_sidebands, parse_self_report


REPORT = {'v': 1, 'kind': 'work-runtime-hints', 'observations': {
    'CODEX_THREAD_ID': 'reported-work', 'CODEX_ENVIRONMENT_ID': 'reported-runtime'}}
TARGET = {'v': 3, 'kind': 'ordinary-work', 'route': 'cloud-task', 'threadId': 'official-work'}


def invalid_reports():
    return [None, False, {}, '', '{}', 'null', '[]', '{', 'x' * 2049,
            ' ' + canonical(REPORT), json.dumps(REPORT),
            canonical(dict(REPORT, v=True)), canonical(dict(REPORT, v=1.0)),
            canonical(dict(REPORT, kind='ordinary-work')),
            canonical(dict(REPORT, authority=True)), canonical(dict(REPORT, route='cloud-task')),
            canonical(dict(REPORT, hostIdentity={'verified': True})),
            canonical(dict(REPORT, observations={})),
            canonical(dict(REPORT, observations={'CODEX_THREAD_ID': 'work', 'grant': True})),
            *[canonical(dict(REPORT, observations={'CODEX_THREAD_ID': value}))
              for value in (None, True, 1, [], {}, '', ' ', '\u0085', '\ufeff', '\ufeff \u0085 \ufeff', '\n', '\x7f', 'x' * 257, '中' * 86)],
            canonical(dict(REPORT, observations={'CODEX_THREAD_ID': 'work', 'CODEX_ENVIRONMENT_ID': ''})),
            canonical(dict(REPORT, observations={'CODEX_THREAD_ID': 'work', 'CODEX_ENVIRONMENT_ID': None})),
            canonical(REPORT).replace('"v":1}', '"v":1,"v":1}'),
            canonical(REPORT).replace('"CODEX_THREAD_ID":"reported-work"',
                                      '"CODEX_THREAD_ID":"reported-work","CODEX_THREAD_ID":"reported-work"'),
            canonical(REPORT).replace('reported-work', '\\u0072eported-work'),
            canonical(REPORT).replace('"v":1}', '"v":NaN}'),
            canonical(REPORT).replace('reported-work', '\\ud800')]


class SelfReportParserTests(unittest.TestCase):
    def test_exact_schema_and_utf8_bytes(self):
        self.assertIsNone(parse_self_report(''))
        self.assertEqual(parse_self_report(canonical(REPORT)), REPORT)
        for identifier in ('x', '中' * 85, 'é' * 128, '🫡' * 64, 'a"\\/b', 'x\u2028y'):
            value = dict(REPORT, observations={'CODEX_THREAD_ID': identifier})
            self.assertEqual(parse_self_report(canonical(value)), value)

    def test_injection_size_duplicate_noncanonical_and_unknown_keys_fail(self):
        for raw in invalid_reports():
            if raw == '':
                continue  # Empty optional tool argument means absent, not a stored report.
            with self.subTest(raw=repr(raw)), self.assertRaises(ValueError):
                parse_self_report(raw)

    def test_sidebands_register_only_and_mutually_exclusive(self):
        for method in ('read', 'save'):
            with self.assertRaisesRegex(ValueError, 'SELF_REPORT_REGISTER_ONLY'):
                parse_self_report(canonical(REPORT), method)
        with self.assertRaisesRegex(ValueError, 'MUTUALLY_EXCLUSIVE'):
            parse_registration_sidebands(canonical(TARGET), canonical(REPORT))


class SelfReportStoreTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_request_store.Tests.asyncSetUp
    dispatch = test_request_store.Tests.dispatch

    async def report_submit(self, report=REPORT, target=None):
        self.request['method'] = 'register'
        self.request['params'] = {}
        self.locator['requestSHA256'] = self.store.exact(self.request)[1]
        return await self.store.submit(self.request, self.locator, dispatch=self.dispatch,
                                       target_locator_json='' if target is None else canonical(target),
                                       self_report_json='' if report is None else canonical(report))

    def lua_args(self, raw, method='register'):
        request = dict(self.request, method=method, params={})
        request_json, sha = self.store.exact(request)
        locator = dict(self.locator, requestSHA256=sha)
        operation = hashlib.sha256((self.key + '|rpc|' + request['id']).encode()).hexdigest()
        message = canonical(locator)
        return dict(event_name='gateway.continuation.ready', operation_key=operation,
                    event_id='e' * 64, message=message,
                    digest=hashlib.sha256(canonical(['gateway.continuation.ready', operation, message]).encode()).hexdigest(),
                    epoch='offline-fixture', rpc_request=request_json, rpc_sha256=sha,
                    rpc_deadline=locator['deadline'], rpc_deadline_string=str(locator['deadline']),
                    rpc_nonce=request['id'], rpc_self_report=raw)

    async def test_exact_sideband_request_hash_deadline_and_single_key(self):
        receipt = await self.report_submit()
        before = await self.redis.get(self.key)
        row = json.loads(before)['messages'][receipt['event_id']]
        self.assertEqual(row['rpc_self_report'], canonical(REPORT))
        self.assertEqual(row['rpc_request'], canonical(self.request))
        self.assertEqual(row['rpc_request_sha256'], self.store.exact(self.request)[1])
        self.assertEqual(row['rpc_deadline'], str(self.locator['deadline']))
        self.assertEqual(row['rpc_nonce'], self.request['id'])
        self.assertEqual(row['rpc_state'], 'reserved_query_only')
        self.assertNotIn('rpc_target_locator', row)
        self.assertEqual(await self.store.read(receipt['event_id']), self.request)
        self.locator['deadline'] += 5
        again = await self.report_submit()
        self.assertTrue(again['duplicate'])
        self.assertEqual(await self.redis.get(self.key), before)
        self.assertEqual(len(self.dispatches), 1)
        self.assertEqual(await self.redis.keys('*'), [self.key])

    async def test_changed_removed_or_replaced_sideband_conflicts_without_dispatch(self):
        await self.report_submit()
        before = await self.redis.get(self.key)
        for report, target in [(None, None), (None, TARGET),
                               (dict(REPORT, observations={'CODEX_THREAD_ID': 'changed'}), None),
                               (dict(REPORT, observations={'CODEX_THREAD_ID': 'reported-work'}), None),
                               (dict(REPORT, observations={'CODEX_THREAD_ID': 'reported-work',
                                                           'CODEX_ENVIRONMENT_ID': 'other'}), None)]:
            with self.subTest(report=report), self.assertRaisesRegex(ValueError, 'rpc_operation_conflict'):
                await self.report_submit(report, target)
            self.assertEqual(await self.redis.get(self.key), before)
        self.assertEqual(len(self.dispatches), 1)

    async def test_old_rows_reject_attaching_new_sideband(self):
        for target in (None, TARGET, dict(v=2, kind='ordinary-work', threadId='old', environmentId='old-env')):
            await self.redis.flushall()  # This isolated fake store only.
            await self.report_submit(None, target)
            before = await self.redis.get(self.key)
            with self.assertRaisesRegex(ValueError, 'rpc_operation_conflict'):
                await self.report_submit()
            self.assertEqual(await self.redis.get(self.key), before)

    async def test_old_default_and_explicit_empty_argument_are_byte_compatible(self):
        receipt = await self.store.submit(self.request, self.locator, dispatch=self.dispatch)
        before = await self.redis.get(self.key)
        row = json.loads(before)['messages'][receipt['event_id']]
        self.assertNotIn('rpc_self_report', row)
        duplicate = await self.store.submit(self.request, self.locator, dispatch=self.dispatch,
                                             target_locator_json='', self_report_json='')
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(await self.redis.get(self.key), before)
        self.assertEqual(await self.store.read(receipt['event_id']), self.request)

    async def test_unknown_committed_submission_cannot_dispatch_on_replay(self):
        original = self.redis.eval
        async def uncertain(*args):
            await original(*args)
            raise ConnectionError('offline committed reply lost')
        self.redis.eval = uncertain
        with self.assertRaises(UnknownOutcome):
            await self.report_submit()
        self.redis.eval = original
        before = await self.redis.get(self.key)
        result = await self.report_submit()
        self.assertTrue(result['duplicate'])
        with self.assertRaisesRegex(ValueError, 'rpc_operation_conflict'):
            await self.report_submit(dict(REPORT, observations={'CODEX_THREAD_ID': 'changed'}))
        self.assertEqual(await self.redis.get(self.key), before)
        self.assertEqual(self.dispatches, [])

    async def test_reject_invalid_before_storage(self):
        self.request['method'] = 'register'
        self.locator['requestSHA256'] = self.store.exact(self.request)[1]
        for raw in invalid_reports():
            if raw == '':
                continue
            with self.subTest(raw=repr(raw)), self.assertRaises(ValueError):
                await self.store.submit(self.request, self.locator, dispatch=self.dispatch, self_report_json=raw)
        with self.assertRaisesRegex(ValueError, 'MUTUALLY_EXCLUSIVE'):
            await self.report_submit(REPORT, TARGET)
        self.assertIsNone(await self.redis.get(self.key))
        self.assertEqual(self.dispatches, [])

    async def test_lua_rejects_injection_noncanonical_and_read_save_directly(self):
        for raw in invalid_reports():
            with self.subTest(raw=repr(raw)):
                result = json.loads(await self.redis.eval(SCRIPT, 1, self.key, 'rpc_submit', canonical(self.lua_args(raw))))
                self.assertEqual(result, {'error': 'rpc_self_report'})
                self.assertIsNone(await self.redis.get(self.key))
        for method in ('read', 'save'):
            result = json.loads(await self.redis.eval(SCRIPT, 1, self.key, 'rpc_submit', canonical(self.lua_args(canonical(REPORT), method))))
            self.assertEqual(result, {'error': 'rpc_self_report'})
        args = self.lua_args(canonical(REPORT))
        args['rpc_target_locator'] = canonical(TARGET)
        result = json.loads(await self.redis.eval(SCRIPT, 1, self.key, 'rpc_submit', canonical(args)))
        self.assertEqual(result, {'error': 'rpc_sidebands_exclusive'})
        self.assertIsNone(await self.redis.get(self.key))

    async def test_lua_retains_canonical_unicode_quotes_and_slashes(self):
        for number, identifier in enumerate(('中' * 85, 'é' * 128, '🫡' * 64, 'a"\\/b', 'x\u2028y')):
            self.request['id'] = f'{number:064x}'
            self.locator['nonce'] = self.request['id']
            report = dict(REPORT, observations={'CODEX_THREAD_ID': identifier})
            receipt = await self.report_submit(report)
            row = json.loads(await self.redis.get(self.key))['messages'][receipt['event_id']]
            self.assertEqual(row['rpc_self_report'], canonical(report))

    async def test_stored_sideband_validation_rejects_tampering_and_two_sidebands(self):
        receipt = await self.report_submit()
        original = json.loads(await self.redis.get(self.key))
        for raw in invalid_reports():
            doc = copy.deepcopy(original)
            doc['messages'][receipt['event_id']]['rpc_self_report'] = raw
            await self.redis.set(self.key, canonical(doc))
            with self.subTest(raw=repr(raw)), self.assertRaises(ValueError):
                await self.store.read(receipt['event_id'])
        doc = copy.deepcopy(original)
        doc['messages'][receipt['event_id']]['rpc_target_locator'] = canonical(TARGET)
        await self.redis.set(self.key, canonical(doc))
        with self.assertRaisesRegex(ValueError, 'MUTUALLY_EXCLUSIVE'):
            await self.store.read(receipt['event_id'])


class SelfReportInstallerTests(test_mcp_installer.Tests):
    async def report_submit(self, **kwargs):
        self.request['method'] = 'register'
        return await self.mcp.tools['gateway_source_rpc_request'](
            'gateway_internal_v1', canonical(self.request), self_report_json=canonical(REPORT), **kwargs)

    async def test_original_read_exposes_untrusted_report_and_unchanged_context(self):
        await self.report_submit()
        result = await self.mcp.tools['gateway_source_rpc_read']('gateway_internal_v1', self.events[0])
        self.assertEqual(set(result), {'request', 'context', 'authority', 'selfReport'})
        self.assertEqual(result['selfReport'], REPORT)
        self.assertIs(result['authority'], False)
        self.assertEqual(result['context']['requestSHA256'], hashlib.sha256(canonical(self.request).encode()).hexdigest())
        self.assertNotIn('targetLocator', result)

    async def test_query_unknown_by_original_nonce_and_event_never_dispatches(self):
        self.reply = False
        outcome = await self.report_submit()
        self.assertEqual(outcome['status'], 'UNKNOWN_QUERY_ONLY')
        client = self.factory(1)
        key = self.mailbox('gateway_internal_v1').key
        before = await client.get(key)
        for locator in ({'request_id': self.request['id']}, {'event_id': self.events[0]}):
            result = await self.mcp.tools['gateway_source_rpc_query']('gateway_internal_v1', **locator)
            self.assertEqual(result['status'], 'UNKNOWN_QUERY_ONLY')
            self.assertEqual(await client.get(key), before)
        await client.aclose()
        self.assertEqual(len(self.events), 1)

    async def test_read_save_and_dual_input_fail_before_session(self):
        for method in ('read', 'save'):
            self.request['method'] = method
            with self.assertRaisesRegex(ValueError, 'SELF_REPORT_REGISTER_ONLY'):
                await self.mcp.tools['gateway_source_rpc_request']('gateway_internal_v1', canonical(self.request),
                                                                   self_report_json=canonical(REPORT))
        with self.assertRaisesRegex(ValueError, 'MUTUALLY_EXCLUSIVE'):
            await self.report_submit(target_locator_json=canonical(TARGET))
        client = self.factory(1)
        self.assertEqual(await client.keys('*'), [])
        await client.aclose()
        self.assertEqual(self.events, [])

    async def test_canonical_request_is_not_rewritten_for_report(self):
        self.request['method'] = 'register'
        for raw in (' ' + canonical(self.request), canonical(self.request).replace('"v":1}', '"v":1,"v":1}')):
            with self.assertRaises(ValueError):
                await self.mcp.tools['gateway_source_rpc_request']('gateway_internal_v1', raw,
                                                                   self_report_json=canonical(REPORT))
        self.assertEqual(self.events, [])

    async def test_report_mutation_same_nonce_cannot_dispatch_or_change_event(self):
        await self.report_submit()
        client = self.factory(1)
        key = self.mailbox('gateway_internal_v1').key
        before = await client.get(key)
        changed = dict(REPORT, observations={'CODEX_THREAD_ID': 'changed-work'})
        result = await self.mcp.tools['gateway_source_rpc_request']('gateway_internal_v1', canonical(self.request),
                                                                   self_report_json=canonical(changed))
        # The existing bounded RPC deliberately maps post-send errors to query-only.
        # The atomic store rejects this changed sideband, without re-dispatch.
        self.assertEqual(result['status'], 'UNKNOWN_QUERY_ONLY')
        self.assertEqual(await client.get(key), before)
        self.assertEqual(len(self.events), 1)
        await client.aclose()

    async def test_report_remains_bound_to_original_owner_mailbox(self):
        await self.report_submit()
        event_id = self.events[0]
        self.owner = 'different-owner'
        with self.assertRaises(ValueError):
            await self.mcp.tools['gateway_source_rpc_read']('gateway_internal_v1', event_id)
        result = await self.mcp.tools['gateway_source_rpc_query']('gateway_internal_v1', request_id=self.request['id'])
        self.assertEqual(result['status'], 'UNKNOWN_QUERY_ONLY')
        self.assertEqual(len(self.events), 1)


if __name__ == '__main__':
    unittest.main()
