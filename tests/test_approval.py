"""Offline approval-ticket tests: fakeredis with Lua, a locally generated ES256 key
that plays the Owner's passkey, FastAPI TestClient for the two HTTP routes.

No real passkey, phone, ntfy, Redis or OAuth.
Run: python -m unittest tests.test_approval -v
"""
import asyncio
import base64
import hashlib
import json
import os
import sys
import unittest
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fakeredis  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402

import approval  # noqa: E402
from approval import ApprovalConfig, ApprovalError, install_approval  # noqa: E402
from chat_room import ChatConfig, ChatStore, install_chat_tools  # noqa: E402

RP = 'owner.github.io'
ORIGIN = 'https://owner.github.io'
OWNER = 'oauth:owner-a'


def b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


class Passkey:
    def __init__(self):
        self.priv = ec.generate_private_key(ec.SECP256R1())
        spki = self.priv.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        self.entry = {'label': 'owner-phone', 'rp_id': RP, 'origin': ORIGIN,
                      'credential_id': 'cred-1', 'alg': -7, 'public_key_spki': b64u(spki)}

    def sign(self, text, *, flags=0x05, origin=ORIGIN, rp=RP, cred='cred-1', challenge_text=None):
        challenge = hashlib.sha256((challenge_text or text).encode()).digest()
        cdj = json.dumps({'type': 'webauthn.get', 'challenge': b64u(challenge),
                          'origin': origin, 'crossOrigin': False}).encode()
        auth = hashlib.sha256(rp.encode()).digest() + bytes([flags]) + b'\x00\x00\x00\x01'
        sig = self.priv.sign(auth + hashlib.sha256(cdj).digest(), ec.ECDSA(hashes.SHA256()))
        return {'v': 1, 'approval': b64u(text.encode()), 'credential_id': cred,
                'authenticator_data': b64u(auth), 'client_data_json': b64u(cdj),
                'signature': b64u(sig)}


