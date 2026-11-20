"""Optional one-shot wake notice after a stored chat mention.

This is a doorbell, not identity and not authority. It does not subscribe,
sign, retry, or write a delivery row. With the environment unset the chat
write is unchanged.

Runtime configuration (read on each post; nothing is loaded at import).
Both must be set or Grok is not rung:

- XJ_CHAT_DOORBELL_GROK_URL
    HTTPS URL. Any other scheme is not called.
- XJ_CHAT_DOORBELL_GROK_AUTHORIZATION
    Sent as the Authorization header value, with no extra prefix. It is
    never logged and never returned to the caller.

Grok is the first member. The POST body is only type, room, seq, member
and created (Unix seconds). It never includes the message text or the
speaker. Timeout is 3 seconds, redirects are not followed, and a second
ring of the same room, sequence and member is skipped. Failure cannot
roll back the chat write.
"""
import json
import logging
import os
import re
import socket
import urllib.error
import urllib.request
from urllib.parse import urlsplit

log = logging.getLogger('chat_doorbell')

TYPE = 'chat.mention'
TIMEOUT_SECONDS = 3
ENV_URL = 'XJ_CHAT_DOORBELL_GROK_URL'
ENV_AUTHORIZATION = 'XJ_CHAT_DOORBELL_GROK_AUTHORIZATION'
TARGETS = {'Grok': (ENV_URL, ENV_AUTHORIZATION)}
ROOM = re.compile(r'^[\w-]{1,40}$')
PAYLOAD_FIELDS = ('created', 'member', 'room', 'seq', 'type')
REASONS = frozenset({
    'dispatched', 'failed', 'timeout', 'http_error', 'rejected_non_https',
    'invalid_url', 'invalid_authorization', 'duplicate', 'claim_failed',
    'invalid_receipt',
})
DELIVERIES = frozenset({'dispatched', 'failed', 'skipped'})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _log(reason, room=None, seq=None, member=None, status=None, error_type=None):
    if reason not in REASONS:
        reason = 'failed'
    fields = {}
    if isinstance(room, str) and ROOM.fullmatch(room):
        fields['room'] = room
    if type(seq) is int:
        fields['seq'] = seq
    if member in TARGETS:
        fields['member'] = member
    if type(status) is int:
        fields['status'] = status
    if isinstance(error_type, str) and error_type.isidentifier() and len(error_type) <= 80:
        fields['error_type'] = error_type
    # Success is informational. Failures stay warnings and never include the URL or header.
    log.log(logging.INFO if reason in ('dispatched', 'duplicate') else logging.WARNING,
            'chat doorbell %s %s', reason, fields)


def _https_url(url):
    if not isinstance(url, str):
        return None
    url = url.strip()
    if not url or len(url) > 2048 or any(ord(c) < 33 or ord(c) > 126 for c in url):
        return None
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme != 'https' or not parsed.hostname:
        return None
    if parsed.username or parsed.password or parsed.fragment:
        return None
    if parsed.port == 0:
        return None
    return url


def _valid_authorization(value):
    return (isinstance(value, str) and 1 <= len(value) <= 2048
            and all(32 <= ord(c) <= 126 for c in value))


def resolve_target(member, env):
    """None when this member has no doorbell. Otherwise ('skip', reason) or ('post', url, auth)."""
    slot = TARGETS.get(member)
    if slot is None:
        return None
    raw_url = env.get(slot[0])
    raw_auth = env.get(slot[1])
    if not isinstance(raw_url, str) or not raw_url.strip():
        return None
    if not isinstance(raw_auth, str) or not raw_auth.strip():
        return None
    auth = raw_auth.strip()
    if not _valid_authorization(auth):
        return ('skip', 'invalid_authorization')
    if urlsplit(raw_url.strip()).scheme != 'https':
        return ('skip', 'rejected_non_https')
    url = _https_url(raw_url)
    if url is None:
        return ('skip', 'invalid_url')
    return ('post', url, auth)


def _payload_ok(room, seq, member, created):
    return (isinstance(room, str) and ROOM.fullmatch(room) and member in TARGETS
            and type(seq) is int and 1 <= seq <= 10**15
            and type(created) is int and 0 <= created <= 10**15)


def _entry(member, delivery):
    if delivery not in DELIVERIES:
        delivery = 'failed'
    if member not in TARGETS:
        return {'delivery': 'failed'}
    return {'to': member, 'delivery': delivery}


def post_once(url, headers, body, timeout, opener=None):
    """One POST. No redirect, no retry, no proxy from the environment."""
    if timeout is None or timeout <= 0 or timeout > TIMEOUT_SECONDS or _https_url(url) != url:
        raise ValueError('rejected_callback')
    request = urllib.request.Request(url, data=body, headers=headers, method='POST')
    if opener is None:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    with opener.open(request, timeout=timeout) as response:
        return response.status


def _claim(store, room, seq, member):
    try:
        claimed = store.claim_doorbell(room, seq, member)
    except Exception:
        return None
    if claimed is True:
        return True
    if claimed is False:
        return False
    return None


def _send(poster, url, authorization, room, seq, member, created):
    payload = {'type': TYPE, 'room': room, 'seq': seq, 'member': member, 'created': created}
    if set(payload) != set(PAYLOAD_FIELDS):
        _log('invalid_receipt', room, seq, member)
        return 'skipped'
    body = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    headers = {'Content-Type': 'application/json', 'Authorization': authorization}
    try:
        status = poster(url, headers, body, TIMEOUT_SECONDS)
    except urllib.error.HTTPError as exc:
        _log('http_error', room, seq, member, status=exc.code)
        return 'failed'
    except (TimeoutError, socket.timeout):
        _log('timeout', room, seq, member)
        return 'failed'
    except Exception as exc:
        _log('failed', room, seq, member, error_type=type(exc).__name__)
        return 'failed'
    if type(status) is not int or not 200 <= status <= 299:
        _log('http_error', room, seq, member, status=status if type(status) is int else None)
        return 'failed'
    _log('dispatched', room, seq, member, status=status)
    return 'dispatched'


def ring_mentions(message, speaker, store, *, environ=None, poster=None):
    """Ring each configured mention at most once. Never raises."""
    try:
        return _ring(message, speaker, store, environ=environ, poster=poster or post_once)
    except Exception as exc:
        _log('failed', error_type=type(exc).__name__)
        return [{'delivery': 'failed'}]


def _ring(message, speaker, store, *, environ, poster):
    env = os.environ if environ is None else environ
    results = []
    mentions = message.get('mentions') if isinstance(message, dict) else None
    if not isinstance(mentions, list):
        return results
    room = message.get('room')
    seq = message.get('seq')
    created = message.get('at')
    seen = set()
    for member in mentions:
        if not isinstance(member, str) or member in seen or member == speaker:
            continue
        seen.add(member)
        outcome = resolve_target(member, env)
        if outcome is None:
            continue
        if outcome[0] == 'skip':
            _log(outcome[1], room if isinstance(room, str) else None, seq, member)
            results.append(_entry(member, 'skipped'))
            continue
        if not _payload_ok(room, seq, member, created):
            _log('invalid_receipt', room if isinstance(room, str) else None, seq, member)
            results.append(_entry(member, 'skipped'))
            continue
        claim = _claim(store, room, seq, member)
        if claim is False:
            _log('duplicate', room, seq, member)
            results.append(_entry(member, 'skipped'))
            continue
        if claim is not True:
            _log('claim_failed', room, seq, member)
            results.append(_entry(member, 'failed'))
            continue
        results.append(_entry(member, _send(poster, outcome[1], outcome[2], room, seq, member, created)))
    return results
