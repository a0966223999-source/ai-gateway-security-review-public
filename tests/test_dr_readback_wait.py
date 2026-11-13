"""Signed transport fixtures; no live Redis, consent or model execution."""
import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from mcp.server.fastmcp import FastMCP

from deer_management_rpc import install_management_rpc_tools
from fresh_batch_host import FreshBatchHost, canonical, digest, initialize
from fresh_batch_rpc import BatchRPC


class RedisFixture:
    def __init__(self, host, *, ready_at=4, early_notice=False, tamper=False):
        self.host = host
        self.ready_at = ready_at
        self.early_notice = early_notice
        self.tamper = tamper
        self.now = 0
        self.enqueues = 0
        self.body = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def pubsub(self):
        return self

    def subscribe(self, channel):
        self.channel = channel

    def get_message(self, *, timeout, ignore_subscribe_messages=False):
        if not ignore_subscribe_messages:
            return {'type': 'subscribe'}
        if self.early_notice:
            self.early_notice = False
            self.now += .25
            return {'type': 'message', 'data': 'untrusted hint'}
        self.now += timeout
        # Simulate a lost notification even though the result was saved.
        return None

    def eval(self, script, count, key, queue, payload_digest, raw, expires):
        self.enqueues += 1
        self.body = json.loads(raw)['envelope']['body']
        return raw

    def get(self, key):
        if self.body is None or self.now < self.ready_at:
            return None
        body = dict(generation=self.host.config['generation'],
                    request_id=self.body['request_id'], request_sha256=digest(self.body),
                    automatic_dispatch=False, native_execution_verified=False,
                    result={'kind': 'original-deer-agent-management-v1',
                            'status': 'REGISTRY_READBACK', 'agent': {'soul': 'fixture'}})
        signature = base64.b64encode(self.host.key.sign(canonical(body))).decode()
        if self.tamper:
            body['result']['agent']['soul'] = 'altered'
        return canonical({'body': body, 'signature': signature})


class TransportWaitTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        initialize(Path(self.temp.name) / 'host', owner='oauth:' + 'a' * 64,
                   platform_service=True)
        self.host = FreshBatchHost(Path(self.temp.name) / 'host')

    def call(self, redis, timeout=12):
        rpc = BatchRPC(config=self.host.config, ingress_key=self.host.ingress,
                       connection_factory=lambda: redis)
        with patch('fresh_batch_rpc.time.monotonic', side_effect=lambda: redis.now):
            return rpc.call(owner=self.host.config['owner'],
                            parent=self.host.config['parent_id'], action='read',
                            operation_key='one-original-read', payload={'kind': 'read'},
                            response_timeout=timeout)

    def test_late_saved_result_survives_lost_notification(self):
        redis = RedisFixture(self.host)
        self.assertEqual(self.call(redis)['result']['status'], 'REGISTRY_READBACK')
        self.assertEqual(redis.enqueues, 1)

    def test_untrusted_early_notice_does_not_end_original_wait(self):
        redis = RedisFixture(self.host, early_notice=True)
        self.assertEqual(self.call(redis)['result']['agent']['soul'], 'fixture')
        self.assertEqual(redis.enqueues, 1)
        self.assertLessEqual(redis.now, 12)

    def test_missing_response_stays_unknown_without_reenqueue(self):
        redis = RedisFixture(self.host, ready_at=100)
        self.assertEqual(self.call(redis)['status'], 'PENDING_OR_UNKNOWN_QUERY_SAME_KEY')
        self.assertEqual(redis.enqueues, 1)
        self.assertEqual(redis.now, 12)

    def test_default_short_wait_does_not_claim_late_success(self):
        redis = RedisFixture(self.host)
        self.assertEqual(self.call(redis, 3)['status'], 'PENDING_OR_UNKNOWN_QUERY_SAME_KEY')

    def test_tampered_late_response_is_not_returned(self):
        redis = RedisFixture(self.host, tamper=True)
        from cryptography.exceptions import InvalidSignature
        with self.assertRaises(InvalidSignature):
            self.call(redis)

    def test_invalid_wait_never_enqueues(self):
        for timeout in (0, -1, 16, True, float('nan'), float('inf')):
            redis = RedisFixture(self.host)
            with self.assertRaisesRegex(ValueError, 'WAIT_INVALID'):
                self.call(redis, timeout)
            self.assertEqual(redis.enqueues, 0)


class ManagementWaitTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_full_read_gets_longer_wait_and_history_stays_private(self):
        effects = []
        cached = False

        def query(**kwargs):
            if cached:
                return {'request_id': 'fixture', 'result': {
                    'kind': 'original-deer-agent-management-v1',
                    'status': 'REGISTRY_READBACK', 'agent': {'soul': 'private-fixture'}}}
            return {'status': 'NOT_FOUND_QUERY_ONLY'}

        def call(**kwargs):
            effects.append(kwargs)
            return {'request_id': 'fixture', 'result': {
                'kind': 'original-deer-agent-management-v1', 'status': 'fixture'}}

        rpc = SimpleNamespace(config={'actions': ['read', 'execute'], 'parent_id': 'parent'},
                              query=query, call=call)
        mcp = FastMCP('isolated-readback')
        install_management_rpc_tools(mcp, rpc=rpc, principal=lambda: 'owner',
                                     oauth_meta={}, enabled=True)
        for kind, extra in (('read', {'name': 'qa-role'}), ('list', {}),
                            ('create', {'name': 'qa-role', 'soul': 'fixture'})):
            await mcp.call_tool('agent_manage', {'request': {
                'kind': kind, 'operation_key': kind, **extra}})
        self.assertEqual([effect['response_timeout'] for effect in effects], [12, 3, 3])
        cached = True
        reply = await mcp.call_tool('agent_management_receipt',
                                    {'kind': 'read', 'operation_key': 'read'})
        self.assertIn('historical_only', str(reply))
        self.assertNotIn('private-fixture', str(reply))
        self.assertEqual(len(effects), 3)


if __name__ == '__main__':
    unittest.main()
