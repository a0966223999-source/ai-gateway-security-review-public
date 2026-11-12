"""Offline cryptographic and original-tool boundary tests; no live authority."""
import copy
import hashlib
import json
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from mcp_installer import install_rpc_tools
from rpc_trust import CutoverReplyVerifier, POLICY_ENV, PUBLIC_ENV, load_reply_verifier
from transport import READ, ReplyVerifier, SameInboxRPC, canonical, sign_reply


def public(private):
    return private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


class VerifierTests(unittest.TestCase):
    def setUp(self):
        # Generated keys are ephemeral offline fixtures, never deployed signers.
        self.old = Ed25519PrivateKey.generate()
        self.new = Ed25519PrivateKey.generate()
        self.epoch = int(time.time()) - 10
        self.context = dict(domain='source-finalize-rpc-reply-v1', owner='fixture-owner',
                            mailbox='gateway_internal_v1', operation='fixture-op',
                            nonce='a' * 64, requestSHA256='b' * 64,
                            deadline=time.time() + 30, eventId='c' * 64)
        self.message = dict(event_id='c' * 64, created_at=self.epoch - 1,
                            event_name='gateway.continuation.ready')
        self.envelope = sign_reply(self.old, self.context, {'fixture': 'saved'})
        self.policy = dict(schema='source-rpc-cutover-v1',
                           mailbox_id='gateway_internal_v1', cutover_epoch=self.epoch,
                           legacy_public_key=public(self.old).hex(), historical_replies=[{
                               'event_id': 'c' * 64,
                               'envelope_sha256': hashlib.sha256(canonical(self.envelope).encode()).hexdigest()}])
        self.verifier = self.make()

    def make(self):
        return CutoverReplyVerifier(public(self.new), canonical(self.policy))

    def test_exact_frozen_old_reply_is_query_only(self):
        result = self.verifier.verify_message(self.envelope, self.context,
                                              self.message, historical=True)
        self.assertEqual(result, {'fixture': 'saved'})
        with self.assertRaisesRegex(ValueError, 'QUERY_ONLY'):
            self.verifier.verify_message(self.envelope, self.context, self.message)
        with self.assertRaises(InvalidSignature):
            self.verifier.verify(self.envelope, self.context)

    def test_old_key_cannot_verify_new_event_even_on_query(self):
        self.message['created_at'] = self.epoch
        for historical in (False, True):
            with self.subTest(historical=historical), self.assertRaises(InvalidSignature):
                self.verifier.verify_message(self.envelope, self.context,
                                              self.message, historical=historical)

    def test_new_signer_cannot_adopt_old_event(self):
        new = sign_reply(self.new, self.context, {'fixture': 'new'})
        with self.assertRaisesRegex(ValueError, 'QUERY_ONLY'):
            self.verifier.verify_message(new, self.context, self.message)
        with self.assertRaisesRegex(ValueError, 'NOT_FROZEN'):
            self.verifier.verify_message(new, self.context, self.message, historical=True)

    def test_new_reply_uses_only_active_key(self):
        self.message['created_at'] = self.epoch
        new = sign_reply(self.new, self.context, {'fixture': 'new'})
        for historical in (False, True):
            self.assertEqual(self.verifier.verify_message(new, self.context,
                self.message, historical=historical), {'fixture': 'new'})

    def test_modified_old_reply_and_unlisted_event_rejected(self):
        changed = sign_reply(self.old, self.context, {'fixture': 'changed'})
        with self.assertRaisesRegex(ValueError, 'NOT_FROZEN'):
            self.verifier.verify_message(changed, self.context, self.message, historical=True)
        self.verifier.historical.clear()
        with self.assertRaisesRegex(ValueError, 'NOT_FROZEN'):
            self.verifier.verify_message(self.envelope, self.context, self.message, historical=True)

    def test_frozen_hash_does_not_replace_signature_check(self):
        forged = sign_reply(self.new, self.context, {'fixture': 'forged'})
        self.policy['historical_replies'][0]['envelope_sha256'] = hashlib.sha256(canonical(forged).encode()).hexdigest()
        with self.assertRaises(InvalidSignature):
            self.make().verify_message(forged, self.context, self.message, historical=True)

    def test_reply_cannot_be_moved_to_another_context(self):
        for field in ('owner', 'nonce', 'requestSHA256'):
            wrong = dict(self.context, **{field: 'different'})
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'BINDING'):
                self.verifier.verify_message(self.envelope, wrong, self.message, historical=True)

    def test_event_payload_timestamp_is_not_an_authority(self):
        for created in (None, True, 1.0, '1', -1, int(time.time()) + 100):
            msg = dict(self.message, created_at=created)
            msg['message'] = canonical({'created_at': self.epoch})
            with self.subTest(created=created), self.assertRaisesRegex(ValueError, 'EVENT_TIME'):
                self.verifier.verify_message(self.envelope, self.context, msg, historical=True)

    def test_future_cutover_cannot_become_active(self):
        self.policy['cutover_epoch'] = int(time.time()) + 100
        with self.assertRaisesRegex(ValueError, 'NOT_REACHED'):
            self.make().require_active()

    def test_policy_rejects_same_key_duplicates_wrong_mailbox_and_bounds(self):
        invalid = []
        p = copy.deepcopy(self.policy); p['legacy_public_key'] = public(self.new).hex(); invalid.append(p)
        p = copy.deepcopy(self.policy); p['mailbox_id'] = 'another'; invalid.append(p)
        p = copy.deepcopy(self.policy); p['historical_replies'] *= 2; invalid.append(p)
        p = copy.deepcopy(self.policy); p['cutover_epoch'] = True; invalid.append(p)
        p = copy.deepcopy(self.policy); p['historical_replies'] *= 101; invalid.append(p)
        for policy in invalid:
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                CutoverReplyVerifier(public(self.new), canonical(policy))
        with self.assertRaisesRegex(ValueError, 'DUPLICATE'):
            CutoverReplyVerifier(public(self.new), '{"schema":1,"schema":2}')

    def test_absent_policy_preserves_single_key_verifier(self):
        self.assertIs(type(load_reply_verifier((public(self.old).hex(), ''))), ReplyVerifier)