class FakeMcp:
    def __init__(self):
        self.tools = {}

    def tool(self, **kwargs):
        def decorate(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorate


class Clock:
    def __init__(self):
        self.t = 1_800_000_000

    def __call__(self):
        return self.t


class Harness:
    def __init__(self, enabled=True, notify_status=200):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        self.server = fakeredis.FakeServer()
        self.key = Passkey()
        self.clock = Clock()
        approval.clock = self.clock
        self.pushes, self.notices = [], []
        chat_env = {'XJ_CHAT_ROOM_ENABLED': 'true',
                    'XJ_CHAT_PUSH_URLS': 'Claude=https://push.example/claude'}
        self.chat_config = ChatConfig(chat_env)
        self.mcp = FakeMcp()
        install_chat_tools(self.mcp, lambda: OWNER, {}, config=self.chat_config,
                           client_factory=self.client, push=self.push, doorbell_environ={})
        env = {'XJ_APPROVAL_ENABLED': 'true' if enabled else 'false',
               'XJ_APPROVAL_OWNER_KEY': json.dumps(self.key.entry),
               'XJ_APPROVAL_PAGE_URL': ORIGIN + '/xj-boot-approve/',
               'XJ_APPROVAL_NOTIFY_URL': 'https://ntfy.example/owner',
               'XJ_MCP_PUBLIC_HOST': 'deer.example'}
        self.app = FastAPI()
        self.names = install_approval(
            self.app, self.mcp, lambda: OWNER, {}, owner_from_env=lambda: OWNER,
            config=ApprovalConfig(env), client_factory=self.client, chat_config=self.chat_config,
            notify=lambda url, title, click: (self.notices.append((url, title, click)) or notify_status))
        self.http = TestClient(self.app)

    def client(self):
        return fakeredis.FakeRedis(server=self.server, decode_responses=True)

    def push(self, url, body):
        self.pushes.append((url, json.loads(body)))
        return 200

    def call(self, name, **kw):
        return asyncio.run(self.mcp.tools[name](**kw))

    def chat(self):
        return ChatStore(OWNER, self.chat_config, self.client).read('lobby', 0, 200)['items']

    def request(self, **kw):
        args = dict(requester='Claude', scope='code_pr', title='Fix typo',
                    plan='Change README line 3 from "teh" to "the"; open a PR; no merge.')
        args.update(kw)
        return self.call('approval_request', **args)

    def text_of(self, ticket):
        return self.client().hget(f'{approval.NAMESPACE}:{hashlib.sha256(OWNER.encode()).hexdigest()[:32]}:ticket:{ticket}', 'text')

    def submit(self, payload):
        return self.http.post('/api/approval/submit', content=json.dumps(payload))


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self._clock = approval.clock

    def tearDown(self):
        approval.clock = self._clock

    def test_request_creates_pending_notifies_and_posts(self):
        h = Harness()
        r = h.request()
        self.assertEqual(r['state'], 'pending')
        self.assertTrue(r['phone_notified'])
        self.assertTrue(r['approve_link'].startswith('https://deer.example/api/approval/open/'))
        self.assertEqual(h.notices[0][2], r['approve_link'])
        notice = h.chat()[-1]
        self.assertEqual((notice['speaker'], notice['kind']), ('Approval', 'system'))
        self.assertIn(r['ticket'][:8], notice['text'])
        self.assertEqual(h.call('approval_status', ticket=r['ticket'])['state'], 'pending')

    def test_open_redirects_with_text_and_matching_plan(self):
        h = Harness()
        r = h.request()
        res = h.http.get(f'/api/approval/open/{r["ticket"]}', follow_redirects=False)
        self.assertEqual(res.status_code, 302)
        frag = parse_qs(urlsplit(res.headers['location']).fragment)
        text = base64.urlsafe_b64decode(frag['a'][0] + '==').decode()
        plan = base64.urlsafe_b64decode(frag['p'][0] + '==').decode()
        self.assertTrue(approval.TEXT_RE.match(text))
        self.assertIn('plan_sha256=' + hashlib.sha256(plan.encode()).hexdigest(), text)

    def test_valid_passkey_approves_and_wakes_requester(self):
        h = Harness()
        r = h.request()
        res = h.submit(h.key.sign(h.text_of(r['ticket'])))
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.headers['access-control-allow-origin'], ORIGIN)
        self.assertEqual(h.call('approval_status', ticket=r['ticket'])['state'], 'approved')
        last = h.chat()[-1]
        self.assertIn('Claude', last['mentions'])
        self.assertEqual(last['kind'], 'system')
        self.assertTrue(any(p[1]['to'] == 'Claude' for p in h.pushes))

    def test_tampered_plan_hash_rejected(self):
        h = Harness()
        r = h.request()
        text = h.text_of(r['ticket'])
        forged = text.replace('scope=code_pr', 'scope=deploy')
        res = h.submit(h.key.sign(forged))
        self.assertEqual(res.status_code, 400)
        self.assertEqual(h.call('approval_status', ticket=r['ticket'])['state'], 'pending')

    def test_wrong_key_origin_rp_or_uv_rejected(self):
        h = Harness()
        r = h.request()
        text = h.text_of(r['ticket'])
        other = Passkey()
        for payload in (other.sign(text),
                        h.key.sign(text, origin='https://evil.example'),
                        h.key.sign(text, rp='evil.example'),
                        h.key.sign(text, flags=0x01),
                        h.key.sign(text, cred='cred-2'),
                        h.key.sign(text, challenge_text=text + 'x')):
            self.assertEqual(h.submit(payload).status_code, 400)
        self.assertEqual(h.call('approval_status', ticket=r['ticket'])['state'], 'pending')

    def test_expired_and_replay_rejected(self):
        h = Harness()
        r = h.request()
        text = h.text_of(r['ticket'])
        h.clock.t += approval.TTL_PENDING + 1
        self.assertEqual(h.submit(h.key.sign(text)).status_code, 400)
        self.assertEqual(h.call('approval_status', ticket=r['ticket'])['state'], 'expired')
        h2 = Harness()
        r2 = h2.request()
        payload = h2.key.sign(h2.text_of(r2['ticket']))
        self.assertEqual(h2.submit(payload).status_code, 200)
        self.assertEqual(h2.submit(payload).status_code, 400)  # already approved

    def test_claim_once_returns_plan_and_report_by_claimant(self):
        h = Harness()
        r = h.request()
        with self.assertRaises(ApprovalError):  # not yet approved
            h.call('approval_claim', ticket=r['ticket'], executor='Claude')
        h.submit(h.key.sign(h.text_of(r['ticket'])))
        got = h.call('approval_claim', ticket=r['ticket'], executor='Claude')
        self.assertIn('teh', got['plan'])
        with self.assertRaises(ApprovalError):
            h.call('approval_claim', ticket=r['ticket'], executor='GPT')
        with self.assertRaises(ApprovalError):
            h.call('approval_report', ticket=r['ticket'], executor='GPT', result='done')
        self.assertEqual(h.call('approval_report', ticket=r['ticket'], executor='Claude',
                                result='PR #99 opened')['state'], 'done')
        self.assertIn('已完成', h.chat()[-1]['text'])

    def test_rate_limits(self):
        h = Harness()
        for _ in range(approval.MAX_PENDING):
            h.request()
        with self.assertRaises(ApprovalError):
            h.request()
        h.clock.t += approval.TTL_PENDING + 1  # pending expire, daily counter continues
        for _ in range(approval.MAX_PER_DAY - approval.MAX_PENDING - 1):
            h.request()
            h.clock.t += approval.TTL_PENDING + 1
        with self.assertRaises(ApprovalError):
            h.request()

    def test_input_validation(self):
        h = Harness()
        for kw in ({'requester': 'Nobody'}, {'scope': 'root'}, {'plan': 'x' * 4001},
                   {'title': '\n\n'}, {'room': 'bad room'}):
            with self.assertRaises(ApprovalError):
                h.request(**kw)
        r = h.request(title='line1\nline2 @GPT')
        self.assertNotIn('\n', h.call('approval_status', ticket=r['ticket'])['title'])

    def test_disabled_installs_nothing(self):
        h = Harness(enabled=False)
        self.assertEqual(h.names, [])
        self.assertNotIn('approval_request', h.mcp.tools)

    def test_bad_config_raises(self):
        key = Passkey().entry
        base = {'XJ_APPROVAL_ENABLED': 'true', 'XJ_APPROVAL_OWNER_KEY': json.dumps(key),
                'XJ_APPROVAL_PAGE_URL': ORIGIN + '/', 'XJ_MCP_PUBLIC_HOST': 'deer.example'}
        ApprovalConfig(base)
        for bad in ({'XJ_APPROVAL_OWNER_KEY': '{}'},
                    {'XJ_APPROVAL_PAGE_URL': 'https://other.example/'},
                    {'XJ_APPROVAL_NOTIFY_URL': 'http://plain.example'},
                    {'XJ_MCP_PUBLIC_HOST': ''}):
            with self.assertRaises(ApprovalError):
                ApprovalConfig({**base, **bad})


class SystemPostTests(unittest.TestCase):
    def test_post_system_keeps_streak_and_wakes(self):
        server = fakeredis.FakeServer()
        client = lambda: fakeredis.FakeRedis(server=server, decode_responses=True)  # noqa: E731
        pushes = []
        config = ChatConfig({'XJ_CHAT_ROOM_ENABLED': 'true',
                             'XJ_CHAT_PUSH_URLS': 'Claude=https://push.example/c'})
        mcp = FakeMcp()
        install_chat_tools(mcp, lambda: OWNER, {}, config=config, client_factory=client,
                           push=lambda u, b: pushes.append(b) or 200, doorbell_environ={})
        store = ChatStore(OWNER, config, client)
        store.append('lobby', 'GPT', 'ai', 'one', [], None)
        msg = asyncio.run(mcp.xj_chat_post_system(OWNER, 'lobby', 'Approval', 'hi @Claude'))
        self.assertEqual((msg['kind'], msg['mentions']), ('system', ['Claude']))
        self.assertEqual(len(pushes), 1)
        _, streak = store.append('lobby', 'GPT', 'ai', 'two', [], None)
        self.assertEqual(streak, 2)


if __name__ == '__main__':
    unittest.main()
