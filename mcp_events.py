"""MCP Events webhook lifecycle, with bounded delivery and encrypted secrets."""
import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import socket
import ssl
import time
from urllib.parse import urlsplit

import anyio
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import httpcore
from httpcore._backends.anyio import AnyIOBackend

from inbox import InboxError, canonical, identifier

EVENT_NAME = 'inbox.message.created'
EVENT = {
    'name': EVENT_NAME,
    'description': 'A new synthetic inbox message. List, claim, then acknowledge it; no formal Gateway authority.',
    'delivery': ['webhook'],
    'inputSchema': {'type': 'object', 'properties': {
        'mailbox_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,80}$'}},
        'required': ['mailbox_id'], 'additionalProperties': False},
    'payloadSchema': {'type': 'object', 'properties': {
        'mailbox_id': {'type': 'string'}, 'event_id': {'type': 'string'},
        'synthetic': {'type': 'boolean'}},
        'required': ['mailbox_id', 'event_id', 'synthetic'], 'additionalProperties': False},
}


def iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace('+00:00', 'Z')


def validate_url(url):
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 33 for c in url):
        raise InboxError('invalid_callback')
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.fragment or parsed.port not in (None, 443)):
            raise ValueError()
    except ValueError:
        raise InboxError('invalid_callback') from None
    return parsed


def secret_bytes(secret):
    try:
        if not isinstance(secret, str) or len(secret) > 100 or not secret.startswith('whsec_'):
            raise ValueError()
        raw = base64.b64decode(secret[6:], validate=True)
        if not 24 <= len(raw) <= 64:
            raise ValueError()
        return raw
    except (ValueError, TypeError):
        raise InboxError('invalid_signing_secret') from None


class PublicBackend(AnyIOBackend):
    """Resolve for each connection, connect to that IP, preserve TLS SNI upstream."""

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        with anyio.fail_after(timeout):
            records = await anyio.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            addresses = list(dict.fromkeys(r[4][0] for r in records))
            if not addresses or any(not ipaddress.ip_address(ip).is_global
                                    or ipaddress.ip_address(ip).is_multicast for ip in addresses):
                raise InboxError('non_public_callback')
            return await super().connect_tcp(addresses[0], port, timeout=timeout,
                                            socket_options=socket_options)


async def post_webhook(url, headers, body, timeout):
    validate_url(url)
    # httpcore makes no proxy lookup, follows no redirects and uses original-host TLS.
    with anyio.fail_after(timeout):
        async with httpcore.AsyncConnectionPool(ssl_context=ssl.create_default_context(),
                network_backend=PublicBackend(), max_connections=1, max_keepalive_connections=0,
                retries=0) as pool:
            async with pool.stream('POST', url, headers=headers, content=body,
                    extensions={'timeout': {k: timeout for k in ('connect', 'read', 'write', 'pool')}}) as response:
                content = bytearray()
                async for chunk in response.aiter_stream():
                    content.extend(chunk)
                    if len(content) > 65536:
                        raise InboxError('callback_response_too_large')
                return response.status, bytes(content)


class SecretBox:
    def __init__(self, key=None):
        try:
            key = key or base64.b64decode(os.environ['XJ_INBOX_SECRET_KEY'], validate=True)
            if len(key) != 32:
                raise ValueError()
            self.cipher = AESGCM(key)
        except (KeyError, ValueError):
            raise InboxError('subscription_encryption_unavailable') from None

    def seal(self, value, binding):
        nonce = os.urandom(12)
        return base64.b64encode(nonce + self.cipher.encrypt(
            nonce, canonical(value).encode(), binding.encode())).decode()

    def open(self, value, binding):
        raw = base64.b64decode(value, validate=True)
        return json.loads(self.cipher.decrypt(raw[:12], raw[12:], binding.encode()))


def signed_headers(sid, event_id, body, keys):
    timestamp = str(int(time.time()))
    content = event_id.encode() + b'.' + timestamp.encode() + b'.' + body
    signatures = ['v1,' + base64.b64encode(hmac.digest(secret_bytes(k), content, 'sha256')).decode()
                  for k in keys]
    return {'Content-Type': 'application/json', 'webhook-id': event_id,
            'webhook-timestamp': timestamp, 'webhook-signature': ' '.join(signatures),
            'X-MCP-Subscription-Id': sid}


