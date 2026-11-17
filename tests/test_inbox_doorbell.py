"""Optional inbox doorbell. HTTP is mocked; these tests make no network calls."""
import json
import os
import unittest
import urllib.error
from unittest.mock import patch

import fakeredis

from inbox import Inbox, InboxError
from inbox_doorbell import (
    ENV_HEADER, ENV_KEY, ENV_MAILBOXES, ENV_URL, TIMEOUT_SECONDS, TYPE,
    _NoRedirect, notify, post_once,
)

MAILBOX = 'gateway_internal_v1'
KEY = 'doorbell-sender-key-fixture'
URL = 'https://doorbell.example/wake'
MESSAGE = 'BODY-SECRET-do-not-leak'
OPERATION = 'OP-SECRET-do-not-leak'
OWNER = 'OWNER-SECRET-do-not-leak'
ENVS = (ENV_URL, ENV_KEY, ENV_MAILBOXES, ENV_HEADER)


class EnvCase(unittest.TestCase):
    def setUp(self):
        self._saved = {name: os.environ.pop(name, None) for name in ENVS}

    def tearDown(self):
        for name in ENVS:
            os.environ.pop(name, None)
        for name, value in self._saved.items():
            if value is not None:
                os.environ[name] = value

    def configure(self, *, url=URL, key=KEY, mailboxes=MAILBOX, header=None):
        os.environ[ENV_URL] = url
        os.environ[ENV_KEY] = key
        os.environ[ENV_MAILBOXES] = mailboxes
        if header is not None:
            os.environ[ENV_HEADER] = header


class NotifyTests(EnvCase):
    def setUp(self):
        super().setUp()
        self.calls = []

    def poster(self, url, headers, body, timeout):
        self.calls.append({'url': url, 'headers': dict(headers), 'body': body, 'timeout': timeout})
        return 204

    def ring(self, mailbox=MAILBOX, sequence=7, created_at=1_700_000_000,
             event_id='ab' * 32, poster=None):
        notify(mailbox, event_id, sequence, created_at, poster=poster or self.poster)

    def payload(self):
        self.assertEqual(len(self.calls), 1)
        body = json.loads(self.calls[0]['body'].decode())
        self.assertEqual(set(body), {'type', 'mailbox_id', 'sequence', 'event_id', 'created_at'})
        return body

    def test_unset_config_makes_no_http_call(self):
        self.ring()
        self.assertEqual(self.calls, [])

    def test_url_and_key_without_allowlist_makes_no_http_call(self):
        os.environ[ENV_URL] = URL
        os.environ[ENV_KEY] = KEY
        self.ring()
        self.assertEqual(self.calls, [])

    def test_allowlist_miss_makes_no_http_call(self):
        self.configure(mailboxes='other-box, second_box')
        self.ring()
        self.assertEqual(self.calls, [])

    def test_configured_posts_once_with_only_allowed_fields(self):
        self.configure(mailboxes=' other-box, gateway_internal_v1 ')
        self.ring()
        body = self.payload()
        self.assertEqual(body, {
            'type': TYPE,
            'mailbox_id': MAILBOX,
            'sequence': 7,
            'event_id': 'ab' * 32,
            'created_at': 1_700_000_000,
        })
        self.assertEqual(self.calls[0]['url'], URL)
        self.assertEqual(self.calls[0]['timeout'], TIMEOUT_SECONDS)
        self.assertLessEqual(self.calls[0]['timeout'], 3)
        self.assertEqual(self.calls[0]['headers'], {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + KEY,
        })
        encoded = self.calls[0]['body'].decode()
        self.assertNotIn(MESSAGE, encoded)
        self.assertNotIn(OPERATION, encoded)
        self.assertNotIn(OWNER, encoded)
        self.assertNotIn('sender_context', encoded)
        self.assertNotIn('operation_key', encoded)

    def test_custom_sender_key_header_has_no_bearer_prefix(self):
        self.configure(header='X-Sender-Key')
        self.ring()
        headers = self.calls[0]['headers']
        self.assertEqual(headers['X-Sender-Key'], KEY)
        self.assertNotIn('Authorization', headers)
        self.assertNotIn('Bearer', headers['X-Sender-Key'])

    def test_non_https_is_rejected_without_a_call_or_the_key_in_logs(self):
        self.configure(url='http://example.com/hook?token=' + KEY)
        with self.assertLogs('inbox_doorbell', level='WARNING') as captured:
            self.ring()
        self.assertEqual(self.calls, [])
        text = '\n'.join(captured.output)
        self.assertIn('rejected_non_https', text)
        self.assertNotIn(KEY, text)

    def test_http_error_and_timeout_are_one_attempt_and_do_not_log_the_key(self):
        self.configure()
        for failure in (
            urllib.error.HTTPError(URL, 503, 'down ' + KEY, {}, None),
            TimeoutError('timed out ' + KEY),
        ):
            with self.subTest(failure=type(failure).__name__):
                self.calls.clear()
                def boom(url, headers, body, timeout, exc=failure):
                    self.calls.append(1)
                    raise exc
                with self.assertLogs('inbox_doorbell', level='WARNING') as captured:
                    self.ring(poster=boom)
                self.assertEqual(self.calls, [1])
                text = '\n'.join(record.getMessage() for record in captured.records)
                self.assertNotIn(KEY, text)
                self.assertNotIn(KEY, '\n'.join(captured.output))

    def test_invalid_key_is_not_logged(self):
        self.configure(key=KEY + '\n')
        with self.assertLogs('inbox_doorbell', level='WARNING') as captured:
            self.ring()
        self.assertEqual(self.calls, [])
        self.assertNotIn(KEY, '\n'.join(captured.output))
        self.assertIn('invalid_key', captured.output[0])


