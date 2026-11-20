"""Optional one-shot wake notice after a successful inbox write.

This is a doorbell, not identity and not authority. It does not subscribe,
sign, retry, or write a delivery row. With the environment unset the caller
is unchanged.

Runtime configuration (read on each send; nothing is loaded at import):

- DEER_INBOX_DOORBELL_URL
    HTTPS URL. Unset, or an empty mailbox allowlist, leaves the feature off.
- DEER_INBOX_DOORBELL_KEY
    Sender key. It is placed only in the request header and is never logged.
- DEER_INBOX_DOORBELL_MAILBOXES
    Comma-separated mailbox ids. Unset or empty means ring nobody.
- DEER_INBOX_DOORBELL_KEY_HEADER
    Optional header name. Default is Authorization, sent as
    ``Authorization: Bearer <key>``. This repo's subscription webhook uses
    webhook-signature, not a sender key, and no in-repo doorbell receiver
    fixes a different name. Set this to the receiver's sender-key header
    (for example X-Sender-Key) to send the raw key with no Bearer prefix.

A non-HTTPS URL is rejected. The POST uses a 3 second timeout, does not
follow redirects, and is attempted at most once. Failure is logged without
the key and cannot roll back the send.
"""
import json
import logging
import os
import re
import socket
import urllib.error
import urllib.request
from urllib.parse import urlsplit

log = logging.getLogger('inbox_doorbell')

TYPE = 'deer.inbox.doorbell.v1'
TIMEOUT_SECONDS = 3
ENV_URL = 'DEER_INBOX_DOORBELL_URL'
ENV_KEY = 'DEER_INBOX_DOORBELL_KEY'
ENV_MAILBOXES = 'DEER_INBOX_DOORBELL_MAILBOXES'
ENV_HEADER = 'DEER_INBOX_DOORBELL_KEY_HEADER'
DEFAULT_HEADER = 'Authorization'
IDENTIFIER = re.compile(r'^[A-Za-z0-9_-]{1,80}$')
EVENT_ID = re.compile(r'^[0-9a-f]{64}$')
HEADER_NAME = re.compile(r'^[A-Za-z][A-Za-z0-9-]{0,63}$')
PAYLOAD_FIELDS = ('created_at', 'event_id', 'mailbox_id', 'sequence', 'type')


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _log(reason, mailbox_id=None, sequence=None, event_id=None, status=None, error_type=None):
    fields = {}
    if isinstance(mailbox_id, str) and IDENTIFIER.fullmatch(mailbox_id):
        fields['mailbox_id'] = mailbox_id
    if type(sequence) is int:
        fields['sequence'] = sequence
    if isinstance(event_id, str) and EVENT_ID.fullmatch(event_id):
        fields['event_id'] = event_id
    if type(status) is int:
        fields['status'] = status
    if isinstance(error_type, str) and error_type.isidentifier() and len(error_type) <= 80:
        fields['error_type'] = error_type
    # Success is informational. Failures stay warnings and never include the key.
    log.log(logging.INFO if reason == 'sent' else logging.WARNING, 'inbox doorbell %s %s', reason, fields)


def _whole(value):
    if type(value) is bool:
        return None
    if type(value) is int:
        return value
    if type(value) is float and value.is_integer() and abs(value) < 2**53:
        return int(value)
    return None


def _valid_key(key):
    return isinstance(key, str) and 1 <= len(key) <= 256 and all(33 <= ord(c) <= 126 for c in key)


def _mailboxes(raw):
    if not isinstance(raw, str) or not raw.strip():
        return None
    found = set()
    for part in raw.split(','):
        item = part.strip()
        if item and IDENTIFIER.fullmatch(item):
            found.add(item)
    return found or None


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


def _header(name, key):
    if not isinstance(name, str) or not HEADER_NAME.fullmatch(name):
        return None
    if name.lower() == 'authorization':
        return name, 'Bearer ' + key
    return name, key


def post_once(url, headers, body, timeout, opener=None):
    """One POST. No redirect, no retry, no proxy from the environment."""
    if timeout is None or timeout <= 0 or timeout > TIMEOUT_SECONDS or _https_url(url) != url:
        raise ValueError('rejected_callback')
    request = urllib.request.Request(url, data=body, headers=headers, method='POST')
    if opener is None:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    with opener.open(request, timeout=timeout) as response:
        return response.status


def notify(mailbox_id, event_id, sequence, created_at, *, environ=None, poster=None):
    """Ring at most once. Never raises."""
    try:
        _notify(mailbox_id, event_id, sequence, created_at, environ=environ, poster=poster or post_once)
    except Exception as exc:
        _log('failed', mailbox_id=mailbox_id, event_id=event_id, error_type=type(exc).__name__)


def _notify(mailbox_id, event_id, sequence, created_at, *, environ, poster):
    env = os.environ if environ is None else environ
    boxes = _mailboxes(env.get(ENV_MAILBOXES))
    if not boxes or mailbox_id not in boxes:
        return
    key = env.get(ENV_KEY)
    if not _valid_key(key):
        _log('invalid_key', mailbox_id=mailbox_id)
        return
    raw_url = env.get(ENV_URL)
    if not isinstance(raw_url, str) or not raw_url.strip():
        _log('missing_url', mailbox_id=mailbox_id)
        return
    if urlsplit(raw_url.strip()).scheme != 'https':
        _log('rejected_non_https', mailbox_id=mailbox_id)
        return
    url = _https_url(raw_url)
    if url is None:
        _log('invalid_url', mailbox_id=mailbox_id)
        return
    header = _header((env.get(ENV_HEADER) or DEFAULT_HEADER).strip(), key)
    if header is None:
        _log('invalid_header', mailbox_id=mailbox_id)
        return
    sequence = _whole(sequence)
    created_at = _whole(created_at)
    if (not isinstance(mailbox_id, str) or not IDENTIFIER.fullmatch(mailbox_id)
            or not isinstance(event_id, str) or not EVENT_ID.fullmatch(event_id)
            or sequence is None or not 1 <= sequence <= 10**15
            or created_at is None or not 0 <= created_at <= 10**15):
        _log('invalid_receipt', mailbox_id=mailbox_id if isinstance(mailbox_id, str) else None)
        return
    payload = {
        'type': TYPE,
        'mailbox_id': mailbox_id,
        'sequence': sequence,
        'event_id': event_id,
        'created_at': created_at,
    }
    if set(payload) != set(PAYLOAD_FIELDS):
        _log('invalid_receipt', mailbox_id=mailbox_id)
        return
    body = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
    name, value = header
    headers = {'Content-Type': 'application/json', name: value}
    try:
        status = poster(url, headers, body, TIMEOUT_SECONDS)
    except urllib.error.HTTPError as exc:
        _log('http_error', mailbox_id=mailbox_id, sequence=sequence, event_id=event_id, status=exc.code)
        return
    except (TimeoutError, socket.timeout):
        _log('timeout', mailbox_id=mailbox_id, sequence=sequence, event_id=event_id)
        return
    except Exception as exc:
        _log('failed', mailbox_id=mailbox_id, sequence=sequence, event_id=event_id,
             error_type=type(exc).__name__)
        return
    if type(status) is not int:
        status = None
    _log('sent', mailbox_id=mailbox_id, sequence=sequence, event_id=event_id, status=status)
