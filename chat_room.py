"""Group chat room tools on the existing Deer MCP and Redis. Default off.

Humans and AIs (GPT, Claude, Grok, Deer) post to a shared room through the same
OAuth-protected MCP service. Messages live in the existing Redis under a separate
namespace (xj:chat-room:v1); the formal mailbox is never used for chat history.

When a post @mentions a member listed in XJ_CHAT_MAILBOX_WAKE (default: GPT),
one short notice is sent through the original formal mailbox path
(gateway_internal_v1 / gateway.continuation.ready), so an existing subscription
can wake that member. The notice carries no task, Source or Work authority.

Speaker names are self-declared: every caller shares one OAuth owner. Each stored
message records the existing caller-context observation (conversation
fingerprint only) for later review; it is not an identity proof.

XJ_CHAT_PUSH_URLS optionally maps members to an https push endpoint, e.g.
"Claude=https://ntfy.sh/<random-topic>". When a post @mentions such a member,
one small JSON notice {type, room, seq, to, from} is POSTed there so a listener
can wake that member. The notice never contains message text. A failed push
never affects the stored message. Unset or blank sends nothing.

Enable with XJ_CHAT_ROOM_ENABLED=true. No credential, subscription, schedule or
Redis instance is created here.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import time
import urllib.request
from urllib.parse import urlsplit

NAMESPACE = 'xj:chat-room:v1'
MAX_TEXT = 8000
MAX_READ = 200
DEFAULT_MEMBERS = '祐:human,GPT:ai,Claude:ai,Grok:ai,Deer:ai'
DEFAULT_WAKE = 'GPT'
DEFAULT_TTL_DAYS = 30
DEFAULT_MAX_MESSAGES = 5000
DEFAULT_AI_STREAK_LIMIT = 8
NOOP = 'NOOP'
ROOM = re.compile(r'^[\w-]{1,40}$')
MENTION = re.compile(r'@([^\s@,，。:：!！?？()（）\[\]]+)')
SCRIPT = Path(__file__).with_name('chat_room.lua').read_text()


class ChatError(Exception):
    """Stable, non-secret error code returned to MCP callers."""


def _int_env(env, name, default, low, high):
    raw = env.get(name, '')
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ChatError(f'invalid_config_{name}') from None
    if not low <= value <= high:
        raise ChatError(f'invalid_config_{name}')
    return value


class ChatConfig:
    def __init__(self, env=None):
        env = os.environ if env is None else env
        get = env.get
        self.enabled = get('XJ_CHAT_ROOM_ENABLED') == 'true'
        self.members = {}
        for item in (get('XJ_CHAT_MEMBERS') or DEFAULT_MEMBERS).split(','):
            name, _, kind = item.strip().partition(':')
            if not name or kind not in ('human', 'ai') or not ROOM.fullmatch(name):
                raise ChatError('invalid_config_XJ_CHAT_MEMBERS')
            self.members[name] = kind
        if not any(kind == 'human' for kind in self.members.values()):
            raise ChatError('invalid_config_XJ_CHAT_MEMBERS')
        wake = get('XJ_CHAT_MAILBOX_WAKE', DEFAULT_WAKE)
        self.mailbox_wake = {w.strip() for w in wake.split(',') if w.strip()}
        if not self.mailbox_wake <= set(self.members):
            raise ChatError('invalid_config_XJ_CHAT_MAILBOX_WAKE')
        self.push_urls = {}
        for item in (get('XJ_CHAT_PUSH_URLS') or '').split(','):
            item = item.strip()
            if not item:
                continue
            name, _, url = item.partition('=')
            name, url = name.strip(), url.strip()
            parts = urlsplit(url)
            if (name not in self.members or parts.scheme != 'https' or not parts.hostname
                    or parts.query or parts.fragment or parts.username or parts.password):
                raise ChatError('invalid_config_XJ_CHAT_PUSH_URLS')
            self.push_urls[name] = url
        self.ttl_seconds = 86400 * _int_env(env, 'XJ_CHAT_TTL_DAYS', DEFAULT_TTL_DAYS, 1, 365)
        self.max_messages = _int_env(env, 'XJ_CHAT_MAX_MESSAGES', DEFAULT_MAX_MESSAGES, 10, 20000)
        self.ai_streak_limit = _int_env(env, 'XJ_CHAT_AI_STREAK_LIMIT',
                                        DEFAULT_AI_STREAK_LIMIT, 1, 100)

    def canonical_name(self, name):
        """Exact name, or a case-insensitive ASCII match. None if unknown."""
        if name in self.members:
            return name
        folded = name.casefold()
        for member in self.members:
            if member.casefold() == folded:
                return member
        return None


def default_client():
    """Same Redis as the inbox: preview only, TLS with certificate checks."""
    from redis import Redis
    if os.getenv('VERCEL_ENV') != 'preview':
        raise ChatError('preview_only')
    url = os.getenv('XJ_PREVIEW_REDIS_URL') or os.getenv('REDIS_URL', '')
    endpoint = urlsplit(url)
    if endpoint.scheme != 'rediss' or not endpoint.hostname or endpoint.query or endpoint.fragment:
        raise ChatError('redis_configuration_unavailable')
    return Redis.from_url(url, decode_responses=True, ssl_cert_reqs='required',
                          ssl_check_hostname=True, socket_connect_timeout=3, socket_timeout=3)


class ChatStore:
    """Per-owner rooms. One Lua call appends; reads never extend TTL."""

    def __init__(self, owner, config, client_factory=default_client):
        self.owner = owner
        self.config = config
        self.client_factory = client_factory
        self.prefix = NAMESPACE + ':' + hashlib.sha256(owner.encode()).hexdigest()[:32]

    def _keys(self, room):
        tag = '{' + hashlib.sha256((self.prefix + '|' + room).encode()).hexdigest() + '}'
        base = self.prefix + ':room:' + tag
        return base + ':seq', base + ':log', base + ':ai_streak'

    def _rooms_key(self):
        return self.prefix + ':rooms'

    def _run(self, fn):
        from redis.exceptions import RedisError
        try:
            client = self.client_factory()
            try:
                return fn(client)
            finally:
                close = getattr(client, 'close', None)
                if callable(close):
                    close()
        except ChatError:
            raise
        except (ValueError, OSError, RedisError):
            raise ChatError('outcome_unknown_read_room_before_retry') from None

    def append(self, room, speaker, kind, text, mentions, origin):
        message = {'room': room, 'speaker': speaker, 'kind': kind, 'text': text,
                   'mentions': mentions, 'at': int(time.time()), 'origin': origin}
        args = (kind, self.config.ai_streak_limit, self.config.max_messages,
                self.config.ttl_seconds, json.dumps(message, ensure_ascii=False))

        def go(client):
            raw = client.eval(SCRIPT, 3, *self._keys(room), *args)
            rooms = self._rooms_key()
            client.zadd(rooms, {room: time.time()})
            client.expire(rooms, self.config.ttl_seconds)
            return raw
        value = json.loads(self._run(go))
        if value.get('error') == 'ai_streak_limit':
            raise ChatError('ai_streak_limit_wait_for_human')
        return json.loads(value['message']), value['ai_streak']

    def read(self, room, after_seq, limit):
        seq_key, log_key, _ = self._keys(room)

        def go(client):
            pipe = client.pipeline(transaction=True)
            pipe.get(seq_key)
            pipe.llen(log_key)
            last, length = pipe.execute()
            last = int(last or 0)
            first = last - length + 1  # seq of the oldest message still kept
            start = max(after_seq + 1, first)
            if length == 0 or start > last:
                return last, first if length else last + 1, []
            offset = start - first
            items = client.lrange(log_key, offset, offset + limit - 1)
            return last, first, [json.loads(item) for item in items]
        last, first, items = self._run(go)
        return {'room': room, 'latest_seq': last, 'oldest_kept_seq': first,
                'items': items, 'has_more': bool(items) and items[-1]['seq'] < last}

    def rooms(self):
        return self._run(lambda client: client.zrevrange(self._rooms_key(), 0, 99))


def parse_mentions(config, text, speaker):
    found = []
    for raw in MENTION.findall(text):
        name = config.canonical_name(raw)
        if name and name != speaker and name not in found:
            found.append(name)
    return found


def wake_operation_key(room, seq, target):
    # Fixed per (room, message, target): a resend deduplicates in the mailbox.
    digest = hashlib.sha256(f'{room}|{seq}|{target}'.encode()).hexdigest()[:40]
    return f'chat-wake-{digest}'


def wake_notice(message, target):
    preview = message['text'][:300]
    notice = {'type': 'chat.mention', 'room': message['room'], 'seq': message['seq'],
              'to': target, 'from': message['speaker'], 'preview': preview,
              'next': f"chat_read(room='{message['room']}', after_seq={message['seq'] - 1})"}
    body = json.dumps(notice, ensure_ascii=False, separators=(',', ':'))
    while len(body) > 1024:  # mailbox limit; shorten the preview only
        preview = preview[:-50]
        notice['preview'] = preview
        body = json.dumps(notice, ensure_ascii=False, separators=(',', ':'))
    return body


def push_notice(message, target):
    """Wake hint only: no text, no preview, no authority."""
    return json.dumps({'type': 'chat.mention', 'room': message['room'], 'seq': message['seq'],
                       'to': target, 'from': message['speaker']},
                      ensure_ascii=False, separators=(',', ':'))


def default_push(url, body):
    req = urllib.request.Request(url, data=body.encode(), method='POST',
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status


def install_chat_tools(mcp, principal, oauth_meta, *, notify=None,
                       caller_context=None, config=None, client_factory=default_client,
                       push=default_push):
    """Register chat tools when enabled. notify(owner, op_key, body) -> receipt.

    Returns the registered tool names (empty when disabled)."""
    config = config or ChatConfig()
    if not config.enabled:
        return []
    from mcp.types import ToolAnnotations
    from mcp.server.fastmcp import Context

    def annotation(read=False):
        return ToolAnnotations(readOnlyHint=read, destructiveHint=False,
                               idempotentHint=read, openWorldHint=False)

    def room_name(room):
        if not isinstance(room, str) or not ROOM.fullmatch(room):
            raise ChatError('invalid_room')
        return room

    def member(speaker):
        name = config.canonical_name(speaker) if isinstance(speaker, str) else None
        if name is None:
            raise ChatError('unknown_speaker')
        return name

    def bounded(after_seq, limit):
        if type(after_seq) is not int or after_seq < 0:
            raise ChatError('invalid_after_seq')
        if type(limit) is not int or not 1 <= limit <= MAX_READ:
            raise ChatError('invalid_limit')

    @mcp.tool(meta=oauth_meta, annotations=annotation())
    async def chat_post(room: str, speaker: str, text: str, ctx: Context = None) -> dict:
        """Post to a group chat room as `speaker` (self-declared; not identity proof).
        Write @Name to call a member. Members who are called reply with chat_post.
        Text 'NOOP' means you choose not to reply and stores nothing. If the AI
        streak limit is reached, stop and wait for a human message."""
        owner = principal()
        room = room_name(room)
        speaker = member(speaker)
        if not isinstance(text, str):
            raise ChatError('invalid_text')
        text = text.strip()
        if text == NOOP:
            return {'stored': False, 'reason': 'noop'}
        if not 1 <= len(text) <= MAX_TEXT:
            raise ChatError('invalid_text_length')
        kind = config.members[speaker]
        mentions = parse_mentions(config, text, speaker)
        origin = caller_context(ctx, owner) if caller_context else None
        store = ChatStore(owner, config, client_factory)
        message, streak = await asyncio.to_thread(
            store.append, room, speaker, kind, text, mentions, origin)
        wakes = []
        for target in mentions:
            if target not in config.mailbox_wake or notify is None:
                continue
            op = wake_operation_key(room, message['seq'], target)
            try:
                receipt = await notify(owner, op, wake_notice(message, target))
                wakes.append({'to': target, 'operation_key': op,
                              'event_id': receipt.get('event_id'),
                              'duplicate': receipt.get('duplicate'),
                              'delivery': receipt.get('delivery_status', 'dispatched')})
            except Exception:
                # The chat message is stored; resend reuses the same key safely.
                wakes.append({'to': target, 'operation_key': op,
                              'delivery': 'unconfirmed_resend_same_key'})
        pushes = []
        for target in mentions:
            url = config.push_urls.get(target)
            if not url or push is None:
                continue
            try:
                status = await asyncio.to_thread(push, url, push_notice(message, target))
                pushes.append({'to': target, 'delivery': 'sent' if 200 <= status < 300 else 'rejected'})
            except Exception:
                pushes.append({'to': target, 'delivery': 'failed'})
        return {'stored': True, 'message': message, 'ai_streak': streak,
                'ai_streak_limit': config.ai_streak_limit, 'mailbox_wakes': wakes,
                'push_wakes': pushes}

    @mcp.tool(meta=oauth_meta, annotations=annotation(read=True))
    async def chat_read(room: str, after_seq: int = 0, limit: int = 50) -> dict:
        """Read room messages with seq > after_seq, oldest first. Use latest_seq
        as the next after_seq. Messages older than the retention window are gone."""
        owner = principal()
        bounded(after_seq, limit)
        store = ChatStore(owner, config, client_factory)
        return await asyncio.to_thread(store.read, room_name(room), after_seq, limit)

    @mcp.tool(meta=oauth_meta, annotations=annotation(read=True))
    async def chat_mentions(room: str, speaker: str, after_seq: int = 0) -> dict:
        """Messages in `room` after after_seq that @mention `speaker`. Returns
        latest_seq to use as the next after_seq. Reading does not mark anything."""
        owner = principal()
        bounded(after_seq, MAX_READ)
        speaker = member(speaker)
        store = ChatStore(owner, config, client_factory)
        page = await asyncio.to_thread(store.read, room_name(room), after_seq, MAX_READ)
        hits = [m for m in page['items'] if speaker in m.get('mentions', [])]
        return {'room': page['room'], 'speaker': speaker, 'latest_seq': page['latest_seq'],
                'scanned_to_seq': page['items'][-1]['seq'] if page['items'] else after_seq,
                'has_more': page['has_more'], 'items': hits}

    @mcp.tool(meta=oauth_meta, annotations=annotation(read=True))
    async def chat_rooms() -> dict:
        """List recent rooms and the configured members (name and human/ai)."""
        owner = principal()
        store = ChatStore(owner, config, client_factory)
        rooms = await asyncio.to_thread(store.rooms)
        return {'rooms': rooms,
                'members': [{'name': n, 'kind': k} for n, k in config.members.items()],
                'mailbox_wake': sorted(config.mailbox_wake),
                'push_wake': sorted(config.push_urls),
                'ai_streak_limit': config.ai_streak_limit,
                'retention_days': config.ttl_seconds // 86400}

    return ['chat_post', 'chat_read', 'chat_mentions', 'chat_rooms']


def formal_notifier(mailbox, formal, mailbox_id, event_name):
    """Build notify() on the ORIGINAL formal mailbox send path, unchanged."""
    async def notify(owner, operation_key, body):
        inbox = mailbox(mailbox_id).for_event(event_name)
        if inbox.owner != owner:
            raise ChatError('owner_changed')
        receipt = await asyncio.to_thread(inbox.send, operation_key, body,
                                          event_name=event_name)
        try:
            receipt['deliveries'] = await formal.dispatch(inbox, receipt['event_id'])
        except Exception:
            receipt['delivery_status'] = 'unconfirmed_query_delivery_status'
        return receipt
    return notify