class TransportTests(unittest.TestCase):
    def test_post_once_rejects_non_https_before_open(self):
        opener = self.Opener()
        with self.assertRaises(ValueError):
            post_once('http://example.com/hook', {}, b'{}', TIMEOUT_SECONDS, opener=opener)
        self.assertEqual(opener.calls, [])

    def test_post_once_single_request_uses_the_given_timeout(self):
        opener = self.Opener()
        status = post_once(URL, {'Content-Type': 'application/json'}, b'{}', TIMEOUT_SECONDS, opener=opener)
        self.assertEqual(status, 204)
        self.assertEqual(len(opener.calls), 1)
        request, timeout = opener.calls[0]
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(request.full_url, URL)
        self.assertEqual(timeout, 3)

    def test_redirects_are_not_followed(self):
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, 'moved', {}, URL))

    class Opener:
        def __init__(self):
            self.calls = []

        def open(self, request, timeout):
            self.calls.append((request, timeout))

            class Response:
                status = 204

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

            return Response()


class SendTests(EnvCase):
    def setUp(self):
        super().setUp()
        self.calls = []
        self.error = None
        self.db = fakeredis.FakeRedis(decode_responses=True)
        self.inbox = Inbox(OWNER, MAILBOX, self.db)
        self.patcher = patch('inbox_doorbell.post_once', self.capture)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        super().tearDown()

    def capture(self, url, headers, body, timeout):
        self.calls.append({'url': url, 'headers': dict(headers), 'body': body, 'timeout': timeout})
        if self.error is not None:
            raise self.error
        return 204

    def send(self, event_name='inbox.message.created'):
        return self.inbox.send(OPERATION, MESSAGE, event_name=event_name)

    def test_unset_config_does_not_call_and_keeps_the_receipt(self):
        receipt = self.send()
        self.assertEqual(self.calls, [])
        self.assertFalse(receipt['duplicate'])
        self.assertNotIn('sequence', receipt)
        self.assertNotIn('created_at', receipt)
        self.assertNotIn(MESSAGE, json.dumps(receipt))
        self.assertEqual(self.inbox.delivery_status()['items'], [])

    def test_configured_send_posts_once_after_the_write(self):
        self.configure()
        with self.assertLogs('inbox_doorbell', level='INFO') as captured:
            receipt = self.send()
        self.assertEqual(len(self.calls), 1)
        body = json.loads(self.calls[0]['body'].decode())
        listed = self.inbox.list()['items']
        self.assertEqual(len(listed), 1)
        self.assertEqual(body, {
            'type': TYPE,
            'mailbox_id': MAILBOX,
            'sequence': listed[0]['sequence'],
            'event_id': receipt['event_id'],
            'created_at': listed[0]['created_at'],
        })
        self.assertEqual(self.calls[0]['headers']['Authorization'], 'Bearer ' + KEY)
        self.assertEqual(self.calls[0]['timeout'], 3)
        self.assertNotIn('sequence', receipt)
        self.assertNotIn('created_at', receipt)
        self.assertNotIn(MESSAGE, self.calls[0]['body'].decode())
        self.assertNotIn(OPERATION, self.calls[0]['body'].decode())
        self.assertNotIn(OWNER, self.calls[0]['body'].decode())
        self.assertEqual(listed[0]['message'], MESSAGE)
        text = '\n'.join(captured.output)
        self.assertNotIn(KEY, text)
        self.assertNotIn(MESSAGE, text)
        self.assertNotIn(OPERATION, text)
        again = self.send()
        self.assertTrue(again['duplicate'])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.inbox.delivery_status()['items'], [])

    def test_continuation_and_unlisted_mailbox_do_not_ring(self):
        self.configure()
        receipt = self.send('gateway.continuation.ready')
        self.assertFalse(receipt['duplicate'])
        self.assertEqual(self.calls, [])
        other = Inbox(OWNER, 'other-box', self.db)
        other.send(OPERATION, MESSAGE)
        self.assertEqual(self.calls, [])

    def test_http_error_and_timeout_do_not_break_or_roll_back_send(self):
        self.configure()
        for error in (
            urllib.error.HTTPError(URL, 500, 'err ' + KEY, {}, None),
            TimeoutError('timeout ' + KEY),
        ):
            with self.subTest(error=type(error).__name__):
                self.calls.clear()
                self.error = error
                db = fakeredis.FakeRedis(decode_responses=True)
                self.inbox = Inbox(OWNER, MAILBOX, db)
                with self.assertLogs('inbox_doorbell', level='WARNING') as captured:
                    receipt = self.send()
                self.assertEqual(len(self.calls), 1)
                self.assertFalse(receipt['duplicate'])
                self.assertEqual(self.inbox.list()['items'][0]['message'], MESSAGE)
                self.assertNotIn(KEY, '\n'.join(captured.output))
                self.error = None

    def test_rejected_send_does_not_ring(self):
        self.configure()
        with self.assertRaises(InboxError):
            self.inbox.send(OPERATION, '')
        self.assertEqual(self.calls, [])

        class Full:
            def eval(self, *args):
                return json.dumps({'error': 'mailbox_full'})

        self.inbox = Inbox(OWNER, MAILBOX, Full())
        with self.assertRaises(InboxError):
            self.send()
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
