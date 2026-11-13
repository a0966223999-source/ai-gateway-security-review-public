"""Offline transport contract tests; no Redis, callbacks, grants or Work runs."""
import copy
import hashlib
import importlib.util
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


class InboxError(Exception):
    pass


class FakeEvents:
    def __init__(self, *args, **kwargs):
        self.calls = []
        self.error = False

    async def deliver(self, inbox, delivery_id):
        self.calls.append(delivery_id)
        if self.error:
            raise OSError('reply lost after possible effect')
        return {'delivery_id': delivery_id, 'state': 'delivered', 'attempts': 1}


class FakeMcp:
    def __init__(self):
        self.tools = {}

    def tool(self, **kwargs):
        def decorate(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorate


class ResumeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'XJ_AUTH0_ALLOWED_SUBJECT': 'fixture-user',
                                           'VERCEL_ENV': 'preview'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.eid = 'a' * 64
        self.sid = 'sub_' + 'b' * 64
        self.did = self.eid + ':' + self.sid
        self.row = {'delivery_id': self.did, 'event_id': self.eid,
                    'event_name': 'gateway.continuation.ready',
                    'state': 'pending', 'attempts': 0}
        owner = 'oauth:' + hashlib.sha256(b'issuer|fixture-user').hexdigest()
        outer = self

        class Box:
            mailbox_id = 'gateway_internal_v1'

            def __init__(self):
                self.owner = owner

            def for_event(self, event):
                outer.event_scope = event
                return self

            def delivery_status(self, delivery_id):
                outer.locators.append(delivery_id)
                if outer.revoke_after_read:
                    os.environ['XJ_AUTH0_ALLOWED_SUBJECT'] = 'revoked-user'
                return {'items': copy.deepcopy(outer.rows)}

        self.box = Box()
        self.rows = [self.row]
        self.locators = []
        self.revoke_after_read = False
        modules = {'inbox': types.SimpleNamespace(InboxError=InboxError),
                   'mcp_events': types.SimpleNamespace(Events=FakeEvents)}
        source = Path(__file__).with_name('formal_transport.py')
        if not source.exists():
            source = Path(__file__).resolve().parent.parent / 'formal_transport.py'
        spec = importlib.util.spec_from_file_location('resume_under_test', source)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, modules):
            spec.loader.exec_module(module)
        self.mcp = FakeMcp()
        verifier = types.SimpleNamespace(issuer='issuer')
        self.events = module.install_formal_transport(self.mcp, lambda _: self.box,
            verifier, lambda: self.box.owner, {})
        self.call = self.mcp.tools['gateway_inbox_resume_delivery']

    async def run_resume(self, **overrides):
        args = {'mailbox_id': 'gateway_internal_v1', 'event_id': self.eid,
                'subscription_id': self.sid}
        return await self.call(**(args | overrides))

    async def test_only_selected_original_delivery_dispatched(self):
        result = await self.run_resume()
        self.assertEqual(self.events.calls, [self.did])
        self.assertEqual(self.locators, [self.did])
        self.assertEqual(self.event_scope, 'gateway.continuation.ready')
        self.assertFalse(result['new_event'])
        self.assertEqual(result['work_completion'], 'UNVERIFIED')

    async def test_already_delivered_not_sent_again(self):
        self.row.update(state='delivered', attempts=1)
        self.assertEqual((await self.run_resume())['status'], 'ALREADY_DELIVERED')
        self.assertEqual(self.events.calls, [])

    async def test_uncertain_attempt_query_only(self):
        self.row['attempts'] = 1
        self.assertEqual((await self.run_resume())['status'], 'QUERY_ONLY')
        self.assertEqual(self.events.calls, [])

    async def test_terminal_and_inflight_query_only(self):
        for state in ['terminal', 'sending']:
            self.row['state'] = state
            self.assertEqual((await self.run_resume())['status'], 'QUERY_ONLY')
        self.assertEqual(self.events.calls, [])

    async def test_missing_or_mismatched_original_denied(self):
        self.rows = []
        with self.assertRaises(InboxError):
            await self.run_resume()
        self.rows = [self.row | {'event_id': 'c' * 64}]
        with self.assertRaises(InboxError):
            await self.run_resume()
        self.assertEqual(self.events.calls, [])

    async def test_other_event_family_denied(self):
        self.row['event_name'] = 'inbox.message.created'
        with self.assertRaises(InboxError):
            await self.run_resume()

    async def test_other_mailbox_denied(self):
        with self.assertRaises(InboxError):
            await self.run_resume(mailbox_id='other')
        self.assertEqual(self.events.calls, [])

    async def test_wrong_owner_denied(self):
        self.box.owner = 'other'
        with self.assertRaises(InboxError):
            await self.run_resume()
        self.assertEqual(self.locators, [])

    async def test_revocation_during_read_blocks_effect(self):
        self.revoke_after_read = True
        with self.assertRaises(InboxError):
            await self.run_resume()
        self.assertEqual(self.events.calls, [])

    async def test_invalid_locator_rejected(self):
        with self.assertRaises(InboxError):
            await self.run_resume(subscription_id='bad')
        self.assertEqual(self.locators, [])

    async def test_exception_is_unknown_without_resend(self):
        self.events.error = True
        result = await self.run_resume()
        self.assertEqual(result['status'], 'UNKNOWN_QUERY_ONLY')
        self.assertEqual(self.events.calls, [self.did])

    async def test_missing_or_boolean_attempt_count_not_unattempted(self):
        self.row.pop('attempts')
        self.assertEqual((await self.run_resume())['status'], 'QUERY_ONLY')
        self.row['attempts'] = False
        self.assertEqual((await self.run_resume())['status'], 'QUERY_ONLY')
        self.assertEqual(self.events.calls, [])


if __name__ == '__main__':
    unittest.main()