class ReadOnlyRedis:
    """A disclosed saved-record fixture; mutation is a failing test, not emulated."""
    def __init__(self, record):
        self.raw = canonical(record)
        self.closed = False
        self.reads = 0

    async def eval(self, script, count, key, event):
        if script != READ:
            raise AssertionError('Unexpected Redis mutation')
        self.reads += 1
        row = json.loads(self.raw)['messages'].get(event)
        return canonical(row) if row else None

    async def get(self, key):
        self.reads += 1
        return self.raw

    async def aclose(self):
        self.closed = True


class MCP:
    def __init__(self):
        self.tools = {}

    def tool(self, **metadata):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


class OriginalToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.old = Ed25519PrivateKey.generate(); self.new = Ed25519PrivateKey.generate()
        self.epoch = int(time.time()) - 5
        self.owner = 'fixture-owner'; self.event = 'c' * 64; self.key = 'fixture-original-mailbox'
        self.request = dict(v=1, id='a' * 64, source='fixture', method='read', params={})
        sha = hashlib.sha256(canonical(self.request).encode()).hexdigest()
        op = hashlib.sha256((self.key + '|rpc|' + self.request['id']).encode()).hexdigest()
        self.deadline = time.time() + 20
        self.context = dict(domain='source-finalize-rpc-reply-v1', owner=self.owner,
            mailbox='gateway_internal_v1', operation=op, nonce=self.request['id'],
            requestSHA256=sha, deadline=self.deadline, eventId=self.event)
        self.envelope = sign_reply(self.old, self.context, {'fixture': 'saved'})
        locator = dict(kind='source-finalize-rpc-v1', nonce=self.request['id'],
                       requestSHA256=sha, deadline=self.deadline)
        row = dict(event_id=self.event, event_name='gateway.continuation.ready', operation_key=op,
                   created_at=self.epoch-1, message=canonical(locator), rpc_request=canonical(self.request),
                   rpc_request_sha256=sha, rpc_nonce=self.request['id'], rpc_deadline=self.deadline,
                   rpc_state='reserved_query_only', rpc_response=canonical(self.envelope))
        self.record = dict(messages={self.event: row})
        self.client = ReadOnlyRedis(self.record)
        self.policy = dict(schema='source-rpc-cutover-v1', mailbox_id='gateway_internal_v1',
            cutover_epoch=self.epoch, legacy_public_key=public(self.old).hex(), historical_replies=[{
                'event_id':self.event, 'envelope_sha256':hashlib.sha256(canonical(self.envelope).encode()).hexdigest()}])
        self.environment = patch.dict(os.environ, {
            PUBLIC_ENV: public(self.new).hex(), POLICY_ENV: canonical(self.policy)})
        self.environment.start(); self.addCleanup(self.environment.stop)
        self.mcp = MCP(); self.dispatched = []
        def mailbox(mid):
            box = SimpleNamespace(owner=self.owner, mailbox_id=mid, key=self.key)
            box.for_event = lambda name: box
            return box
        self.mailbox = mailbox
        async def dispatch(*args):
            self.dispatched.append(args)
            raise AssertionError('Unexpected dispatch')
        self.formal = SimpleNamespace(dispatch=dispatch)
        self.config = dict(budgets_verified=True, max_wait_seconds=1,
                           function_budget_seconds=20, connector_budget_seconds=20)
        result = self.install()
        self.assertTrue(result['enabled'])

    def install(self):
        return install_rpc_tools(self.mcp, self.mailbox, self.formal,
            lambda:self.owner, {}, host_config=self.config, connection_factory=lambda wait:self.client)

    async def test_query_original_saved_old_reply_without_mutation(self):
        result = await self.mcp.tools['gateway_source_rpc_query']('gateway_internal_v1', event_id=self.event)
        self.assertEqual(result['status'], 'verified_historical_host_response')
        self.assertTrue(result['historical_only'])
        self.assertFalse(result['authority'])
        self.assertEqual(result['result'], {'fixture':'saved'})
        self.assertEqual(self.client.raw, canonical(self.record))
        self.assertEqual(self.dispatched, [])

    async def test_old_unknown_stays_unknown_no_dispatch(self):
        del self.record['messages'][self.event]['rpc_response']
        self.client.raw = canonical(self.record)
        before = self.client.raw
        result = await self.mcp.tools['gateway_source_rpc_query']('gateway_internal_v1', request_id=self.request['id'])
        self.assertEqual(result['status'], 'UNKNOWN_QUERY_ONLY')
        self.assertEqual(self.client.raw, before)
        self.assertEqual(self.dispatched, [])

    async def test_old_request_cannot_be_read_for_continuation_or_responded_to(self):
        with self.assertRaisesRegex(ValueError, 'QUERY_ONLY'):
            await self.mcp.tools['gateway_source_rpc_read']('gateway_internal_v1', self.event)
        new = sign_reply(self.new, self.context, {'fixture':'replacement'})
        with self.assertRaisesRegex(ValueError, 'QUERY_ONLY'):
            await self.mcp.tools['gateway_source_rpc_respond']('gateway_internal_v1', self.event, canonical(new))
        self.assertEqual(self.client.raw, canonical(self.record))

    async def test_changed_policy_revokes_installed_session_before_access(self):
        os.environ[POLICY_ENV] = canonical(dict(self.policy, historical_replies=[]))
        with self.assertRaisesRegex(ValueError, 'TRUST_CONFIG_CHANGED'):
            await self.mcp.tools['gateway_source_rpc_query']('gateway_internal_v1', event_id=self.event)
        self.assertEqual(self.client.reads, 0)

    def test_malformed_policy_disables_rpc_only_not_old_tools(self):
        self.mcp = MCP(); self.mcp.tools['existing_notification_tool'] = 'retained'
        os.environ[POLICY_ENV] = '{}'
        result = self.install()
        self.assertFalse(result['enabled'])
        self.assertEqual(self.mcp.tools, {'existing_notification_tool':'retained'})

    async def test_direct_transport_cannot_commit_new_signature_to_old_event(self):
        verifier = CutoverReplyVerifier(public(self.new), canonical(self.policy))
        rpc = SameInboxRPC(redis=self.client, inbox_key=self.key, owner=self.owner,
            mailbox='gateway_internal_v1', verifier=verifier, current=lambda:True,
            max_wait_seconds=1, function_budget_seconds=20, connector_budget_seconds=20)
        new = sign_reply(self.new, self.context, {'fixture':'replacement'})
        with self.assertRaisesRegex(ValueError, 'QUERY_ONLY'):
            await rpc.commit(envelope=new, context=self.context)
        self.assertEqual(self.client.raw, canonical(self.record))


if __name__ == '__main__':
    unittest.main()
