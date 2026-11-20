"""Mention doorbell. HTTP is mocked; these tests make no network calls.

Run: python -m unittest tests.test_chat_doorbell -v
"""
import asyncio
import importlib.util
import json
import os
import sys
import unittest
import urllib.error
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chat_doorbell  # noqa: E402
from chat_doorbell import (  # noqa: E402
    ENV_AUTHORIZATION, ENV_URL, TIMEOUT_SECONDS, TYPE, _NoRedirect, post_once,
)
from chat_room import ChatError, ChatStore  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    'chat_room_tests', os.path.join(os.path.dirname(__file__), 'test_chat_room.py'))
_chat_tests = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_chat_tests)
Harness = _chat_tests.Harness

URL = 'https://doorbell.example/wake'
AUTH = 'Bearer doorbell-secret-do-not-leak'
TEXT = 'BODY-SECRET-do-not-leak'
SPEAKER = '祐'
CONFIGURED = {ENV_URL: URL, ENV_AUTHORIZATION: AUTH}


def _no_secret(blob):
    raw = blob if isinstance(blob, str) else json.dumps(blob)
    if AUTH in raw or URL in raw or TEXT in raw:
        raise AssertionError('secret or message text leaked')


class TransportTests(unittest.TestCase):
    def test_post_once_rejects_non_https_before_open(self):
        opener = self.Opener()
        with self.assertRaises(ValueError):
            post_once('http://example.com/hook', {}, b'{}', TIMEOUT_SECONDS, opener=opener)
        self.assertEqual(opener.calls, [])

    def test_post_once_single_request_uses_the_given_timeout(self):
        opener = self.Opener()
        status = post_once(URL, {'Content-Type': 'application/json'}, b'{}',
                           TIMEOUT_SECONDS, opener=opener)
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


class DoorbellTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

    def poster(self, url, headers, body, timeout):
        self.calls.append({'url': url, 'headers': dict(headers), 'body': body, 'timeout': timeout})
        return 204

    def post(self, text='@Grok ' + TEXT, speaker=SPEAKER, environ=None, **env):
        h = Harness(doorbell_environ=CONFIGURED if environ is None else environ, **env)
        with patch('chat_doorbell.post_once', self.poster):
            result = h.call('chat_post', room='main', speaker=speaker, text=text)
        return h, result

    def test_positive_posts_once_with_only_allowed_fields(self):
        with self.assertLogs('chat_doorbell', level='INFO') as captured:
            h, r = self.post()
        self.assertTrue(r['stored'])
        self.assertEqual(r['mention_doorbells'], [{'to': 'Grok', 'delivery': 'dispatched'}])
        self.assertEqual(set(r['mention_doorbells'][0]), {'to', 'delivery'})
        self.assertEqual(len(self.calls), 1)
        call = self.calls[0]
        self.assertEqual(call['url'], URL)
        self.assertEqual(call['timeout'], TIMEOUT_SECONDS)
        self.assertLessEqual(call['timeout'], 3)
        self.assertEqual(call['headers'], {
            'Content-Type': 'application/json',
            'Authorization': AUTH,
        })
        body = json.loads(call['body'].decode())
        self.assertEqual(set(body), {'type', 'room', 'seq', 'member', 'created'})
        self.assertEqual(body, {
            'type': TYPE,
            'room': 'main',
            'seq': 1,
            'member': 'Grok',
            'created': r['message']['at'],
        })
        self.assertNotIn(SPEAKER, call['body'].decode())
        self.assertNotIn(TEXT, call['body'].decode())
        self.assertNotIn('preview', body)
        _no_secret(call['body'].decode())
        _no_secret('\n'.join(captured.output))
        _no_secret(r['mention_doorbells'])
        self.assertNotIn(URL, json.dumps(r))
        self.assertNotIn(AUTH, json.dumps(r))
        self.assertIn(TEXT, r['message']['text'])
        self.assertEqual(h.call('chat_read', room='main')['items'][0]['text'], '@Grok ' + TEXT)
        client = h.client()
        keys = client.keys('xj:chat-room:v1:*doorbell*')
        self.assertEqual(len(keys), 1)
        self.assertEqual(client.get(keys[0]), '1')
        self.assertNotIn(TEXT, keys[0])
        self.assertTrue(0 < client.ttl(keys[0]) <= 30 * 86400)

    def test_disabled_and_partial_config_make_no_call(self):
        cases = (
            {},
            {ENV_URL: URL},
            {ENV_AUTHORIZATION: AUTH},
            {ENV_URL: '  ', ENV_AUTHORIZATION: '  '},
        )
        for environ in cases:
            with self.subTest(environ=sorted(environ)):
                self.calls.clear()
                with self.assertNoLogs('chat_doorbell', level='DEBUG'):
                    h, r = self.post(environ=environ)
                self.assertEqual(self.calls, [])
                self.assertNotIn('mention_doorbells', r)
                self.assertTrue(r['stored'])
                self.assertNotIn(URL, json.dumps(r))
                self.assertNotIn(AUTH, json.dumps(r))
                keys = h.client().keys('xj:chat-room:v1:*doorbell*')
                self.assertEqual(keys, [])

    def test_noop_and_self_mention_do_not_ring(self):
        _, noop = self.post(text='  NOOP ', speaker='Grok')
        self.assertEqual(noop, {'stored': False, 'reason': 'noop'})
        self.assertEqual(self.calls, [])
        _, own = self.post(text='@Grok ' + TEXT, speaker='Grok')
        self.assertTrue(own['stored'])
        self.assertEqual(own['message']['mentions'], [])
        self.assertNotIn('mention_doorbells', own)
        self.assertEqual(self.calls, [])
        _, other = self.post(text='@Claude ' + TEXT, speaker='Grok')
        self.assertEqual(other['message']['mentions'], ['Claude'])
        self.assertEqual(self.calls, [])

    def test_non_https_and_bad_authorization_are_skipped_without_a_call(self):
        cases = (
            {ENV_URL: 'http://doorbell.example/wake?token=' + AUTH, ENV_AUTHORIZATION: AUTH},
            {ENV_URL: 'https://user:' + AUTH + '@doorbell.example/wake', ENV_AUTHORIZATION: AUTH},
            {ENV_URL: URL, ENV_AUTHORIZATION: AUTH + '\r\nX-Injected: 1'},
        )
        for environ in cases:
            with self.subTest(url=environ[ENV_URL][:24]):
                self.calls.clear()
                with self.assertLogs('chat_doorbell', level='WARNING') as captured:
                    _, r = self.post(environ=environ)
                self.assertEqual(self.calls, [])
                self.assertEqual(r['mention_doorbells'], [{'to': 'Grok', 'delivery': 'skipped'}])
                self.assertTrue(r['stored'])
                text = '\n'.join(captured.output)
                self.assertNotIn(AUTH, text)
                self.assertNotIn(URL, text)
                self.assertNotIn(TEXT, text)
                self.assertNotIn(AUTH, json.dumps(r))
                self.assertNotIn('http://', json.dumps(r['mention_doorbells']))

    def test_http_error_and_timeout_keep_the_message_and_try_once(self):
        failures = (
            urllib.error.HTTPError(URL, 503, 'down ' + AUTH + TEXT, {}, None),
            TimeoutError('timed out ' + AUTH + TEXT),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                self.calls.clear()

                def boom(url, headers, body, timeout, exc=failure):
                    self.calls.append(1)
                    raise exc

                h = Harness(doorbell_environ=CONFIGURED)
                with patch('chat_doorbell.post_once', boom):
                    with self.assertLogs('chat_doorbell', level='WARNING') as captured:
                        r = h.call('chat_post', room='main', speaker=SPEAKER, text='@Grok ' + TEXT)
                self.assertEqual(self.calls, [1])
                self.assertTrue(r['stored'])
                self.assertEqual(r['mention_doorbells'], [{'to': 'Grok', 'delivery': 'failed'}])
                self.assertEqual(h.call('chat_read', room='main')['items'][0]['text'], '@Grok ' + TEXT)
                text = '\n'.join(captured.output)
                self.assertNotIn(AUTH, text)
                self.assertNotIn(URL, text)
                self.assertNotIn(TEXT, text)
                again = chat_doorbell.ring_mentions(
                    r['message'], SPEAKER, ChatStore(h.owner, h.config, h.client),
                    environ=CONFIGURED, poster=boom)
                self.assertEqual(again, [{'to': 'Grok', 'delivery': 'skipped'}])
                self.assertEqual(self.calls, [1])

    def test_duplicate_mention_and_second_ring_post_once(self):
        h = Harness(doorbell_environ=CONFIGURED)
        with patch('chat_doorbell.post_once', self.poster):
            r = h.call('chat_post', room='main', speaker=SPEAKER, text='@Grok @grok ' + TEXT)
            second = h.call('chat_post', room='main', speaker=SPEAKER, text='@Grok later')
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(r['mention_doorbells'][0]['delivery'], 'dispatched')
        self.assertEqual(json.loads(self.calls[0]['body'].decode())['seq'], 1)
        self.assertEqual(json.loads(self.calls[1]['body'].decode())['seq'], 2)
        with patch('chat_doorbell.post_once', self.poster):
            again = chat_doorbell.ring_mentions(
                r['message'], SPEAKER, ChatStore(h.owner, h.config, h.client),
                environ=CONFIGURED)
        self.assertEqual(again, [{'to': 'Grok', 'delivery': 'skipped'}])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(second['message']['seq'], 2)

    def test_claim_failure_does_not_post_or_undo_the_write(self):
        h = Harness(doorbell_environ=CONFIGURED)
        with patch('chat_room.ChatStore.claim_doorbell', return_value=None), \
                patch('chat_doorbell.post_once', self.poster), \
                self.assertLogs('chat_doorbell', level='WARNING') as captured:
            r = h.call('chat_post', room='main', speaker=SPEAKER, text='@Grok ' + TEXT)
        self.assertEqual(self.calls, [])
        self.assertTrue(r['stored'])
        self.assertEqual(r['mention_doorbells'], [{'to': 'Grok', 'delivery': 'failed'}])
        self.assertEqual(h.call('chat_read', room='main')['latest_seq'], 1)
        self.assertNotIn(AUTH, '\n'.join(captured.output))
        self.assertNotIn(URL, '\n'.join(captured.output))
        self.assertNotIn(TEXT, '\n'.join(captured.output))

    def test_self_mention_guard_never_claims_or_posts(self):
        claims = []

        class Store:
            def claim_doorbell(self, room, seq, member):
                claims.append((room, seq, member))
                return True

        message = {'room': 'main', 'seq': 4, 'at': 10, 'text': TEXT, 'speaker': 'Grok',
                   'mentions': ['Grok', 'Grok']}
        result = chat_doorbell.ring_mentions(
            message, 'Grok', Store(), environ=CONFIGURED, poster=self.poster)
        self.assertEqual(result, [])
        self.assertEqual(claims, [])
        self.assertEqual(self.calls, [])
        message['mentions'] = ['Grok', 'Grok']
        result = chat_doorbell.ring_mentions(
            message, SPEAKER, Store(), environ=CONFIGURED, poster=self.poster)
        self.assertEqual(result, [{'to': 'Grok', 'delivery': 'dispatched'}])
        self.assertEqual(claims, [('main', 4, 'Grok')])
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn(TEXT, self.calls[0]['body'].decode())

    def test_streak_limit_does_not_ring(self):
        h = Harness({'XJ_CHAT_AI_STREAK_LIMIT': '1'}, doorbell_environ=CONFIGURED)
        with patch('chat_doorbell.post_once', self.poster):
            h.call('chat_post', room='main', speaker='GPT', text='one')
            with self.assertRaises(ChatError):
                h.call('chat_post', room='main', speaker='Claude', text='@Grok ' + TEXT)
        self.assertEqual(self.calls, [])
        self.assertEqual(h.call('chat_read', room='main')['latest_seq'], 1)

    def test_mailbox_wake_stays_separate_and_webhook_has_no_preview(self):
        h = Harness(doorbell_environ=CONFIGURED)
        with patch('chat_doorbell.post_once', self.poster):
            r = h.call('chat_post', room='main', speaker=SPEAKER, text='@GPT @Grok ' + TEXT)
        self.assertEqual(len(h.notices), 1)
        notice = json.loads(h.notices[0][2])
        self.assertEqual(notice['to'], 'GPT')
        self.assertIn(TEXT, notice['preview'])
        self.assertEqual(len(self.calls), 1)
        body = json.loads(self.calls[0]['body'].decode())
        self.assertEqual(body['member'], 'Grok')
        self.assertNotIn(TEXT, self.calls[0]['body'].decode())
        self.assertNotIn(SPEAKER, self.calls[0]['body'].decode())
        self.assertEqual([item['to'] for item in r['mention_doorbells']], ['Grok'])
        self.assertEqual(r['mailbox_wakes'][0]['to'], 'GPT')

    def test_unset_install_reads_the_environment(self):
        saved = {name: os.environ.pop(name, None) for name in (ENV_URL, ENV_AUTHORIZATION)}
        os.environ[ENV_URL] = URL
        os.environ[ENV_AUTHORIZATION] = AUTH
        try:
            mcp = _chat_tests.FakeMcp()
            from chat_room import ChatConfig, install_chat_tools
            import fakeredis
            server = fakeredis.FakeServer()

            def client():
                return fakeredis.FakeRedis(server=server, decode_responses=True)

            install_chat_tools(
                mcp, lambda: 'oauth:owner-a', {}, config=ChatConfig({'XJ_CHAT_ROOM_ENABLED': 'true'}),
                client_factory=client)
            with patch('chat_doorbell.post_once', self.poster):
                result = asyncio.run(
                    mcp.tools['chat_post'](room='main', speaker=SPEAKER, text='@grok ' + TEXT))
            self.assertEqual(result['mention_doorbells'][0]['delivery'], 'dispatched')
            body = json.loads(self.calls[0]['body'].decode())
            self.assertEqual(body['member'], 'Grok')
            self.assertNotIn(TEXT, self.calls[0]['body'].decode())
            _no_secret(result['mention_doorbells'])
        finally:
            for name in (ENV_URL, ENV_AUTHORIZATION):
                os.environ.pop(name, None)
            for name, value in saved.items():
                if value is not None:
                    os.environ[name] = value


if __name__ == '__main__':
    unittest.main()
