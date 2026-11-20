"""Create-only Writer for the Owner's MEGA "知識庫" folder (stage 2).

Owner ruling 2026-11-21: writes are allowed only inside 知識庫, only as NEW
files (no update, no overwrite, no delete), and each write needs its own
phone-approved ticket. Default off.

  XJ_MEGA_KB_WRITE_ENABLED=true   enable (also needs the stage-1 reader env)
  XJ_MEGA_EMAIL                   Sensitive, entered by the Owner
  XJ_MEGA_PASSWORD                Sensitive, entered by the Owner

Ticket rule. The write's operation_key must be an approval ticket that:
  * is approved or claimed and still inside the 24h claim window,
  * has a title starting with "MEGA知識庫寫入",
  * has a plan line exactly "mega_write parent=<parent_ref> name=<name>"
    matching this call (parent_ref "kb:root" means the 知識庫 folder itself).
A ticket authorizes one write; it is consumed before the upload starts.

Path rule. parent_ref must be a folder ref inside the exported 知識庫 tree
(the same tree the Reader serves); the name must not already exist there.

MEGA formats (account session, v2 accounts):
  login    PBKDF2-HMAC-SHA512(password, salt, 100000) -> pw key | user hash;
           master key = AES-ECB(pw key)^-1 (k); session id = RSA(privk)^-1 (csid)
  upload   AES-128-CTR content, chunk CBC-MACs -> 8-byte meta MAC
  node     key = [aes ^ (nonce|mac), nonce|mac] encrypted with the master key,
           plus a "cr" share-key entry so the folder link can decrypt it too
Errors are fixed codes; no email, password, link, key or MEGA URL is echoed.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import time
import unicodedata
import urllib.request

from mega_library import API, MegaDenied, b64d, ecb_decrypt, version_of

TITLE_PREFIX = 'MEGA知識庫寫入'
ROOT_REF = 'kb:root'  # the 知識庫 folder itself, so tickets never carry its handle
PLAN_LINE = re.compile(r'^mega_write parent=(kb:root|m:[A-Za-z0-9_-]{8}) name=([^\n/\\]{1,160})$', re.M)
CLAIM_WINDOW = 24 * 3600
MAX_WRITE_BYTES = 16384
NAMESPACE = 'xj:mega-write:v1'


def b64e(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def _cipher():
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    return Cipher, algorithms, modes


def ecb_encrypt(key, data):
    Cipher, algorithms, modes = _cipher()
    e = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return e.update(data) + e.finalize()


def cbc_encrypt(key, iv, data):
    Cipher, algorithms, modes = _cipher()
    e = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return e.update(data) + e.finalize()


def ctr_encrypt(key, nonce, data):
    Cipher, algorithms, modes = _cipher()
    e = Cipher(algorithms.AES(key), modes.CTR(nonce + b'\0' * 8)).encryptor()
    return e.update(data) + e.finalize()


def encrypt_attrs(key, obj):
    plain = b'MEGA' + json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode()
    plain += b'\0' * (-len(plain) % 16)
    return b64e(cbc_encrypt(key, b'\0' * 16, plain))


def chunk_bounds(size):
    """MEGA chunking: 128K, 256K, ... up to 1M, then 1M each."""
    out, start, step = [], 0, 0x20000
    while start < size or not out:
        end = min(start + step, size)
        out.append((start, end))
        start = end
        step = min(step + 0x20000, 0x100000)
        if start >= size:
            break
    return out


def meta_mac(key, nonce, data):
    file_mac = b'\0' * 16
    for start, end in chunk_bounds(len(data)):
        chunk = data[start:end]
        chunk += b'\0' * (-len(chunk) % 16)
        block = cbc_encrypt(key, nonce + nonce, chunk)[-16:] if chunk else ecb_encrypt(key, nonce + nonce)
        file_mac = ecb_encrypt(key, bytes(a ^ b for a, b in zip(file_mac, block)))
    w = [int.from_bytes(file_mac[i:i + 4], 'big') for i in range(0, 16, 4)]
    return (w[0] ^ w[1]).to_bytes(4, 'big') + (w[2] ^ w[3]).to_bytes(4, 'big')


def read_mpis(raw, count):
    out, pos = [], 0
    for _ in range(count):
        bits = int.from_bytes(raw[pos:pos + 2], 'big')
        n = (bits + 7) // 8
        out.append(int.from_bytes(raw[pos + 2:pos + 2 + n], 'big'))
        pos += 2 + n
    return out


def _post_bytes(url, data):
    req = urllib.request.Request(url, data=data, method='POST',
                                 headers={'Content-Type': 'application/octet-stream'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode('ascii', 'replace')


class MegaSession:
    """Logged-in account session; credentials live only in this object."""

    def __init__(self, email, password, post_json, post_bytes=_post_bytes):
        if not email or not password:
            raise MegaDenied('MEGA_WRITE_CREDENTIALS_MISSING')
        self._post, self._post_bytes = post_json, post_bytes
        self._seq = secrets.randbelow(1_000_000)
        self._sid = None
        self._login(email.strip().lower(), password)

    def api(self, command):
        self._seq += 1
        url = f'{API}?id={self._seq}' + (f'&sid={self._sid}' if self._sid else '')
        try:
            resp = self._post(url, [command])
        except Exception:
            raise MegaDenied('MEGA_UNREACHABLE') from None
        if not isinstance(resp, list) or not resp:
            raise MegaDenied('MEGA_API_ERROR')
        if isinstance(resp[0], int):
            if resp[0] == -26:
                raise MegaDenied('MEGA_2FA_REQUIRED')
            if resp[0] in (-9, -16):
                raise MegaDenied('MEGA_LOGIN_FAILED')
            raise MegaDenied('MEGA_API_ERROR')
        return resp[0]

    def _login(self, email, password):
        pre = self.api({'a': 'us0', 'user': email})
        if not isinstance(pre, dict) or pre.get('v') != 2 or not pre.get('s'):
            raise MegaDenied('MEGA_ACCOUNT_VERSION_UNSUPPORTED')
        derived = hashlib.pbkdf2_hmac('sha512', password.encode(), b64d(pre['s']), 100000, 32)
        pw_key, user_hash = derived[:16], b64e(derived[16:])
        res = self.api({'a': 'us', 'user': email, 'uh': user_hash})
        try:
            self.master_key = ecb_decrypt(pw_key, b64d(res['k']))
            privk = ecb_decrypt(self.master_key, b64d(res['privk']))
            p, q, d, _u = read_mpis(privk, 4)
            [csid] = read_mpis(b64d(res['csid']), 1)
            m = pow(csid, d, p * q)
            raw = m.to_bytes((m.bit_length() + 7) // 8, 'big')
            self._sid = b64e(raw[:43])
        except MegaDenied:
            raise
        except Exception:
            raise MegaDenied('MEGA_LOGIN_FAILED') from None

    def upload(self, parent_handle, share_handle, share_key, name, data):
        """New file under parent; returns the put-nodes node dict."""
        aes, nonce = secrets.token_bytes(16), secrets.token_bytes(8)
        info = self.api({'a': 'u', 's': len(data)})
        url = info.get('p') if isinstance(info, dict) else None
        if not isinstance(url, str) or not url.startswith('https://'):
            raise MegaDenied('MEGA_UPLOAD_UNAVAILABLE')
        try:
            token = self._post_bytes(url + '/0', ctr_encrypt(aes, nonce, data)).strip()
        except Exception:
            raise MegaDenied('MEGA_UPLOAD_FAILED') from None
        if not token or token.lstrip('-').isdigit():
            raise MegaDenied('MEGA_UPLOAD_FAILED')
        tail = nonce + meta_mac(aes, nonce, data)
        node_key = bytes(a ^ b for a, b in zip(aes, tail)) + tail
        res = self.api({
            'a': 'p', 't': parent_handle, 'i': secrets.token_hex(5),
            'n': [{'h': token, 't': 0, 'a': encrypt_attrs(aes, {'n': name}),
                   'k': b64e(ecb_encrypt(self.master_key, node_key))}],
            'cr': [[share_handle], [token], [0, 0, b64e(ecb_encrypt(share_key, node_key))]]})
        nodes = res.get('f') if isinstance(res, dict) else None
        if not isinstance(nodes, list) or len(nodes) != 1 or not isinstance(nodes[0], dict):
            raise MegaDenied('MEGA_PUT_FAILED')
        return nodes[0]


class TicketLedger:
    """Approval-ticket check plus one-write-per-ticket receipts in Redis."""

    def __init__(self, owner, client_factory, now=time.time):
        from approval import ApprovalStore
        self.store = ApprovalStore(owner, client_factory, now=now)
        self.client_factory, self.now = client_factory, now
        self.prefix = NAMESPACE + ':' + hashlib.sha256(owner.encode()).hexdigest()[:32]

    def _run(self, fn):
        client = self.client_factory()
        try:
            return fn(client)
        finally:
            close = getattr(client, 'close', None)
            if callable(close):
                close()

    def check(self, ticket, parent_ref, name):
        try:
            data = self.store.get(ticket)
        except Exception:
            raise MegaDenied('MEGA_TICKET_NOT_FOUND') from None
        approved_at = int(data.get('approved_at') or 0)
        if (data.get('state') not in ('approved', 'claimed') or not approved_at
                or approved_at + CLAIM_WINDOW < self.now()):
            raise MegaDenied('MEGA_TICKET_NOT_APPROVED')
        if not str(data.get('title', '')).startswith(TITLE_PREFIX):
            raise MegaDenied('MEGA_TICKET_WRONG_KIND')
        allowed = {(p, unicodedata.normalize('NFC', n).strip())
                   for p, n in PLAN_LINE.findall(str(data.get('plan', '')))}
        if (parent_ref, name) not in allowed:
            raise MegaDenied('MEGA_TICKET_PLAN_MISMATCH')

    def consume(self, ticket):
        key = self.prefix + ':' + ticket
        ok = self._run(lambda c: c.set(key, json.dumps({'status': 'pending'}),
                                       nx=True, ex=30 * 86400))
        if not ok:
            raise MegaDenied('MEGA_TICKET_ALREADY_USED')

    def save(self, ticket, receipt):
        key = self.prefix + ':' + ticket
        self._run(lambda c: c.set(key, json.dumps(receipt), ex=30 * 86400))

    def load(self, ticket):
        raw = self._run(lambda c: c.get(self.prefix + ':' + ticket))
        return json.loads(raw) if raw else None


def make_mega_writer(folder, owner_of_deployment, session_factory, ledger_factory,
                     now=time.time):
    """resolve_writer for OriginalLibraryHostBinding(source=MEGA_SOURCE)."""
    from library_catalog_gate import MEGA_SOURCE
    from library_write_gate import OriginalLibraryWriteScope, request_digest

    async def resolve_writer(ctx, owner, client_id, operation, target_ref,
                             expected_version, operation_key):
        import asyncio
        if owner != owner_of_deployment():
            raise MegaDenied('MEGA_NOT_OWNER')
        if operation not in ('create', 'status'):
            raise MegaDenied('MEGA_WRITE_CREATE_ONLY')
        ledger = ledger_factory(owner)
        receipts = {}

        def base_receipt(status, ref='', version=''):
            return {'schema': 'original-library-write-receipt-v1', 'source': MEGA_SOURCE,
                    'owner': owner, 'client_id': client_id, 'operation': operation,
                    'target_ref': target_ref, 'expected_version': expected_version,
                    'operation_key': operation_key, 'ref': ref, 'version': version,
                    'status': status}

        async def commit_original(request):
            name = unicodedata.normalize('NFC', request['name']).strip()
            if (request['payload_kind'] != 'text' or not name or name != request['name']
                    or '..' in name or '/' in name or '\\' in name):
                raise MegaDenied('MEGA_WRITE_NAME')
            data = request['content'].encode('utf-8')
            if len(data) > MAX_WRITE_BYTES:
                raise MegaDenied('MEGA_WRITE_TOO_LARGE')

            def go():
                folder._tree = None  # fresh listing: path and no-overwrite checks
                tree = folder.tree()
                if target_ref == ROOT_REF:
                    parent = next((n for n in tree.values() if n['p'] not in tree), None)
                else:
                    m = re.fullmatch(r'm:([A-Za-z0-9_-]{8})', target_ref)
                    parent = tree.get(m.group(1)) if m else None
                if parent is None or parent['t'] != 1:
                    raise MegaDenied('MEGA_PARENT_NOT_IN_FOLDER')
                if any(n['p'] == parent['h'] and n['name'] == name for n in tree.values()):
                    raise MegaDenied('MEGA_NAME_EXISTS')
                ledger.check(operation_key, target_ref, name)
                ledger.consume(operation_key)
                share_handle, share_key = folder.share()
                node = session_factory().upload(parent['h'], share_handle, share_key, name, data)
                folder._tree = None
                created = folder.tree().get(node.get('h'))
                if created is None or created['p'] != parent['h'] or created['name'] != name:
                    raise MegaDenied('MEGA_READBACK_NOT_IN_FOLDER')
                receipt = dict(base_receipt('committed', 'm:' + created['h'], version_of(created)),
                               request_digest=request_digest(request))
                ledger.save(operation_key, receipt)
                return receipt
            receipts[operation_key] = await asyncio.to_thread(go)

        async def receipt_original(key):
            if key in receipts:
                return receipts[key]
            stored = await asyncio.to_thread(ledger.load, key)
            if not stored:
                return dict(base_receipt('denied'), request_digest='')
            return stored

        return OriginalLibraryWriteScope(
            owner=owner, client_id=client_id, source=MEGA_SOURCE, operation=operation,
            target_ref=target_ref, expected_version=expected_version,
            operation_key=operation_key, expires_at=now() + 90, is_current=lambda: True,
            commit_original=commit_original, receipt_original=receipt_original)

    return resolve_writer


def writer_from_env(env, folder, owner_of_deployment, post_json, post_bytes=_post_bytes,
                    client_factory=None):
    if env.get('XJ_MEGA_KB_WRITE_ENABLED') != 'true':
        return None
    email, password = env.get('XJ_MEGA_EMAIL', ''), env.get('XJ_MEGA_PASSWORD', '')
    if not email or not password:
        return None
    if client_factory is None:
        from chat_room import default_client as client_factory

    def session_factory():
        return MegaSession(email, password, post_json, post_bytes)

    return make_mega_writer(folder, owner_of_deployment, session_factory,
                            lambda owner: TicketLedger(owner, client_factory))
