"""Offline chat room tests: fakeredis with Lua, fake MCP and fake mailbox.

No real Redis, OAuth, mailbox send, callback or Work run.
Run: python -m unittest tests.test_chat_room -v
"""
import asyncio
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fakeredis  # noqa: E402

import chat_room  # noqa: E402
from chat_room import ChatConfig, ChatError, install_chat_tools  # noqa: E402


class FakeMcp:
    def __init__(self):
        self.tools = {}

    def tool(self, **kwargs):
        def decorate(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorate


ENABLED = {'XJ_CHAT_ROOM_ENABLED': 'true'}


class Harness:
    def __init__(self, env=None, owner='oauth:owner-a', notify_error=False, push_error=None):
        self.server = fakeredis.FakeServer()
        self.owner = owner
        self.notices = []
        self.pushes = []
        self.push_error = push_error
        self.notify_error = notify_error
        self.mcp = FakeMcp()
        self.config = ChatConfig({**ENABLED, **(env or {})})

        async def notify(owner, op, body):
            self.notices.append((owner, op, body))
            if self.notify_error:
                raise OSError('lost after possible send')
            return {'event_id': 'e' * 64, 'duplicate': False}

        self.names = install_chat_tools(
            self.mcp, lambda: self.owner, {'securitySchemes': []}, notify=notify,
            caller_context=lambda ctx, owner: {'schema': 'caller-context-observation-v1',
                                               'work_identity_verified': False},
            config=self.config, client_factory=self.client, push=self.push)

    def push(self, url, body):
        self.pushes.append((url, body))
        if self.push_error == 'raise':
            raise OSError('push lost')
        return 500 if self.push_error == 'status' else 200

    def client(self):
        return fakeredis.FakeRedis(server=self.server, decode_responses=True)

    def call(self, name, **kwargs):
        return asyncio.run(self.mcp.tools[name](**kwargs))


class ConfigTests(unittest.TestCase):
    def test_disabled_registers_nothing(self):
        mcp = FakeMcp()
        self.assertEqual(install_chat_tools(mcp, lambda: 'o', {}, config=ChatConfig({})), [])
        self.assertEqual(mcp.tools, {})

    def test_defaults(self):
        c = ChatConfig(ENABLED)
        self.assertEqual(c.members['GPT'], 'ai')
        self.assertEqual(c.members['祐'], 'human')
        self.assertEqual(c.mailbox_wake, {'GPT'})
        self.assertEqual(c.ttl_seconds, 30 * 86400)
        self.assertEqual(c.max_messages, 5000)
        self.assertEqual(c.ai_streak_limit, 8)

    def test_bad_config_rejected(self):
        for env in ({'XJ_CHAT_MEMBERS': 'GPT:ai'},  # no human
                    {'XJ_CHAT_MEMBERS': 'GPT:robot,me:human'},
                    {'XJ_CHAT_MAILBOX_WAKE': 'Nobody'},
                    {'XJ_CHAT_TTL_DAYS': '0'},
                    {'XJ_CHAT_AI_STREAK_LIMIT': 'x'}):
            with self.subTest(env=env), self.assertRaises(ChatError):
                ChatConfig({**ENABLED, **env})


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness()

    def test_tools_registered(self):
        self.assertEqual(set(self.h.mcp.tools),
                         {'chat_post', 'chat_read', 'chat_mentions', 'chat_rooms'})

    def test_post_and_read_in_order(self):
        a = self.h.call('chat_post', room='main', speaker='祐', text='大家好')
        b = self.h.call('chat_post', room='main', speaker='claude', text='你好 @grok')
        self.assertEqual(a['message']['seq'], 1)
        self.assertEqual(a['message']['mentions'], [])
        self.assertEqual(b['message']['seq'], 2)
        self.assertEqual(b['message']['speaker'], 'Claude')  # canonical name
        self.assertEqual(b['message']['mentions'], ['Grok'])
        page = self.h.call('chat_read', room='main')
        self.assertEqual([m['text'] for m in page['items']], ['大家好', '你好 @grok'])
        self.assertEqual(page['latest_seq'], 2)
        self.assertFalse(page['has_more'])
        self.assertEqual(page['items'][0]['mentions'], [])  # stays a list in Redis
        self.assertEqual(page['items'][1]['origin']['work_identity_verified'], False)
        after = self.h.call('chat_read', room='main', after_seq=1)
        self.assertEqual([m['seq'] for m in after['items']], [2])
        self.assertEqual(self.h.call('chat_read', room='main', after_seq=2)['items'], [])

    def test_pagination(self):
        for i in range(5):
            self.h.call('chat_post', room='main', speaker='祐', text=f'm{i}')
        page = self.h.call('chat_read', room='main', after_seq=0, limit=2)
        self.assertEqual([m['seq'] for m in page['items']], [1, 2])
        self.assertTrue(page['has_more'])
        page = self.h.call('chat_read', room='main', after_seq=4, limit=2)
        self.assertEqual([m['seq'] for m in page['items']], [5])
        self.assertFalse(page['has_more'])

    def test_self_mention_and_unknown_names_ignored(self):
        r = self.h.call('chat_post', room='main', speaker='GPT', text='@GPT @nobody @Claude @claude')
        self.assertEqual(r['message']['mentions'], ['Claude'])

    def test_noop_stores_nothing(self):
        r = self.h.call('chat_post', room='main', speaker='Grok', text='  NOOP ')
        self.assertEqual(r, {'stored': False, 'reason': 'noop'})
        self.assertEqual(self.h.call('chat_read', room='main')['items'], [])

    def test_input_validation(self):
        bad = [dict(room='bad room', speaker='祐', text='x'),
               dict(room='main', speaker='Stranger', text='x'),
               dict(room='main', speaker='祐', text=''),
               dict(room='main', speaker='祐', text='x' * 8001)]
        for kwargs in bad:
            with self.subTest(kwargs=kwargs['room'] + kwargs['speaker']), self.assertRaises(ChatError):
                self.h.call('chat_post', **kwargs)
        with self.assertRaises(ChatError):
            self.h.call('chat_read', room='main', after_seq=-1)
        with self.assertRaises(ChatError):
            self.h.call('chat_read', room='main', limit=201)

    def test_ai_streak_limit_and_human_reset(self):
        for i in range(8):
            r = self.h.call('chat_post', room='main', speaker=['GPT', 'Claude'][i % 2], text=f'a{i}')
        self.assertEqual(r['ai_streak'], 8)
        with self.assertRaises(ChatError) as caught:
            self.h.call('chat_post', room='main', speaker='Grok', text='one more')
        self.assertEqual(str(caught.exception), 'ai_streak_limit_wait_for_human')
        self.assertEqual(self.h.call('chat_read', room='main')['latest_seq'], 8)  # nothing stored
        self.assertEqual(self.h.call('chat_post', room='main', speaker='祐', text='繼續')['ai_streak'], 0)
        self.assertEqual(self.h.call('chat_post', room='main', speaker='Grok', text='ok')['ai_streak'], 1)

    def test_streak_is_per_room(self):
        for i in range(8):
            self.h.call('chat_post', room='a', speaker='GPT', text=f'{i}')
        self.assertEqual(self.h.call('chat_post', room='b', speaker='GPT', text='x')['ai_streak'], 1)

    def test_retention_trims_oldest(self):
        h = Harness({'XJ_CHAT_MAX_MESSAGES': '10'})
        for i in range(15):
            h.call('chat_post', room='main', speaker='祐', text=f'm{i}')
        page = h.call('chat_read', room='main', after_seq=0, limit=50)
        self.assertEqual(page['oldest_kept_seq'], 6)
        self.assertEqual([m['seq'] for m in page['items']], list(range(6, 16)))
        page = h.call('chat_read', room='main', after_seq=12)
        self.assertEqual([m['seq'] for m in page['items']], [13, 14, 15])

    def test_keys_expire(self):
        self.h.call('chat_post', room='main', speaker='祐', text='x')
        client = self.h.client()
        keys = client.keys('xj:chat-room:v1:*')
        self.assertEqual(len(keys), 4)  # seq, log, ai_streak, rooms
        for key in keys:
            self.assertTrue(0 < client.ttl(key) <= 30 * 86400, key)

    def test_owner_isolation(self):
        self.h.call('chat_post', room='main', speaker='祐', text='secret')
        self.h.owner = 'oauth:owner-b'
        self.assertEqual(self.h.call('chat_read', room='main')['items'], [])
        self.assertEqual(self.h.call('chat_rooms')['rooms'], [])

    def test_principal_required(self):
        def deny():
            raise PermissionError('AUTH_REQUIRED')
        mcp = FakeMcp()
        install_chat_tools(mcp, deny, {}, config=ChatConfig(ENABLED),
                           client_factory=lambda: fakeredis.FakeRedis(decode_responses=True))
        for name, kwargs in (('chat_post', dict(room='main', speaker='祐', text='x')),
                             ('chat_read', dict(room='main')),
                             ('chat_mentions', dict(room='main', speaker='GPT')),
                             ('chat_rooms', {})):
            with self.subTest(name=name), self.assertRaises(PermissionError):
                asyncio.run(mcp.tools[name](**kwargs))

    def test_mentions_filter(self):
        self.h.call('chat_post', room='main', speaker='祐', text='@Claude 看一下')
        self.h.call('chat_post', room='main', speaker='GPT', text='我先')
        self.h.call('chat_post', room='main', speaker='祐', text='@claude @Grok 再看')
        r = self.h.call('chat_mentions', room='main', speaker='Claude')
        self.assertEqual([m['seq'] for m in r['items']], [1, 3])
        self.assertEqual(r['latest_seq'], 3)
        r = self.h.call('chat_mentions', room='main', speaker='Claude', after_seq=1)
        self.assertEqual([m['seq'] for m in r['items']], [3])
        self.assertEqual(r['scanned_to_seq'], 3)

    def test_rooms_listing(self):
        self.h.call('chat_post', room='alpha', speaker='祐', text='x')
        self.h.call('chat_post', room='beta', speaker='祐', text='x')
        r = self.h.call('chat_rooms')
        self.assertEqual(set(r['rooms']), {'alpha', 'beta'})
        self.assertIn({'name': 'GPT', 'kind': 'ai'}, r['members'])
        self.assertEqual(r['retention_days'], 30)


class WakeTests(unittest.TestCase):
    def test_gpt_mention_sends_one_notice(self):
        h = Harness()
        r = h.call('chat_post', room='main', speaker='祐', text='@GPT 和 @Claude 請看')
        self.assertEqual(len(h.notices), 1)  # Claude is not mailbox-woken
        owner, op, body = h.notices[0]
        self.assertEqual(owner, 'oauth:owner-a')
        self.assertRegex(op, r'^chat-wake-[0-9a-f]{40}$')
        notice = json.loads(body)
        self.assertEqual((notice['type'], notice['room'], notice['seq'], notice['to'], notice['from']),
                         ('chat.mention', 'main', 1, 'GPT', '祐'))
        self.assertEqual(r['mailbox_wakes'][0]['operation_key'], op)
        self.assertEqual(op, chat_room.wake_operation_key('main', 1, 'GPT'))  # stable on resend

    def test_no_mention_no_notice(self):
        h = Harness()
        h.call('chat_post', room='main', speaker='祐', text='GPT 不帶小老鼠')
        self.assertEqual(h.notices, [])

    def test_notify_failure_keeps_message(self):
        h = Harness(notify_error=True)
        r = h.call('chat_post', room='main', speaker='祐', text='@GPT hi')
        self.assertTrue(r['stored'])
        self.assertEqual(r['mailbox_wakes'][0]['delivery'], 'unconfirmed_resend_same_key')
        self.assertEqual(h.call('chat_read', room='main')['latest_seq'], 1)

    def test_notice_fits_mailbox_limit(self):
        message = {'room': 'main', 'seq': 7, 'speaker': '祐', 'text': '長' * 8000}
        body = chat_room.wake_notice(message, 'GPT')
        self.assertLessEqual(len(body), 1024)
        self.assertEqual(json.loads(body)['seq'], 7)

    def test_custom_wake_list(self):
        h = Harness({'XJ_CHAT_MAILBOX_WAKE': 'GPT,Grok'})
        h.call('chat_post', room='main', speaker='祐', text='@Grok @GPT')
        self.assertEqual(sorted(json.loads(b)['to'] for _, _, b in h.notices), ['GPT', 'Grok'])


class FormalNotifierTests(unittest.TestCase):
    def test_uses_original_send_and_dispatch(self):
        calls = []

        class Box:
            owner = 'oauth:owner-a'

            def for_event(self, name):
                calls.append(('for_event', name))
                return self

            def send(self, op, body, event_name):
                calls.append(('send', op, event_name))
                return {'event_id': 'f' * 64, 'duplicate': False}

        class Formal:
            async def dispatch(self, inbox, event_id):
                calls.append(('dispatch', event_id))
                return [{'state': 'delivered'}]

        notify = chat_room.formal_notifier(lambda mid: Box(), Formal(),
                                           'gateway_internal_v1', 'gateway.continuation.ready')
        receipt = asyncio.run(notify('oauth:owner-a', 'chat-wake-x', '{}'))
        self.assertEqual(calls, [('for_event', 'gateway.continuation.ready'),
                                 ('send', 'chat-wake-x', 'gateway.continuation.ready'),
                                 ('dispatch', 'f' * 64)])
        self.assertEqual(receipt['deliveries'], [{'state': 'delivered'}])
        with self.assertRaises(ChatError):
            asyncio.run(notify('oauth:someone-else', 'chat-wake-x', '{}'))




PUSH = {'XJ_CHAT_PUSH_URLS': 'Claude=https://ntfy.sh/xj-test-topic'}


class PushWakeTests(unittest.TestCase):
    def test_push_config_parsed_and_validated(self):
        self.assertEqual(ChatConfig({**ENABLED, **PUSH}).push_urls,
                         {'Claude': 'https://ntfy.sh/xj-test-topic'})
        self.assertEqual(ChatConfig(ENABLED).push_urls, {})
        for bad in ('Nobody=https://ntfy.sh/t', 'Claude=http://ntfy.sh/t',
                    'Claude=https://ntfy.sh/t?x=1', 'Claude=https://u:p@ntfy.sh/t', 'Claude='):
            with self.subTest(bad=bad), self.assertRaises(ChatError):
                ChatConfig({**ENABLED, 'XJ_CHAT_PUSH_URLS': bad})

    def test_mention_pushes_hint_without_text(self):
        h = Harness(env=PUSH)
        r = h.call('chat_post', room='main', speaker='祐', text='秘密內容 @Claude 請看')
        self.assertEqual(r['push_wakes'], [{'to': 'Claude', 'delivery': 'sent'}])
        self.assertEqual(len(h.pushes), 1)
        url, body = h.pushes[0]
        self.assertEqual(url, 'https://ntfy.sh/xj-test-topic')
        self.assertEqual(json.loads(body), {'type': 'chat.mention', 'room': 'main', 'seq': 1,
                                            'to': 'Claude', 'from': '祐'})
        self.assertNotIn('秘密', body)

    def test_no_push_without_mention_or_for_unlisted(self):
        h = Harness(env=PUSH)
        h.call('chat_post', room='main', speaker='祐', text='沒有點名')
        h.call('chat_post', room='main', speaker='祐', text='@Grok 你好')
        self.assertEqual(h.pushes, [])

    def test_self_mention_does_not_push(self):
        h = Harness(env=PUSH)
        h.call('chat_post', room='main', speaker='Claude', text='我是 @Claude')
        self.assertEqual(h.pushes, [])

    def test_push_failure_keeps_message(self):
        for mode, delivery in (('raise', 'failed'), ('status', 'rejected')):
            with self.subTest(mode=mode):
                h = Harness(env=PUSH, push_error=mode)
                r = h.call('chat_post', room='main', speaker='祐', text='@Claude hi')
                self.assertTrue(r['stored'])
                self.assertEqual(r['push_wakes'], [{'to': 'Claude', 'delivery': delivery}])
                self.assertEqual(h.call('chat_read', room='main')['latest_seq'], 1)

    def test_rooms_lists_push_members_not_urls(self):
        h = Harness(env=PUSH)
        info = h.call('chat_rooms')
        self.assertEqual(info['push_wake'], ['Claude'])
        self.assertNotIn('ntfy', json.dumps(info))


if __name__ == '__main__':
    unittest.main()
