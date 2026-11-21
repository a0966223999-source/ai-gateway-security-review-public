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
  upload   AES-128-CTR content, chunk CBC-MACs -> 8-byte meta MAC;
           posted in 1 MiB pieces to {url}/{offset} (last response is the token)
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

from mega_library import API, TEXT_TYPES, MegaDenied, b64d, ecb_decrypt, guess_mime, version_of

TITLE_PREFIX = 'MEGA知識庫寫入'
ROOT_REF = 'kb:root'  # the 知識庫 folder itself, so tickets never carry its handle
# Client mode (mega_policy, Owner rulings 2026-11-21): allowlisted AIs create and
# overwrite files anywhere inside 知識庫 without a per-write ticket.
PLAN_LINE = re.compile(r'^mega_write parent=(kb:root|m:[A-Za-z0-9_-]{8}) name=([^\n/\\]{1,160})$', re.M)
CLAIM_WINDOW = 24 * 3600
MAX_WRITE_BYTES = 512 * 1024
UPLOAD_CHUNK = 0x100000  # 1 MiB; a 5,973,238-byte zip is six posts
NAMESPACE = 'xj:mega-write:v1'
# KEYS: operation, destination, receipt, daily counter.
# ARGV: marker, receipt json, ttl, daily limit, daily ttl.
# 0 = operation key or destination already used, 2 = daily limit reached, 1 = reserved.
RESERVE_CLIENT = """
for i = 1, 3 do
  if redis.call('EXISTS', KEYS[i]) == 1 then return 0 end
end
local used = tonumber(redis.call('GET', KEYS[4]) or '0')
if used >= tonumber(ARGV[4]) then return 2 end
redis.call('INCR', KEYS[4])
redis.call('EXPIRE', KEYS[4], ARGV[5])
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[3])
redis.call('SET', KEYS[2], ARGV[1], 'EX', ARGV[3])
redis.call('SET', KEYS[3], ARGV[2], 'EX', ARGV[3])
return 1
"""


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


def upload_ranges(size, chunk=UPLOAD_CHUNK):
    """Byte ranges for one MEGA upload. Files up to 1 MiB stay a single POST at /0."""
    if size <= chunk:
        return [(0, size)]
    return [(start, min(start + chunk, size)) for start in range(0, size, chunk)]


def upload_timeout(nbytes):
    """Socket timeout for one POST. 512 KiB text keeps 30s; each 1 MiB chunk gets 25s
    so six chunks of a ~6 MiB file still finish inside the 180s function limit."""
    if nbytes <= 512 * 1024:
        return 30
    return 25


def _post_bytes(url, data):
    req = urllib.request.Request(url, data=data, method='POST',
                                 headers={'Content-Type': 'application/octet-stream'})
    with urllib.request.urlopen(req, timeout=upload_timeout(len(data))) as r:
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
        if isinstance(resp[0], int) and resp[0] < 0:  # 0 means plain success (e.g. delete)
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

    def version_chain(self, handle):
        """Older versions of a file, newest first. MEGA lists file versions only in the
        account listing's "f2" (never in "f", never via the folder link)."""
        res = self.api({'a': 'f', 'c': 1, 'r': 1})
        older = {}
        for n in (res.get('f2') or []) if isinstance(res, dict) else []:
            if isinstance(n, dict) and isinstance(n.get('h'), str) and isinstance(n.get('p'), str):
                older.setdefault(n['p'], []).append(n['h'])
        chain, cur, seen = [], handle, {handle}
        while len(older.get(cur, [])) == 1 and older[cur][0] not in seen:
            cur = older[cur][0]
            chain.append(cur)
            seen.add(cur)
        return chain

    def delete(self, handle):
        """Remove one node (and anything under it). Used only to prune old versions."""
        self.api({'a': 'd', 'n': handle, 'i': secrets.token_hex(5)})

    def upload(self, parent_handle, share_handle, share_key, name, data, overwrite=None):
        """New file under parent; returns the put-nodes node dict.

        overwrite=<handle>: MEGA "ov" — the new node replaces that file and the old
        one becomes its previous version.
        """
        aes, nonce = secrets.token_bytes(16), secrets.token_bytes(8)
        info = self.api({'a': 'u', 's': len(data), 'ssl': 1})  # ssl=1: https upload URL
        url = info.get('p') if isinstance(info, dict) else None
        if not isinstance(url, str) or not url.startswith('https://'):
            raise MegaDenied('MEGA_UPLOAD_UNAVAILABLE')
        encrypted = ctr_encrypt(aes, nonce, data)
        try:
            token = self._put_chunks(url, encrypted)
        except MegaDenied:
            raise
        except Exception:
            raise MegaDenied('MEGA_UPLOAD_FAILED') from None
        tail = nonce + meta_mac(aes, nonce, data)
        node_key = bytes(a ^ b for a, b in zip(aes, tail)) + tail
        new_node = {'h': token, 't': 0, 'a': encrypt_attrs(aes, {'n': name}),
                    'k': b64e(ecb_encrypt(self.master_key, node_key))}
        if overwrite:
            new_node['ov'] = overwrite
        res = self.api({
            'a': 'p', 't': parent_handle, 'i': secrets.token_hex(5),
            'n': [new_node],
            # Same shape as MEGA's web client crypto_makecr(newNodes, [share], false):
            # node list empty, the second index points into "n".
            'cr': [[share_handle], [], [0, 0, b64e(ecb_encrypt(share_key, node_key))]]})
        nodes = res.get('f') if isinstance(res, dict) else None
        made = [n for n in nodes if isinstance(n, dict) and n.get('p') == parent_handle
                and n.get('h') != overwrite] if isinstance(nodes, list) else []
        if len(made) != 1:
            raise MegaDenied('MEGA_PUT_FAILED')
        return made[0]

    def _put_chunks(self, url, blob):
        """POST ciphertext to {url}/{offset}. Only the chunk that finishes the file
        carries the completion token; a numeric body is a progress ack."""
        token, total = '', len(blob)
        for start, end in upload_ranges(total):
            body = self._post_bytes(url + '/' + str(start), blob[start:end])
            text = body.strip() if isinstance(body, str) else ''
            if text.startswith('-') or (end != total and text and not text.isdigit()):
                raise MegaDenied('MEGA_UPLOAD_FAILED')
            if end == total:
                token = text
        if not token or token.lstrip('-').isdigit():
            raise MegaDenied('MEGA_UPLOAD_FAILED')
        return token


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


