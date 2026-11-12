"""Actual FastAPI and Ed25519 boundaries; Redis is an explicit transport fixture."""
import base64
import json
from pathlib import Path
import secrets
import tempfile
import time
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from fresh_batch_host import FreshBatchHost, canonical, initialize
from fresh_batch_rpc import install_fresh_batch_routes


class RedisFixture:
    def __init__(self):
        self.records = {}
        self.calls = 0
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return None
    def set(self, key, value, nx=False, ex=None):
        self.calls += 1
        if nx and key in self.records:
            return False
        self.records[key] = value
        return True
    def lpop(self, key):
        self.calls += 1
        return None


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name) / 'host'
        config = initialize(root, owner='oauth:' + 'a' * 64)
        self.host = FreshBatchHost(root)
        self.redis = RedisFixture()
        self.app = FastAPI()
        env = dict(XJ_FRESH_BATCH_HOST_ENABLED='true', XJ_FRESH_BATCH_HOST_CONFIG=json.dumps(config),
                   XJ_FRESH_BATCH_INGRESS_KEY=self.host.ingress.hex())
        install_fresh_batch_routes(self.app, env=env, connection_factory=lambda: self.redis)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def envelope(self, **updates):
        body = dict(domain='fresh-deer-batch-control-v1', generation=self.host.config['generation'],
                    action='poll', nonce=secrets.token_hex(16), expires_at=time.time() + 30, payload={})
        body.update(updates)
        return dict(body=body, signature=base64.b64encode(self.host.key.sign(canonical(body))).decode())

    def test_unauthenticated_control_never_reaches_redis(self):
        r = self.client.post('/internal/deer-batch/rpc', json={})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.redis.calls, 0)

    def test_altered_signature_and_other_generation_denied(self):
        envelope = self.envelope()
        envelope['body']['action'] = 'respond'
        self.assertEqual(self.client.post('/internal/deer-batch/rpc', json=envelope).status_code, 403)
        self.assertEqual(self.client.post('/internal/deer-batch/rpc',
            json=self.envelope(generation='b' * 32)).status_code, 403)
        self.assertEqual(self.redis.calls, 0)

    def test_signed_poll_is_once_only(self):
        envelope = self.envelope()
        first = self.client.post('/internal/deer-batch/rpc', json=envelope)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), {'request': None})
        self.assertEqual(self.client.post('/internal/deer-batch/rpc', json=envelope).status_code, 409)

    def test_expired_control_and_extra_fields_rejected(self):
        self.assertEqual(self.client.post('/internal/deer-batch/rpc',
            json=self.envelope(expires_at=time.time() - 1)).status_code, 403)
        self.assertEqual(self.client.post('/internal/deer-batch/rpc',
            json=self.envelope(owner='forged')).status_code, 403)
        self.assertEqual(self.redis.calls, 0)

    def test_default_off_has_no_host_control_route(self):
        app = FastAPI()
        install_fresh_batch_routes(app, env={})
        with TestClient(app) as client:
            self.assertEqual(client.post('/internal/deer-batch/rpc', json={}).status_code, 404)


if __name__ == '__main__':
    unittest.main()
