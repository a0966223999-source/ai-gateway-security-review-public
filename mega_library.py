"""Read-only Library source backed by the Owner's MEGA "知識庫" folder export link.

Owner ruling 2026-11-20 (approval ticket 05e93e8f). Default off.

  XJ_MEGA_KB_ENABLED=true        enable
  XJ_MEGA_KB_FOLDER_LINK         Sensitive: https://mega.nz/folder/<handle>#<key>
  XJ_MEGA_KB_ROOT_NAME           optional, default 知識庫

Only the exported folder tree is reachable (MEGA serves nothing else for a
folder link) and its root must be named 知識庫. Refs are node handles inside
that tree, never paths; names containing ".." are refused. Reads download and
decrypt server-side and return only a manifest (name, size, SHA-256) or, for a
small UTF-8 text file, a text excerpt. The link never appears in any output,
error or log line: errors are reduced to fixed codes.

MEGA formats used (public folder links, no account):
  node keys   AES-128-ECB with the folder key; file keys are 32 bytes:
              key = k[0:16] XOR k[16:32], CTR nonce = k[16:24]
  attributes  AES-128-CBC, zero IV, plaintext b'MEGA' + JSON (+ NUL padding)
  content     AES-128-CTR, counter block = nonce(8) + block index(8, big endian)
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
import unicodedata
import urllib.request
from datetime import datetime, timezone

from library_catalog_gate import MEGA_SOURCE, OriginalLibraryScope

API = 'https://g.api.mega.co.nz/cs'
SCHEMA_PAGE = 'chatgpt-library-catalog-page-v1'
SCHEMA_ITEM = 'chatgpt-library-item-view-v1'
DEFAULT_ROOT = '知識庫'
MAX_READ_BYTES = 25 * 1024 * 1024
TEXT_LIMIT = 512 * 1024  # card libraries are JSON files; serve them whole
TREE_TTL = 60
HANDLE = re.compile(r'[A-Za-z0-9_-]{8}\Z')
REF = re.compile(r'm:([A-Za-z0-9_-]{8})\Z')
LINK = re.compile(r'https://mega\.(?:nz|co\.nz)/folder/([A-Za-z0-9_-]{8})#([A-Za-z0-9_-]{22})\Z')
TEXT_TYPES = {'.txt': 'text/plain', '.md': 'text/markdown', '.json': 'application/json',
              '.csv': 'text/csv', '.yaml': 'application/yaml', '.yml': 'application/yaml',
              '.html': 'text/html', '.xml': 'application/xml', '.py': 'text/x-python'}
OTHER_TYPES = {'.zip': 'application/zip', '.pdf': 'application/pdf', '.png': 'image/png',
               '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif',
               '.webp': 'image/webp', '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}


class MegaDenied(Exception):
    """Fixed-code failure; never carries the link, keys or MEGA URLs."""


def b64d(text):
    text = text.replace('-', '+').replace('_', '/').replace(',', '')
    return base64.b64decode(text + '=' * (-len(text) % 4))


def _aes():
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    return Cipher, algorithms, modes


def ecb_decrypt(key, data):
    Cipher, algorithms, modes = _aes()
    d = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    return d.update(data) + d.finalize()


def cbc_decrypt(key, data):
    Cipher, algorithms, modes = _aes()
    d = Cipher(algorithms.AES(key), modes.CBC(b'\0' * 16)).decryptor()
    return d.update(data) + d.finalize()


def ctr_decryptor(key, nonce):
    Cipher, algorithms, modes = _aes()
    return Cipher(algorithms.AES(key), modes.CTR(nonce + b'\0' * 8)).decryptor()


def parse_link(link):
    m = LINK.fullmatch(link.strip()) if isinstance(link, str) else None
    if not m:
        raise MegaDenied('MEGA_LINK_INVALID')
    key = b64d(m.group(2))
    if len(key) != 16:
        raise MegaDenied('MEGA_LINK_INVALID')
    return m.group(1), key


def node_key(raw):
    """Folder nodes: 16 bytes. File nodes: 32 bytes -> (aes key, ctr nonce)."""
    if len(raw) == 16:
        return raw, None
    if len(raw) == 32:
        return bytes(a ^ b for a, b in zip(raw[:16], raw[16:])), raw[16:24]
    raise MegaDenied('MEGA_NODE_KEY')


def decrypt_attrs(key, blob):
    plain = cbc_decrypt(key, b64d(blob)).rstrip(b'\0')
    if not plain.startswith(b'MEGA{'):
        raise MegaDenied('MEGA_ATTRS')
    return json.loads(plain[4:].decode('utf-8'))


def guess_mime(name):
    ext = os.path.splitext(name.lower())[1]
    return TEXT_TYPES.get(ext) or OTHER_TYPES.get(ext) or 'application/octet-stream'


def _post_json(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method='POST',
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _get_stream(url):
    return urllib.request.urlopen(urllib.request.Request(url), timeout=30)


class MegaFolder:
    """One exported folder tree. Holds the link only in memory."""

    def __init__(self, link, root_name=DEFAULT_ROOT, post_json=_post_json,
                 get_stream=_get_stream, clock=time.time):
        self._handle, self._key = parse_link(link)
        self._root_name = unicodedata.normalize('NFC', root_name)
        self._post, self._get, self._clock = post_json, get_stream, clock
        self._tree, self._loaded = None, 0.0
        self._seq = int(clock() * 1000) % 1_000_000
        self.revision_seed = hashlib.sha256(b'mega-kb|' + self._handle.encode()).hexdigest()

    def _api(self, command):
        self._seq += 1
        try:
            resp = self._post(f'{API}?id={self._seq}&n={self._handle}', [command])
        except Exception:
            raise MegaDenied('MEGA_UNREACHABLE') from None
        if not isinstance(resp, list) or not resp:
            raise MegaDenied('MEGA_API_ERROR')
        if isinstance(resp[0], int):
            raise MegaDenied('MEGA_API_ERROR')
        return resp[0]

    def tree(self):
        """{handle: node} for the exported tree; root must be named 知識庫."""
        if self._tree is not None and self._clock() - self._loaded < TREE_TTL:
            return self._tree
        listing = self._api({'a': 'f', 'c': 1, 'ca': 1, 'r': 1})
        raw = listing.get('f') if isinstance(listing, dict) else None
        if not isinstance(raw, list):
            raise MegaDenied('MEGA_API_ERROR')
        nodes = {}
        for item in raw:
            try:
                h, t, k = item['h'], item['t'], item['k']
                if not HANDLE.fullmatch(h) or t not in (0, 1):
                    continue
                attrs = None
                for part in k.split('/'):  # "<owner/share handle>:<encrypted key>"[/...]
                    try:
                        enc = b64d(part.split(':', 1)[1])
                        key, nonce = node_key(ecb_decrypt(self._key, enc))
                        attrs = decrypt_attrs(key, item['a'])
                        break
                    except Exception:
                        continue
                if attrs is None:
                    continue
                name = unicodedata.normalize('NFC', str(attrs.get('n', '')))
            except Exception:
                continue  # unreadable node: not part of what we serve
            nodes[h] = {'h': h, 'p': item.get('p', ''), 't': t, 'name': name,
                        'key': key, 'nonce': nonce, 's': item.get('s', 0),
                        'ts': int(item.get('ts', 0))}
        roots = [n for n in nodes.values() if n['p'] not in nodes]
        if len(roots) != 1 or roots[0]['t'] != 1 or roots[0]['name'] != self._root_name:
            raise MegaDenied('MEGA_ROOT_NOT_ALLOWED')
        root = roots[0]['h']
        # File versions hang under the newer file node (parent is a file). They are
        # never served; the Writer uses this map only to keep one previous version.
        versions = {h: n['p'] for h, n in nodes.items()
                    if n['p'] in nodes and nodes[n['p']]['t'] == 0}
        safe = {}
        for h, n in nodes.items():  # keep only nodes whose whole path is inside the root
            if h in versions:
                continue
            path, cur, ok, seen = [], n, True, set()
            while True:
                if cur['h'] in seen or '..' in cur['name'] or '/' in cur['name'] or not cur['name']:
                    ok = False
                    break
                seen.add(cur['h'])
                path.append(cur['name'])
                if cur['h'] == root:
                    break
                cur = nodes.get(cur['p'])
                if cur is None:
                    ok = False
                    break
            if ok:
                n['path'] = '/'.join(reversed(path))
                safe[h] = n
        self._version_of = versions  # version handle -> newer node handle
        self._tree, self._loaded = safe, self._clock()
        return safe

    def version_chain(self, handle):
        """Older versions of a served file, newest first (from the last listing)."""
        newer_to_older = {}
        for older, newer in getattr(self, '_version_of', {}).items():
            newer_to_older.setdefault(newer, []).append(older)
        chain, cur, seen = [], handle, {handle}
        while True:
            older = newer_to_older.get(cur, [])
            if len(older) != 1 or older[0] in seen:
                return chain if not older else chain + [h for h in older if h not in seen]
            chain.append(older[0])
            seen.add(older[0])
            cur = older[0]

    def share(self):
        """(share handle, share key) of the exported folder, for the Writer's cr entry."""
        return self._handle, self._key

    def revision(self):
        tree = self.tree()
        stamp = max((n['ts'] for n in tree.values()), default=0)
        return hashlib.sha256(f'{self.revision_seed}|{len(tree)}|{stamp}'.encode()).hexdigest()

    def file_node(self, ref, expected_version=None):
        m = REF.fullmatch(ref) if isinstance(ref, str) else None
        node = self.tree().get(m.group(1)) if m else None
        if node is None or node['t'] != 0:
            raise MegaDenied('MEGA_REF_NOT_IN_FOLDER')
        if expected_version is not None and expected_version != version_of(node):
            raise MegaDenied('MEGA_VERSION_CHANGED')
        return node

    def read(self, node):
        """Download and decrypt; returns (size, sha256 hex, first bytes up to TEXT_LIMIT+1)."""
        if node['s'] > MAX_READ_BYTES:
            raise MegaDenied('MEGA_FILE_TOO_LARGE')
        info = self._api({'a': 'g', 'g': 1, 'n': node['h']})
        url = info.get('g') if isinstance(info, dict) else None
        if isinstance(url, list):
            url = url[0] if url else None
        if not isinstance(url, str) or not url.startswith(('https://', 'http://')):
            raise MegaDenied('MEGA_DOWNLOAD_UNAVAILABLE')
        dec = ctr_decryptor(node['key'], node['nonce'])
        digest, size, head = hashlib.sha256(), 0, bytearray()
        try:
            with self._get(url) as resp:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_READ_BYTES:
                        raise MegaDenied('MEGA_FILE_TOO_LARGE')
                    plain = dec.update(chunk)
                    digest.update(plain)
                    if len(head) <= TEXT_LIMIT:
                        head.extend(plain[:TEXT_LIMIT + 1 - len(head)])
        except MegaDenied:
            raise
        except Exception:
            raise MegaDenied('MEGA_DOWNLOAD_FAILED') from None
        if size != node['s']:
            raise MegaDenied('MEGA_SIZE_MISMATCH')
        return size, digest.hexdigest(), bytes(head)


