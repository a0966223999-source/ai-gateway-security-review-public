"""Lifecycle and CLI event contract tests; no live grants, Codex, or dispatch."""
import hmac
import json
from pathlib import Path
import tempfile
import unittest

from fresh_batch_host import (FreshBatchHost, HostDenied, canonical, digest,
                              enrollment_active, initialize, request_deadline)
from platform_codex_executor import Execute, successful_output


class PlatformServiceTests(unittest.TestCase):
    def test_service_restart_preserves_generation_and_stop_revokes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'service'
            config = initialize(root, owner='oauth:' + 'a' * 64, platform_service=True,
                                enable_codex=True, now=100)
            self.assertIsNone(config['expires_at'])
            self.assertIn('execute', config['actions'])
            host = FreshBatchHost(root, clock=lambda: 9999999999)
            restarted = FreshBatchHost(root, clock=lambda: 9999999999)
            self.assertEqual(host.config, restarted.config)
            (root / 'STOP').touch(mode=0o600)
            with self.assertRaises(HostDenied):
                restarted.check('execute')
            with self.assertRaises(HostDenied):
                FreshBatchHost(root, clock=lambda: 9999999999)

    def test_null_expiry_is_not_an_implicit_extension_of_test_enrollment(self):
        config = dict(created_at=100, expires_at=None, authority='new-owner-enrollment')
        self.assertFalse(enrollment_active(config, 200))
        config['lifecycle'] = 'owner-platform-service-v1'
        self.assertFalse(enrollment_active(config, 200))
        self.assertFalse(enrollment_active(dict(config, expires_at=150), 200))

    def test_service_requests_still_expire_and_durable_attempts_never_replay(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'service'
            c = initialize(root, owner='oauth:' + 'a' * 64, platform_service=True, now=100)
            host = FreshBatchHost(root, clock=lambda: 200)
            self.assertEqual(request_deadline(c, 200, 60), 260)
            body = dict(generation=c['generation'], owner=c['owner'], parent_id=c['parent_id'],
                        action='submit', operation_key='once', payload={}, expires_at=260)
            body['request_id'] = digest([c['generation'], c['owner'], 'submit', 'once'])
            def signed():
                return dict(body=body, mac=hmac.new(host.ingress, canonical(body), 'sha256').hexdigest())
            host.reserve(host.verify_request(signed()))
            with self.assertRaisesRegex(HostDenied, 'QUERY_ONLY'):
                FreshBatchHost(root, clock=lambda: 200).reserve(body)
            body['expires_at'] = 100000
            with self.assertRaisesRegex(HostDenied, 'SCOPE'):
                host.verify_request(signed())

    def test_executor_not_enabled_in_legacy_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'service'
            with self.assertRaises(HostDenied):
                initialize(root, owner='oauth:' + 'a' * 64, enable_codex=True)
            self.assertFalse(root.exists())

    def test_complete_cli_result_and_rejected_incomplete_events(self):
        events = [dict(type='thread.started', thread_id='synthetic-thread'),
                  dict(type='item.completed', item=dict(type='agent_message', text='synthetic answer')),
                  dict(type='turn.completed')]
        raw = lambda values: '\n'.join(json.dumps(v) for v in values).encode()
        start, report = successful_output(raw(events))
        self.assertEqual(start['thread_id'], 'synthetic-thread')
        self.assertEqual(report, 'synthetic answer')
        for bad in [events[:-1], events[1:], events + [events[0]], events + [dict(type='error')]]:
            with self.assertRaises(HostDenied):
                successful_output(raw(bad))

    def test_caller_cannot_supply_executable_path_or_unbounded_timeout(self):
        kwargs = dict(parent_id='p', operation_key='once', batch_id='b', item_id='i')
        for changes in [dict(executable='/bin/sh'), dict(timeout_seconds=91), dict(timeout_seconds=True)]:
            with self.assertRaises(ValueError):
                Execute(**kwargs, **changes)


if __name__ == '__main__':
    unittest.main()