class ClientLedger(TicketLedger):
    """Client mode: receipts per (owner, client, operation_key); names reserved per folder."""

    def __init__(self, owner, client_id, client_factory, now=time.time):
        self.client_factory, self.now = client_factory, now
        tag = '{' + hashlib.sha256(owner.encode()).hexdigest()[:32] + '}'  # one slot for the script
        self.base = NAMESPACE + ':clients:' + tag
        self.prefix = self.base + ':c:' + hashlib.sha256(client_id.encode()).hexdigest()[:32]

    def destination_key(self, parent_handle, name):
        return self.base + ':dest:' + hashlib.sha256(
            (parent_handle + '\0' + name).encode()).hexdigest()

    def reserve(self, operation_key, parent_handle, name, receipt, daily_limit):
        day = time.strftime('%Y%m%d', time.gmtime(self.now()))
        result = self._run(lambda c: c.eval(
            RESERVE_CLIENT, 4, self.prefix + ':op:' + operation_key,
            self.destination_key(parent_handle, name),
            self.prefix + ':' + operation_key, self.prefix + ':day:' + day,
            operation_key, json.dumps(receipt), 30 * 86400, daily_limit, 2 * 86400))
        if result == 2:
            raise MegaDenied('MEGA_DAILY_LIMIT')
        if result != 1:
            raise MegaDenied('MEGA_OPERATION_OR_NAME_ALREADY_USED')

    def release_destination(self, parent_handle, name):
        """Free a name/version slot when nothing was uploaded. The operation key stays used."""
        self._run(lambda c: c.delete(self.destination_key(parent_handle, name)))

    @staticmethod
    def match(stored, receipt):
        """Committed receipt for this exact digest, or a fixed rejection code.

        Pending is not retried. A different digest never resumes the old write.
        """
        digest = receipt.get('request_digest') if isinstance(receipt, dict) else None
        if (type(stored) is not dict or not isinstance(digest, str)
                or stored.get('owner') != receipt.get('owner')
                or stored.get('client_id') != receipt.get('client_id')
                or stored.get('operation_key') != receipt.get('operation_key')):
            raise MegaDenied('MEGA_OPERATION_OR_NAME_ALREADY_USED')
        if stored.get('request_digest') != digest:
            raise MegaDenied('MEGA_OPERATION_DIGEST_MISMATCH')
        if stored.get('status') == 'committed':
            return stored
        if stored.get('status') == 'pending':
            raise MegaDenied('MEGA_OPERATION_PENDING')
        raise MegaDenied('MEGA_OPERATION_OR_NAME_ALREADY_USED')

    def claim(self, operation_key, parent_handle, name, receipt, daily_limit):
        """Reserve, or return the committed receipt when a racer stored the same digest."""
        try:
            self.reserve(operation_key, parent_handle, name, receipt, daily_limit)
        except MegaDenied as exc:
            if str(exc) != 'MEGA_OPERATION_OR_NAME_ALREADY_USED':
                raise
            stored = self.load(operation_key)
            if type(stored) is not dict:
                raise MegaDenied('MEGA_OPERATION_OR_NAME_ALREADY_USED') from None
            return ClientLedger.match(stored, receipt)
        return None


