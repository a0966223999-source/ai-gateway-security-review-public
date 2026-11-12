"""Fresh enrollment/fences on real files and real Ed25519 keys, no live grants."""
import base64
import hmac
import json
from pathlib import Path
import tempfile
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from fresh_batch_host import FreshBatchHost, HostDenied, canonical, digest, initialize
from fresh_batch_rpc import BatchRPC, prefix, request_id


class FreshHostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'new-host'
        self.owner = 'oauth:' + 'a' * 64
        self.config = initialize(self.root, owner=self.owner, now=100, ttl_seconds=3600)
        self.host = FreshBatchHost(self.root, clock=lambda: 200)

    def signed(self, **changes):
        body = dict(generation=self.config['generation'], owner=self.owner,
                    parent_id=self.config['parent_id'], action='submit', operation_key='new-operation',
                    payload={'title': 'synthetic'}, expires_at=260)
        body.update(changes)
        body['request_id'] = request_id(self.config, body['action'], body['operation_key'])
        return {'body': body, 'mac': hmac.new(self.host.ingress, canonical(body), 'sha256').hexdigest()}

    def test_fresh_private_generation_does_not_overwrite_old_state(self):
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        for name in ['host.json', 'config.sha256', 'signing.key', 'ingress.key']:
            self.assertEqual((self.root / name).stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            initialize(self.root, owner=self.owner)
        other = initialize(Path(self.temp.name) / 'other', owner=self.owner, now=100)
        self.assertNotEqual(other['generation'], self.config['generation'])
        self.assertNotEqual(other['public_key'], self.config['public_key'])

    def test_restart_preserves_durable_attempt_no_replay(self):
        body = self.host.verify_request(self.signed())
        self.host.reserve(body)
        resumed = FreshBatchHost(self.root, clock=lambda: 200)
        with self.assertRaisesRegex(HostDenied, 'ATTEMPTED_QUERY_ONLY'):
            resumed.reserve(body)
        self.assertEqual(json.loads((self.root / 'attempts' / body['request_id']).read_bytes()), body)

    def test_stop_blocks_existing_instance_and_restart(self):
        (self.root / 'STOP').touch(mode=0o600)
        with self.assertRaises(HostDenied):
            self.host.check('submit')
        with self.assertRaises(HostDenied):
            FreshBatchHost(self.root, clock=lambda: 200)

    def test_expiry_and_clock_before_enrollment_deny(self):
        for clock in [99, 3700]:
            with self.assertRaises(HostDenied):
                FreshBatchHost(self.root, clock=lambda: clock)

    def test_changed_pinned_configuration_denies(self):
        changed = dict(self.config, owner='oauth:' + 'b' * 64)
        (self.root / 'host.json').write_bytes(canonical(changed))
        with self.assertRaises(HostDenied):
            self.host.check('submit')

    def test_unknown_owner_parent_and_generation_never_reserve(self):
        for field, value in [('owner', 'oauth:' + 'b' * 64), ('parent_id', 'old-parent'),
                             ('generation', 'b' * 32)]:
            with self.assertRaises(HostDenied):
                self.host.verify_request(self.signed(**{field: value}))
        self.assertEqual(list((self.root / 'attempts').iterdir()), [])

    def test_invalid_mac_and_replayed_expired_request_deny(self):
        envelope = self.signed()
        envelope['body']['payload']['title'] = 'changed'
        with self.assertRaisesRegex(HostDenied, 'SIGNATURE'):
            self.host.verify_request(envelope)
        with self.assertRaises(HostDenied):
            self.host.verify_request(self.signed(expires_at=199))

    def test_unsafe_private_file_and_symlink_deny(self):
        (self.root / 'host.json').chmod(0o644)
        with self.assertRaises(HostDenied):
            FreshBatchHost(self.root, clock=lambda: 200)
        (self.root / 'host.json').chmod(0o600)
        (self.root / 'STOP').symlink_to(self.root / 'missing')
        with self.assertRaises(HostDenied):
            self.host.check('read')

    def test_second_worker_cannot_acquire_live_lock(self):
        with self.host.single_worker():
            with self.assertRaises(BlockingIOError):
                with self.host.single_worker():
                    self.fail('second worker acquired a live lock')

    def test_signed_reply_requires_exact_generation_request_and_signer(self):
        body = self.signed()['body']
        response = dict(generation=self.config['generation'], request_id=body['request_id'],
                        request_sha256=digest(body), result={'content': 'synthetic pending'},
                        automatic_dispatch=False, native_execution_verified=False)
        signed = dict(body=response, signature=base64.b64encode(self.host.key.sign(canonical(response))).decode())
        rpc = BatchRPC(config=self.config, ingress_key=self.host.ingress, connection_factory=lambda: None)
        self.assertEqual(rpc.verified_result(canonical(signed), body), response)
        response['generation'] = 'b' * 32
        signed['signature'] = base64.b64encode(self.host.key.sign(canonical(response))).decode()
        with self.assertRaisesRegex(ValueError, 'SCOPE'):
            rpc.verified_result(canonical(signed), body)

    def test_transport_namespace_does_not_change_legacy_inbox_document(self):
        self.assertIn(':deer-batch:' + self.config['generation'], prefix(self.config))
        self.assertNotEqual(prefix(self.config), prefix(dict(self.config, generation='b' * 32)))


if __name__ == '__main__':
    unittest.main()