def version_of(node):
    return f"v:{node['ts']}:{node['s']}"


def entry_of(node):
    entry = {'ref': 'm:' + node['h'], 'name': node['path'][-255:],
             'kind': 'file' if node['t'] == 0 else 'folder',
             'mime_type': guess_mime(node['name']) if node['t'] == 0 else 'inode/directory',
             'version': version_of(node)}
    if node['t'] == 0:
        entry['size_bytes'] = int(node['s'])
    if node['ts']:
        entry['modified_at'] = datetime.fromtimestamp(node['ts'], timezone.utc).isoformat()
    return entry


def make_mega_reader(folder, owner_of_deployment, *, policy=None):
    """resolve_reader for OriginalLibraryHostBinding(source=MEGA_SOURCE).

    With a client policy (mega_policy), only allowlisted clients with "read" may
    list or read, re-checked on every call; no per-read ticket.
    """

    async def resolve_reader(ctx, owner, client_id, operation, ref):
        def allowed():
            return owner == owner_of_deployment() and (
                policy is None or policy.member(owner, client_id, 'read') is not None)

        if not allowed():
            raise MegaDenied('MEGA_READ_DENIED')
        import asyncio
        revision = await asyncio.to_thread(folder.revision)

        async def search(query, cursor, limit):
            tree = await asyncio.to_thread(folder.tree)
            needle = unicodedata.normalize('NFC', query).casefold()
            hits = sorted((n for n in tree.values() if n['p'] in tree or n['t'] == 0),
                          key=lambda n: n['path'])
            hits = [n for n in hits if needle in n['path'].casefold()]
            start = int(cursor) if cursor.isdigit() else 0
            page = hits[start:start + limit]
            nxt = str(start + limit) if start + limit < len(hits) else ''
            return {'schema': SCHEMA_PAGE, 'source': MEGA_SOURCE, 'owner': owner,
                    'revision': revision, 'items': [entry_of(n) for n in page],
                    'next_cursor': nxt}

        async def read(ref, expected_version):
            node = await asyncio.to_thread(folder.file_node, ref, expected_version)
            size, sha, head = await asyncio.to_thread(folder.read, node)
            mime = guess_mime(node['name'])
            text = None
            if mime in TEXT_TYPES.values() and size <= TEXT_LIMIT:
                try:
                    text = head.decode('utf-8')
                except UnicodeDecodeError:
                    text = None
            if text is not None:
                kind, preview = 'text_excerpt', text
            else:
                kind = 'binary_manifest'
                preview = json.dumps({'name': node['path'], 'size_bytes': size, 'sha256': sha},
                                     ensure_ascii=False)
            return {'schema': SCHEMA_ITEM, 'source': MEGA_SOURCE, 'owner': owner,
                    'revision': revision, 'ref': ref, 'version': expected_version,
                    'mime_type': mime, 'preview_kind': kind, 'preview': preview}

        return OriginalLibraryScope(owner=owner, client_id=client_id, revision=revision,
                                    is_current=allowed, search_original=search,
                                    read_original=read, source=MEGA_SOURCE)

    return resolve_reader