def make_mega_writer(folder, owner_of_deployment, session_factory, ledger_factory,
                     now=time.time, *, policy=None, client_ledger_factory=None):
    """resolve_writer for OriginalLibraryHostBinding(source=MEGA_SOURCE)."""
    from library_catalog_gate import MEGA_SOURCE
    from library_write_gate import OriginalLibraryWriteScope, request_digest

    async def resolve_writer(ctx, owner, client_id, operation, target_ref,
                             expected_version, operation_key):
        import asyncio
        if owner != owner_of_deployment():
            raise MegaDenied('MEGA_NOT_OWNER')
        if operation not in ('create', 'update', 'status'):
            raise MegaDenied('MEGA_WRITE_OPERATION')
        if operation == 'update' and policy is None:
            raise MegaDenied('MEGA_WRITE_CREATE_ONLY')  # Owner-ticket mode never overwrites
        need = 'update' if operation == 'update' else 'create'
        if policy is not None:
            if policy.member(owner, client_id, need) is None and not (
                    operation == 'status' and policy.member(owner, client_id, 'update')):
                raise MegaDenied('MEGA_CLIENT_NOT_ALLOWED')
            ledger = client_ledger_factory(owner, client_id)
        else:
            ledger = ledger_factory(owner)
        receipts = {}

        def base_receipt(status, ref='', version=''):
            return {'schema': 'original-library-write-receipt-v1', 'source': MEGA_SOURCE,
                    'owner': owner, 'client_id': client_id, 'operation': operation,
                    'target_ref': target_ref, 'expected_version': expected_version,
                    'operation_key': operation_key, 'ref': ref, 'version': version,
                    'status': status}

        async def commit_original(request):
            updating = operation == 'update'
            name = unicodedata.normalize('NFC', request['name']).strip()
            if request['payload_kind'] != 'text':
                raise MegaDenied('MEGA_WRITE_NAME')
            if not updating and (not name or name != request['name'] or '..' in name
                                 or '/' in name or '\\' in name):
                raise MegaDenied('MEGA_WRITE_NAME')
            data = request['content'].encode('utf-8')
            if len(data) > MAX_WRITE_BYTES:
                raise MegaDenied('MEGA_WRITE_TOO_LARGE')

            def go():
                nonlocal name
                stage, old = 'check', None
                try:
                    folder._tree = None  # fresh listing: path, version and name checks
                    tree = folder.tree()
                    if policy is not None and updating:
                        # Overwrite: one existing text file inside 知識庫, at the exact
                        # version the caller read (compare-and-swap on v:<ts>:<size>).
                        m = re.fullmatch(r'm:([A-Za-z0-9_-]{8})', target_ref)
                        old = tree.get(m.group(1)) if m else None
                        if old is None or old['t'] != 0:
                            raise MegaDenied('MEGA_REF_NOT_IN_FOLDER')
                        if guess_mime(old['name']) not in TEXT_TYPES.values():
                            raise MegaDenied('MEGA_OVERWRITE_NOT_TEXT')  # e.g. the v7 zip backup
                        if version_of(old) != expected_version:
                            raise MegaDenied('MEGA_VERSION_CHANGED')
                        parent, name = tree.get(old['p']), old['name']
                    elif target_ref == ROOT_REF:
                        parent = next((n for n in tree.values() if n['p'] not in tree), None)
                    else:
                        m = re.fullmatch(r'm:([A-Za-z0-9_-]{8})', target_ref)
                        parent = tree.get(m.group(1)) if m else None
                    if parent is None or parent['t'] != 1:
                        raise MegaDenied('MEGA_PARENT_NOT_IN_FOLDER')
                    if not updating and any(n['p'] == parent['h'] and n['name'] == name
                                            for n in tree.values()):
                        raise MegaDenied('MEGA_NAME_EXISTS')  # use library_item_update instead
                    if policy is None:
                        ledger.check(operation_key, target_ref, name)
                    stage = 'login'
                    session = session_factory()  # login failures do not use up the ticket
                    stage = 'consume'
                    if policy is not None:
                        if policy.daily_creates <= 0:
                            raise MegaDenied('MEGA_DAILY_LIMIT')
                        # Create: one per folder+name. Update: one per old version handle,
                        # so two writers racing on the same version cannot both win.
                        slot = ('upd:' + old['h']) if updating else parent['h']
                        ledger.reserve(operation_key, slot, '' if updating else name,
                                       dict(base_receipt('pending'), request_digest=''),
                                       policy.daily_creates)
                        if policy.member(owner, client_id, need) is None:  # revoked meanwhile
                            ledger.save(operation_key, dict(base_receipt('denied'), request_digest=''))
                            raise MegaDenied('MEGA_CLIENT_NOT_ALLOWED')
                    else:
                        ledger.consume(operation_key)
                    stage = 'upload'
                    share_handle, share_key = folder.share()
                    node = session.upload(parent['h'], share_handle, share_key, name, data,
                                          overwrite=old['h'] if updating else None)
                    stage = 'readback'
                    folder._tree = None
                    tree = folder.tree()
                    created = tree.get(node.get('h'))
                    if created is None or created['p'] != parent['h'] or created['name'] != name:
                        raise MegaDenied('MEGA_READBACK_NOT_IN_FOLDER')
                    if updating and old['h'] in tree:
                        raise MegaDenied('MEGA_OVERWRITE_LEFT_OLD_FILE')
                    size, sha, _ = folder.read(created)  # contents, not just the listing
                    if size != len(data) or sha != hashlib.sha256(data).hexdigest():
                        raise MegaDenied('MEGA_READBACK_DIGEST_MISMATCH')
                except Exception as exc:
                    code = str(exc) if isinstance(exc, MegaDenied) else 'MEGA_INTERNAL_' + type(exc).__name__
                    # Fixed codes only: never credentials, links, keys or MEGA URLs.
                    print(f'xj-mega-write ticket={operation_key[:8]} stage={stage} code={code}', flush=True)
                    if stage in ('upload', 'readback'):
                        # The upload may have happened: status stays "pending", never retried.
                        try:
                            ledger.save(operation_key, dict(base_receipt('pending'), request_digest=''))
                        except Exception:
                            pass
                    raise MegaDenied(code) from None
                pruned = 0
                if updating:
                    # Owner ruling 2026-11-21: keep only the previous version. Delete only
                    # versions older than that, and only within this one file's own chain.
                    try:
                        for extra in session.version_chain(created['h'])[1:][:1]:
                            session.delete(extra)  # removes it and every older version under it
                            pruned += 1
                    except Exception:
                        print(f'xj-mega-write ticket={operation_key[:8]} stage=prune '
                              f'code=MEGA_PRUNE_FAILED', flush=True)
                receipt = dict(base_receipt('committed', 'm:' + created['h'], version_of(created)),
                               request_digest=request_digest(request))
                ledger.save(operation_key, receipt)
                print(f'xj-mega-write ticket={operation_key[:8]} stage=done code=COMMITTED'
                      + (f' pruned={pruned}' if updating else ''), flush=True)
                if policy is not None:
                    print(f'xj-mega-write member={policy.member(owner, client_id, need)} op={operation} '
                          f'name_sha={hashlib.sha256(name.encode()).hexdigest()[:12]}', flush=True)
                return receipt
            receipts[operation_key] = await asyncio.to_thread(go)

        async def receipt_original(key):
            if key in receipts:
                return receipts[key]
            stored = await asyncio.to_thread(ledger.load, key)
            if not stored:
                return dict(base_receipt('denied'), request_digest='')
            if stored.get('schema') is None:  # bare consume marker from an interrupted write
                return dict(base_receipt('pending'), request_digest='')
            return stored

        return OriginalLibraryWriteScope(
            owner=owner, client_id=client_id, source=MEGA_SOURCE, operation=operation,
            target_ref=target_ref, expected_version=expected_version,
            operation_key=operation_key, expires_at=now() + 90,
            is_current=lambda: policy is None or policy.member(owner, client_id, 'create') is not None,
            commit_original=commit_original, receipt_original=receipt_original)

    return resolve_writer


def writer_from_env(env, folder, owner_of_deployment, post_json, post_bytes=_post_bytes,
                    client_factory=None, policy=None):
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
                            lambda owner: TicketLedger(owner, client_factory),
                            policy=policy,
                            client_ledger_factory=lambda owner, client_id: ClientLedger(
                                owner, client_id, client_factory))
