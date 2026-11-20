"""Exercise the original event RPC, chat notifier and Redis transition offline."""
import base64
import json
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

import fakeredis

from chat_room import formal_notifier, wake_notice
from formal_transport import FORMAL, MAILBOX
from inbox import Inbox, InboxError, LOBBY_GPT_MENTION
from mcp_events import EVENT_NAME, Events, LOBBY_GPT_MAILBOX_SELECTOR, SecretBox


class LobbyGptSubscriptionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.inbox = Inbox('oauth:test-owner', MAILBOX, fakeredis.FakeRedis(), event_name=FORMAL)
        self.token = SimpleNamespace(token='fixture-token', expires_at=int(time.time()) + 600)
        self.verifier = SimpleNamespace(verify_token=AsyncMock(return_value=self.token))
        self.callbacks = []
        self.events = Events(self.original_mailbox, self.verifier, sender=self.send_callback,
                             box=SecretBox(b'x' * 32), event_name=FORMAL,
                             authorize_event=lambda *_: True)

    def original_mailbox(self, name):
        self.assertEqual(name, MAILBOX)
        return self.inbox

    async def send_callback(self, url, headers, body, timeout):
        payload = json.loads(body)
        if payload.get('type') == 'verification':
            return 200, body
        self.callbacks.append((headers['X-MCP-Subscription-Id'], payload))
        return 204, b''

    async def rpc(self, method, arguments):
        return await self.events.rpc(method, {
            'name': FORMAL,
            'arguments': arguments,
            'delivery': {
                'mode': 'webhook', 'url': 'https://example.com/callback',
                'secret': 'whsec_' + base64.b64encode(b'x' * 32).decode(),
            },
        }, self.token)

    async def test_schema_and_enforcement_at_send(self):
        schema = (await self.events.rpc('events/list', {}, self.token))['events'][0]['inputSchema']
        self.assertEqual(schema['required'], ['mailbox_id'])
        self.assertEqual(set(schema['properties']['filter']['required']), set(LOBBY_GPT_MENTION))
        plain = {'mailbox_id': MAILBOX}
        scoped = plain | {'filter': LOBBY_GPT_MENTION}
        regular_id = (await self.rpc('events/subscribe', plain))['id']
        scoped_id = (await self.rpc('events/subscribe', scoped))['id']
        self.assertNotEqual(scoped_id, regular_id)
        recorded = {item['id']: item for item in self.inbox.subscription_status()['items']}
        self.assertEqual(recorded[scoped_id]['filter'], LOBBY_GPT_MENTION)
        self.assertNotIn('filter', recorded[regular_id])

        notify = formal_notifier(lambda _: self.inbox, self.events, MAILBOX, FORMAL)
        mention = {'room': 'lobby', 'seq': 17, 'speaker': '祐', 'text': '@GPT test'}
        body = wake_notice(mention, 'GPT')
        fake = self.inbox.send('forged-mention-body', body, event_name=FORMAL)
        await self.events.dispatch(self.inbox, fake['event_id'])
        await notify(self.inbox.owner, 'wrong-room', wake_notice(mention | {'room': 'main'}, 'GPT'))
        await notify(self.inbox.owner, 'wrong-target', wake_notice(mention, 'Grok'))
        actual = await notify(self.inbox.owner, 'correct-mention', body)
        again = await notify(self.inbox.owner, 'correct-mention', body)
        self.assertTrue(again['duplicate'])
        scoped_deliveries = [payload for sid, payload in self.callbacks if sid == scoped_id]
        self.assertEqual([payload['eventId'] for payload in scoped_deliveries], [actual['event_id']])
        self.assertEqual(scoped_deliveries[0]['name'], FORMAL)
        self.assertEqual(sum(sid == regular_id for sid, _ in self.callbacks), 4)
        messages = {row['event_id']: row for row in self.inbox.list()['items']}
        self.assertEqual(messages[actual['event_id']]['chat_mention'], LOBBY_GPT_MENTION)
        self.assertNotIn('chat_mention', messages[fake['event_id']])

        await self.rpc('events/unsubscribe', scoped)
        self.assertEqual([row['id'] for row in self.inbox.subscription_status()['items']], [regular_id])

    async def test_wrong_or_partial_filter_is_rejected(self):
        for event_filter in ({'type': 'chat.mention'}, LOBBY_GPT_MENTION | {'target': 'Grok'},
                             LOBBY_GPT_MENTION | {'room': 'main'}):
            with self.subTest(event_filter=event_filter), self.assertRaisesRegex(InboxError, 'invalid_arguments'):
                await self.rpc('events/subscribe', {'mailbox_id': MAILBOX, 'filter': event_filter})
        self.assertEqual(self.inbox.subscription_status()['count'], 0)

    async def test_mailbox_only_connector_selects_exact_same_subscription(self):
        selector = {'mailbox_id': LOBBY_GPT_MAILBOX_SELECTOR}
        scoped = {'mailbox_id': MAILBOX, 'filter': LOBBY_GPT_MENTION}
        created = await self.rpc('events/subscribe', selector)
        renewed = await self.rpc('events/subscribe', scoped)
        self.assertEqual(created['id'], renewed['id'])
        items = self.inbox.subscription_status()['items']
        self.assertEqual([(row['id'], row['filter']) for row in items],
                         [(created['id'], LOBBY_GPT_MENTION)])
        await self.rpc('events/unsubscribe', selector)
        self.assertEqual(self.inbox.subscription_status()['count'], 0)

    async def test_synthetic_event_cannot_use_formal_selector(self):
        events = Events(self.original_mailbox, self.verifier)
        with self.assertRaisesRegex(InboxError, 'invalid_arguments'):
            await events.rpc('events/subscribe', {
                'name': EVENT_NAME,
                'arguments': {'mailbox_id': LOBBY_GPT_MAILBOX_SELECTOR},
            }, self.token)
