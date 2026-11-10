"""Owner-scoped synthetic inbox. No formal Source or Work identity authority."""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
from urllib.parse import urlsplit

from redis import Redis
from redis.exceptions import RedisError

SCRIPT = Path(__file__).with_name('inbox.lua').read_text()
IDENTIFIER = re.compile(r'^[A-Za-z0-9_-]{1,80}$')
EVENT_ID = re.compile(r'^[0-9a-f]{64}$')


class InboxError(Exception):
    """A stable, non-secret error code suitable for tool and RPC responses."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise InboxError('invalid_identifier')
    return value


def event_id(value):
    if not isinstance(value, str) or not EVENT_ID.fullmatch(value):
        raise InboxError('invalid_event_id')
    return value


class Inbox:
    def __init__(self, owner, mailbox_id, client=None, *, event_name='inbox.message.created'):
        if event_name not in ('inbox.message.created', 'gateway.continuation.ready'):
            raise InboxError('unknown_event')
        self.event_name = event_name
        self.owner = owner
        self.mailbox_id = identifier(mailbox_id)
        digest = hashlib.sha256((owner + '|' + mailbox_id).encode()).hexdigest()
        self.key = 'xj:synthetic-inbox:v1:{' + digest + '}'
        self.client = client

    def for_event(self, name):
        return Inbox(self.owner, self.mailbox_id, self.client, event_name=name)

    def run(self, action, **request):
        request.setdefault('event_name', self.event_name)
        request['epoch'] = secrets.token_hex(16)
        try:
            if self.client is not None:
                raw = self.client.eval(SCRIPT, 1, self.key, action, canonical(request))
            else:
                if os.getenv('VERCEL_ENV') != 'preview':
                    raise InboxError('preview_only')
                url = os.getenv('XJ_PREVIEW_REDIS_URL') or os.getenv('REDIS_URL', '')
                endpoint = urlsplit(url)
                if endpoint.scheme != 'rediss' or not endpoint.hostname or endpoint.query or endpoint.fragment:
                    raise InboxError('redis_configuration_unavailable')
                with Redis.from_url(url, decode_responses=True, ssl_cert_reqs='required',
                                    ssl_check_hostname=True, socket_connect_timeout=3,
                                    socket_timeout=3) as client:
                    raw = client.eval(SCRIPT, 1, self.key, action, canonical(request))
            value = json.loads(raw)
        except (ValueError, OSError, RedisError):
            raise InboxError('outcome_unknown_query_same_operation') from None
        if 'error' in value:
            raise InboxError(value['error'])
        return value

    def send(self, operation_key, message, *, event_name='inbox.message.created'):
        if event_name not in ('inbox.message.created', 'gateway.continuation.ready'):
            raise InboxError('unknown_event')
        identifier(operation_key)
        if not isinstance(message, str) or not 1 <= len(message) <= 1024:
            raise InboxError('invalid_message')
        eid = hashlib.sha256((self.key + '|' + operation_key + '|' + secrets.token_hex(32)).encode()).hexdigest()
        digest = hashlib.sha256(canonical([operation_key, message] if event_name == 'inbox.message.created' else [event_name, operation_key, message]).encode()).hexdigest()
        return self.run('send', event_id=eid, digest=digest,
                        operation_key=operation_key, message=message, event_name=event_name)

    def list(self, cursor='', limit=20):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise InboxError('invalid_limit')
        epoch, after = '', 0
        if cursor:
            match = re.fullmatch(r'([0-9a-f]{32}):([0-9]{1,15})', cursor)
            if not match:
                raise InboxError('invalid_cursor')
            epoch, after = match[1], int(match[2])
        value = self.run('list', cursor_epoch=epoch, after=after, limit=limit)
        value['items'] = value['items'] or []
        return value

    def claim(self, eid):
        token = secrets.token_urlsafe(32)
        value = self.run('claim', event_id=event_id(eid),
                         lease_digest=hashlib.sha256(token.encode()).hexdigest())
        return {**value, 'claim_token': token}

    def settle(self, action, eid, token, version):
        if action not in ('ack', 'release') or not isinstance(token, str) or not 20 <= len(token) <= 100:
            raise InboxError('invalid_claim')
        if type(version) is not int or version < 1:
            raise InboxError('invalid_version')
        return self.run(action, event_id=event_id(eid), version=version,
                        lease_digest=hashlib.sha256(token.encode()).hexdigest())

    def delivery_status(self, delivery_id=''):
        if delivery_id and not re.fullmatch(r'[0-9a-f]{64}:sub_[0-9a-f]{64}', delivery_id):
            raise InboxError('invalid_delivery_id')
        value = self.run('delivery_status', delivery_id=delivery_id)
        value['items'] = value['items'] or []
        return value