def binding_from_env(env=None, owner_of_deployment=None, post_bytes=None,
                     client_factory=None, policy=None, drive_post_form=None,
                     drive_open=None, **folder_kw):
    """OriginalLibraryHostBinding for MEGA, or None when disabled/misconfigured."""
    env = os.environ if env is None else env
    if env.get('XJ_MEGA_KB_ENABLED') != 'true':
        return None
    link = env.get('XJ_MEGA_KB_FOLDER_LINK', '')
    try:
        folder = MegaFolder(link, env.get('XJ_MEGA_KB_ROOT_NAME') or DEFAULT_ROOT, **folder_kw)
    except MegaDenied:
        return None
    from library_original_host_binding import OriginalLibraryHostBinding
    if owner_of_deployment is None:
        from mail_hook import owner_principal
        owner_of_deployment = lambda: owner_principal(env)  # noqa: E731
    # Stage 2 (Owner ruling 2026-11-21): optional create-only Writer, default off.
    from mega_writer import writer_from_env
    extra = {k: v for k, v in (('post_bytes', post_bytes), ('client_factory', client_factory))
             if v is not None}
    if policy is None:
        from mega_policy import policy_from_env
        policy = policy_from_env(env, owner_of_deployment)
    post_json = folder_kw.get('post_json', _post_json)
    writer = writer_from_env(env, folder, owner_of_deployment, post_json, policy=policy, **extra)
    # Drive import is a separate default-off flag. A bad Drive config must not
    # take down the reader or the text writer.
    drive = None
    if writer is not None:
        try:
            from mega_drive_import import drive_import_from_env
            drive = drive_import_from_env(
                env, folder, owner_of_deployment, policy, post_json, post_bytes,
                client_factory, drive_post_form, drive_open)
        except Exception:
            print('xj-mega-drive stage=config code=DRIVE_CONFIG_FAILED', flush=True)
    return OriginalLibraryHostBinding(
        source=MEGA_SOURCE, generation=folder.revision_seed, is_current=lambda: True,
        resolve_reader=make_mega_reader(folder, owner_of_deployment, policy=policy),
        resolve_writer=writer, drive_import=drive)