class Events:
    def __init__(self, inbox_factory, verifier, sender=post_webhook, box=None, *,
                 event_name=EVENT_NAME, authorize_event=None):
        if event_name not in (EVENT_NAME, 'gateway.continuation.ready'):
            raise InboxError('unknown_event')
        if event_name != EVENT_NAME and not callable(authorize_event):
            raise InboxError('formal_authority_not_bound')
        self.event_name = event_name
        self.authorize_event = authorize_event
        self.inbox_factory = inbox_factory
        self.verifier = verifier
        self.sender = sender
        self._box = box

    @property
    def box(self):
        if self._box is None:
            self._box = SecretBox()
        return self._box

    async def rpc(self, method, params, token):
        if not isinstance(params, dict):
            raise InboxError('invalid_params')
        if method == 'events/list':
            if params.get('cursor') is not None:
                raise InboxError('invalid_cursor')
            event = json.loads(json.dumps(EVENT))
            event['name'] = self.event_name
            if self.event_name != EVENT_NAME:
                if self.authorize_event('discover', None, None) is not True:
                    raise InboxError('formal_authority_required')
                event['description'] = 'An admitted Gateway continuation is ready. Notification only; receiver must recheck original Source and task authority.'
            return {'events': [event]}
        if params.get('name') != self.event_name:
            raise InboxError('unknown_event')
        arguments = params.get('arguments')
        if not isinstance(arguments, dict) or set(arguments) != {'mailbox_id'}:
            raise InboxError('invalid_arguments')
        mailbox = identifier(arguments['mailbox_id'])
        inbox = self.inbox_factory(mailbox).for_event(self.event_name)
        if self.event_name != EVENT_NAME and self.authorize_event(method, inbox, None) is not True:
            raise InboxError('formal_authority_required')
        delivery = params.get('delivery', {})
        if not isinstance(delivery, dict) or delivery.get('mode') != 'webhook':
            raise InboxError('unsupported_delivery')
        url = delivery.get('url')
        validate_url(url)
        sid = 'sub_' + hashlib.sha256(canonical(
            [inbox.owner, url, self.event_name, arguments]).encode()).hexdigest()
        if method == 'events/unsubscribe':
            await asyncio.to_thread(inbox.run, 'unsubscribe', id=sid)
            return {}
        if method != 'events/subscribe':
            raise InboxError('unknown_method')
        if params.get('cursor') is not None:
            raise InboxError('replay_unsupported')
        key = delivery.get('secret')
        secret_bytes(key)
        box = self.box
        now = int(time.time())
        requested = params.get('ttlMs', 86400000)
        if requested is not None and (type(requested) is not int or requested < 1000):
            raise InboxError('invalid_ttl')
        ttl = min(86400, requested // 1000) if requested is not None else 86400
        expires = min(now + ttl, token.expires_at or now)
        if expires <= now:
            raise InboxError('access_expired')
        old = (await asyncio.to_thread(inbox.run, 'subscription_get', id=sid))['subscription']
        challenge = secrets.token_urlsafe(32)
        body = canonical({'type': 'verification', 'challenge': challenge}).encode()
        verification_id = 'verify_' + secrets.token_hex(16)
        try:
            status, response = await self.sender(url, signed_headers(sid, verification_id, body, [key]), body, 3)
            echoed = json.loads(response).get('challenge', '')
            if not 200 <= status < 300 or not isinstance(echoed, str) or not hmac.compare_digest(challenge, echoed):
                raise InboxError('callback_challenge_failed')
        except (OSError, TimeoutError, httpcore.NetworkError, httpcore.TimeoutException, ValueError, AttributeError):
            raise InboxError('callback_verification_failed') from None
        protected = {'secret': key, 'access_token': token.token}
        if old:
            previous = box.open(old['protected'], sid)
            if previous['secret'] != key:
                protected.update(previous_secret=previous['secret'], previous_until=now + 120)
            elif previous.get('previous_until', 0) > now:
                protected.update(previous_secret=previous['previous_secret'],
                                 previous_until=previous['previous_until'])
        subscription = {'event_name': self.event_name, 'id': sid, 'url': url, 'expires_at': expires,
                        'access_until': token.expires_at, 'revision': secrets.token_hex(16),
                        'protected': box.seal(protected, sid)}
        await asyncio.to_thread(inbox.run, 'subscribe', subscription=subscription,
                                expected_revision=old['revision'] if old else '')
        return {'id': sid, 'refreshBefore': iso(expires), 'cursor': None, 'truncated': False}

    async def deliver(self, inbox, delivery_id, deadline=None):
        inbox = inbox.for_event(self.event_name)
        deadline = deadline or time.monotonic() + 10
        statuses = (await asyncio.to_thread(inbox.delivery_status, delivery_id))['items']
        if len(statuses) != 1 or statuses[0].get('event_name', EVENT_NAME) != self.event_name:
            raise InboxError('event_type_mismatch')
        lock = secrets.token_hex(16)
        acquired = await asyncio.to_thread(inbox.run, 'delivery_acquire', delivery_id=delivery_id, lock=lock)
        delivery, subscription = acquired['delivery'], acquired['subscription']
        attempts, status, state = 0, 0, 'pending'
        try:
            protected = self.box.open(subscription['protected'], subscription['id'])
            # Revalidate the stored OAuth proof before every delivery invocation.
            token = await self.verifier.verify_token(protected['access_token'])
            if (token is None or delivery.get('event_name', EVENT_NAME) != self.event_name
                    or subscription.get('event_name', EVENT_NAME) != self.event_name):
                state = 'terminal'
            else:
                body = canonical({'eventId': delivery['event_id'], 'name': self.event_name,
                    'timestamp': iso(delivery['created_at']), 'cursor': None,
                    'data': {'mailbox_id': inbox.mailbox_id, 'event_id': delivery['event_id'],
                             'synthetic': self.event_name == EVENT_NAME}}).encode()
                keys = [protected['secret']]
                if protected.get('previous_until', 0) > time.time():
                    keys.append(protected['previous_secret'])
                for attempt in range(3):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    # Recheck host authority per attempt; notifications do not grant Work access.
                    if self.event_name != EVENT_NAME and self.authorize_event('deliver', inbox, delivery['event_id']) is not True:
                        state = 'terminal'
                        break
                    # Unsubscribe and access-expiration checks precede each attempt.
                    current = (await asyncio.to_thread(inbox.run, 'subscription_get', id=subscription['id']))['subscription']
                    if not current or current['expires_at'] <= time.time() or current['access_until'] <= time.time():
                        state = 'terminal'
                        break
                    if current['revision'] != subscription['revision']:
                        break  # Refresh/secret rotation: next invocation uses the new snapshot.
                    attempts += 1
                    try:
                        status, _ = await self.sender(subscription['url'], signed_headers(
                            subscription['id'], delivery['event_id'], body, keys), body, min(2.5, remaining))
                    except (OSError, TimeoutError, httpcore.NetworkError, httpcore.TimeoutException, InboxError):
                        status = 0
                    if 200 <= status < 300:
                        state = 'delivered'
                        break
                    if status in (410, 413) or (400 <= status < 500 and status not in (408, 429)):
                        state = 'terminal'
                        break
                    if attempt < 2:
                        await asyncio.sleep(min(0.2 * 2 ** attempt, max(0, deadline - time.monotonic())))
        finally:
            # Cancelled/unknown sends remain recoverable under the same event ID.
            await asyncio.shield(asyncio.to_thread(inbox.run, 'delivery_finish', delivery_id=delivery_id,
                lock=lock, state=state, attempts=attempts, status=status))
        return {'delivery_id': delivery_id, 'state': state, 'attempts': attempts, 'last_status': status}

    async def dispatch(self, inbox, event_id):
        inbox = inbox.for_event(self.event_name)
        deadline = time.monotonic() + 10
        deliveries = (await asyncio.to_thread(inbox.delivery_status))['items']
        outcomes = []
        for delivery in deliveries:
            if delivery.get('event_name', EVENT_NAME) != self.event_name:
                continue
            if delivery['event_id'] != event_id or delivery['state'] in ('delivered', 'terminal'):
                continue
            if time.monotonic() >= deadline:
                break
            try:
                outcomes.append(await self.deliver(inbox, delivery['delivery_id'], deadline))
            except InboxError as error:
                outcomes.append({'delivery_id': delivery['delivery_id'], 'error': str(error)})
        return outcomes
