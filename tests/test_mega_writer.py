"""Offline tests for the create-only MEGA 知識庫 Writer (stage 2).

A fake MEGA account API implements the real v2 login (PBKDF2 + RSA session id),
upload (AES-CTR) and put-nodes with a "cr" share-key entry; the new node is then
read back through the stage-1 folder-link Reader. No network, no real account.
Run: python -m unittest tests.test_mega_writer -v
"""
import asyncio
import hashlib
import json
import os
import secrets
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

import mega_library  # noqa: E402
import mega_writer  # noqa: E402
from library_catalog_gate import authorized_read, authorized_search  # noqa: E402
from library_original_host_binding import library_mount_state, make_original_library_resolvers  # noqa: E402
from library_write_gate import LibraryWriteDenied, authorized_status, authorized_write  # noqa: E402
from mega_library import b64d, ecb_decrypt  # noqa: E402
from mega_writer import b64e, ecb_encrypt  # noqa: E402
from tests.test_mega_library import CLIENT, OWNER, FakeMega  # noqa: E402

EMAIL, PASSWORD = 'owner@example.com', 'correct horse battery'
NAME = '_gateway_write_test.txt'
CONTENT = 'gateway write test 2026-11-21\n'
NOW = 1795200000


def mpi(n):
    raw = n.to_bytes((n.bit_length() + 7) // 8, 'big')
    return n.bit_length().to_bytes(2, 'big') + raw


class FakeRedis:
    def __init__(self):
        self.kv, self.h = {}, {}

    def hgetall(self, k):
        return dict(self.h.get(k, {}))

    def set(self, k, v, nx=False, ex=None):
        if nx and k in self.kv:
            return None
        self.kv[k] = v
        return True

    def get(self, k):
        return self.kv.get(k)

    def close(self):
        pass


class FakeAccount(FakeMega):
    """FakeMega folder plus the owner's account API."""

    def __init__(self, twofa=False, **kw):
        super().__init__(**kw)
        self.twofa, self.salt = twofa, secrets.token_bytes(32)
        self.master = secrets.token_bytes(16)
        derived = hashlib.pbkdf2_hmac('sha512', PASSWORD.encode(), self.salt, 100000, 32)
        self.pw_key, self.uh = derived[:16], b64e(derived[16:])
        key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        nums = key.private_numbers()
        self.n, self.e = nums.public_numbers.n, nums.public_numbers.e
        raw = mpi(nums.p) + mpi(nums.q) + mpi(nums.d) + mpi(nums.iqmp)
        raw += b'\0' * (-len(raw) % 16)
        self.privk = b64e(ecb_encrypt(self.master, raw))
        self.sid_raw = b'\x01' + secrets.token_bytes(60)
        self.sid = b64e(self.sid_raw[:43])
        self.uploads, self.puts, self.counter, self.deleted = {}, [], 0, []

    def post_json(self, url, payload):
        cmd = payload[0]
        if cmd['a'] == 'f' and 'sid=' in url:
            # Account listing: like real MEGA, file versions only in "f2".
            assert f'sid={self.sid}' in url
            files = {x['h'] for x in self.nodes if x['t'] == 0}
            return [{'f': [x for x in self.nodes if x['p'] not in files],
                     'f2': [x for x in self.nodes if x['p'] in files]}]
        if cmd['a'] in ('f', 'g'):
            return super().post_json(url, payload)
        self.calls.append(url)
        if cmd['a'] == 'us0':
            return [{'v': 2, 's': b64e(self.salt)}] if cmd['user'] == EMAIL else [-9]
        if cmd['a'] == 'us':
            if cmd['user'] != EMAIL or cmd['uh'] != self.uh:
                return [-9]
            if self.twofa:
                return [-26]
            csid = pow(int.from_bytes(self.sid_raw, 'big'), self.e, self.n)
            return [{'k': b64e(ecb_encrypt(self.pw_key, self.master)), 'privk': self.privk,
                     'csid': b64e(mpi(csid)), 'u': 'UserHand'}]
        assert f'sid={self.sid}' in url, 'command without a valid session'
        if cmd['a'] == 'u':
            # Real MEGA hands out an http:// URL unless ssl is requested.
            return [{'p': 'https://ul.example/up1' if cmd.get('ssl') else 'http://ul.example/up1'}]
        if cmd['a'] == 'p':
            self.puts.append(cmd)
            [n] = cmd['n']
            data = self.uploads.pop(n['h'])
            node_key_master = ecb_decrypt(self.master, b64d(n['k']))
            parts = []
            cr = cmd.get('cr')
            if cr and cr[0] == [self.folder_handle] and cr[1] == [] and cr[2][:2] == [0, 0]:
                shared = ecb_decrypt(self.folder_key, b64d(cr[2][2]))
                assert shared == node_key_master
                parts.append(f'{self.folder_handle}:{cr[2][2]}')
            parts.append('UserHand:' + n['k'])
            self.counter += 1
            h = f'NewNode{self.counter}'
            self.blobs[h] = data
            node = {'h': h, 'p': cmd['t'], 't': 0, 's': len(data), 'ts': NOW + self.counter,
                    'k': '/'.join(parts), 'a': n['a']}
            self.nodes.append(node)
            changed = [node]
            if n.get('ov'):
                # Like MEGA file versioning: the old file moves under the new node.
                [old] = [x for x in self.nodes if x['h'] == n['ov']]
                old['p'] = h
                changed.append(dict(old))
            return [{'f': changed}]
        if cmd['a'] == 'd':
            self.deleted.append(cmd['n'])
            gone, grew = {cmd['n']}, True
            while grew:
                more = {x['h'] for x in self.nodes if x['p'] in gone} - gone
                grew = bool(more)
                gone |= more
            self.nodes = [x for x in self.nodes if x['h'] not in gone]
            return [0]
        return [-2]

    def post_bytes(self, url, data):
        assert url == 'https://ul.example/up1/0'
        token = 'TOKEN' + secrets.token_hex(8)
        self.uploads[token] = data
        return token


def plan_for(parent, name=NAME):
    return f'寫入測試\nmega_write parent={parent} name={name}\n'


class WriterTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeAccount()
        self.redis = FakeRedis()
        self.root = 'm:' + self.fake.folder_handle
        self.env = {'XJ_MEGA_KB_ENABLED': 'true', 'XJ_MEGA_KB_FOLDER_LINK': self.fake.link,
                    'XJ_MEGA_KB_WRITE_ENABLED': 'true', 'XJ_MEGA_EMAIL': EMAIL,
                    'XJ_MEGA_PASSWORD': PASSWORD}
        self.mount()

    def mount(self):
        binding = mega_library.binding_from_env(
            self.env, owner_of_deployment=lambda: OWNER, post_json=self.fake.post_json,
            get_stream=self.fake.get_stream, post_bytes=self.fake.post_bytes,
            client_factory=lambda: self.redis)
        self.read, self.write = make_original_library_resolvers(binding)

    def ticket(self, state='approved', title='MEGA知識庫寫入 測試', plan=None, approved_at=None):
        from approval import ApprovalStore
        t = secrets.token_hex(16)
        store = ApprovalStore(OWNER, lambda: self.redis)
        self.redis.h[store._key(t)] = {
            'state': state, 'scope': 'deploy', 'title': title,
            'plan': plan if plan is not None else plan_for(self.root),
            'expires': str(NOW + 600),
            'approved_at': str(approved_at if approved_at is not None else int(__import__('time').time()))}
        return t

    def create(self, ticket, parent=None, name=NAME, content=CONTENT):
        return asyncio.run(authorized_write(
            self.write, lambda: OWNER, lambda: CLIENT, None, 'create', parent or self.root,
            'create-only', ticket, 'text/plain', content, 'text', name))

    def test_status_reports_writer_registered(self):
        state = library_mount_state(self.read, self.write)
        self.assertTrue(state['catalog_resolver_registered'])
        self.assertTrue(state['write_resolver_registered'])

    def test_writer_off_by_default(self):
        self.env.pop('XJ_MEGA_KB_WRITE_ENABLED')
        self.mount()
        self.assertIsNone(self.write)
        self.assertFalse(library_mount_state(self.read, self.write)['write_resolver_registered'])

    def test_create_then_read_back_hash_matches(self):
        receipt = self.create(self.ticket())
        self.assertEqual(receipt['status'], 'committed')
        self.assertEqual(receipt['source'], 'mega-knowledge-base')
        page = asyncio.run(authorized_search(self.read, lambda: OWNER, lambda: CLIENT, None,
                                             NAME, '', 50))
        [item] = page['items']
        self.assertEqual(item['ref'], receipt['ref'])
        self.assertEqual(item['name'], f'知識庫/{NAME}')
        view = asyncio.run(authorized_read(self.read, lambda: OWNER, lambda: CLIENT, None,
                                           item['ref'], item['version']))
        self.assertEqual(view['preview'], CONTENT)
        self.assertEqual(hashlib.sha256(view['preview'].encode()).hexdigest(),
                         hashlib.sha256(CONTENT.encode()).hexdigest())
        put = self.fake.puts[0]
        self.assertEqual(put['t'], self.fake.folder_handle)
        self.assertNotIn(PASSWORD, json.dumps(receipt))

    def test_status_after_commit(self):
        t = self.ticket()
        self.create(t)
        status = asyncio.run(authorized_status(self.write, lambda: OWNER, lambda: CLIENT, None, t))
        self.assertEqual(status['status'], 'committed')

    def assert_denied(self, ticket, **kw):
        with self.assertRaises(LibraryWriteDenied):
            self.create(ticket, **kw)
        self.assertEqual(self.fake.puts, [])

    def test_unknown_or_pending_ticket_denied(self):
        self.assert_denied(secrets.token_hex(16))
        self.assert_denied(self.ticket(state='pending'))
        self.assert_denied(self.ticket(state='done'))

    def test_expired_claim_window_denied(self):
        self.assert_denied(self.ticket(approved_at=1))

    def test_wrong_title_or_plan_denied(self):
        self.assert_denied(self.ticket(title='other work'))
        self.assert_denied(self.ticket(plan=plan_for(self.root, 'other.txt')))
        self.assert_denied(self.ticket(plan='mega_write anywhere'))

    def test_ticket_used_once(self):
        t = self.ticket(plan=plan_for(self.root) + plan_for(self.root, 'b.txt'))
        self.create(t)
        with self.assertRaises(LibraryWriteDenied):
            self.create(t, name='b.txt')
        self.assertEqual(len(self.fake.puts), 1)

    def test_no_overwrite_of_existing_name(self):
        zip_name = 'XJ_Starry_Shared_Memory_Reflection_Cards_Library_v7_20261116.zip'
        self.assert_denied(self.ticket(plan=plan_for(self.root, zip_name)), name=zip_name)
        self.create(self.ticket())
        with self.assertRaises(LibraryWriteDenied):
            self.create(self.ticket())
        self.assertEqual(len(self.fake.puts), 1)

    def test_parent_must_be_folder_inside_tree(self):
        for parent in ('m:Outside1', 'm:ZipFile1', 'm:DotDot01'):
            self.assert_denied(self.ticket(plan=plan_for(parent)), parent=parent)

    def test_root_alias(self):
        receipt = self.create(self.ticket(plan=plan_for('kb:root')), parent='kb:root')
        self.assertEqual(receipt['status'], 'committed')
        self.assertEqual(self.fake.puts[0]['t'], self.fake.folder_handle)

    def test_subfolder_allowed(self):
        receipt = self.create(self.ticket(plan=plan_for('m:SubDir01')), parent='m:SubDir01')
        self.assertEqual(receipt['status'], 'committed')

    def test_update_is_denied(self):
        with self.assertRaises(LibraryWriteDenied):
            asyncio.run(authorized_write(self.write, lambda: OWNER, lambda: CLIENT, None,
                                         'update', 'm:ZipFile1', 'v:1795100000:9406',
                                         self.ticket(), 'text/plain', 'x', 'text', ''))
        self.assertEqual(self.fake.puts, [])

    def test_non_owner_denied(self):
        with self.assertRaises(LibraryWriteDenied):
            asyncio.run(authorized_write(self.write, lambda: 'oauth:' + 'b' * 64, lambda: CLIENT,
                                         None, 'create', self.root, 'create-only', self.ticket(),
                                         'text/plain', CONTENT, 'text', NAME))

    def test_bad_password_and_2fa_fail_closed(self):
        self.env['XJ_MEGA_PASSWORD'] = 'wrong'
        self.mount()
        self.assert_denied(self.ticket())
        self.fake.twofa = True
        self.env['XJ_MEGA_PASSWORD'] = PASSWORD
        self.mount()
        self.assert_denied(self.ticket())

    def test_login_failure_keeps_ticket_usable(self):
        t = self.ticket()
        self.env['XJ_MEGA_PASSWORD'] = 'wrong'
        self.mount()
        self.assert_denied(t)
        self.env['XJ_MEGA_PASSWORD'] = PASSWORD
        self.mount()
        self.assertEqual(self.create(t)['status'], 'committed')

    def test_failed_upload_reports_pending_not_retryable(self):
        t = self.ticket()
        self.fake.post_bytes = lambda url, data: (_ for _ in ()).throw(OSError('net'))
        self.mount()
        self.assert_denied(t)
        status = asyncio.run(authorized_status(self.write, lambda: OWNER, lambda: CLIENT, None, t))
        self.assertEqual(status['status'], 'pending')
        with self.assertRaises(LibraryWriteDenied):
            self.create(t)

    def test_status_of_unused_ticket_is_denied(self):
        status = asyncio.run(authorized_status(self.write, lambda: OWNER, lambda: CLIENT, None,
                                               self.ticket()))
        self.assertEqual(status['status'], 'denied')

    def test_session_errors_never_echo_credentials(self):
        self.fake.twofa = True
        with self.assertRaises(mega_library.MegaDenied) as ctx:
            mega_writer.MegaSession(EMAIL, PASSWORD, self.fake.post_json, self.fake.post_bytes)
        self.assertEqual(str(ctx.exception), 'MEGA_2FA_REQUIRED')
        self.assertNotIn(PASSWORD, repr(ctx.exception))

    def test_missing_credentials_gives_no_writer(self):
        self.env.pop('XJ_MEGA_PASSWORD')
        self.mount()
        self.assertIsNone(self.write)

    def test_small_text_upload_is_still_one_post(self):
        calls = []
        original = self.fake.post_bytes

        def wrap(url, data):
            calls.append(url)
            return original(url, data)

        self.fake.post_bytes = wrap
        self.mount()
        self.create(self.ticket())
        self.assertEqual(calls, ['https://ul.example/up1/0'])

    def _session(self, post_bytes):
        self.fake.post_bytes = post_bytes
        return mega_writer.MegaSession(EMAIL, PASSWORD, self.fake.post_json, post_bytes)

    def test_six_megabyte_upload_is_chunked_and_readable(self):
        payload = (b'mega-drive-chunk\n' * (5_973_238 // 16 + 1))[:5_973_238]
        self.assertEqual(len(payload), 5_973_238)
        parts, posts = {}, []

        def post_bytes(url, data):
            self.assertTrue(url.startswith('https://ul.example/up1/'))
            off = int(url.rsplit('/', 1)[1])
            posts.append(off)
            parts[off] = data
            if off + len(data) != len(payload):
                return ''
            blob = b''.join(parts[i] for i in sorted(parts))
            self.assertEqual(len(blob), len(payload))
            token = 'TOKCHUNK1'
            self.fake.uploads[token] = blob
            return token

        node = self._session(post_bytes).upload(
            self.fake.folder_handle, self.fake.folder_handle, self.fake.folder_key,
            'cards.zip', payload)
        self.assertEqual(posts, [start for start, _end in mega_writer.upload_ranges(len(payload))])
        folder = mega_library.MegaFolder(self.fake.link, post_json=self.fake.post_json,
                                         get_stream=self.fake.get_stream)
        size, sha, _head = folder.read(folder.tree()[node['h']])
        self.assertEqual(size, len(payload))
        self.assertEqual(sha, hashlib.sha256(payload).hexdigest())
        self.assertEqual(len(self.fake.puts), 1)

    def test_chunk_failure_does_not_put_the_node(self):
        payload = b'x' * (mega_writer.UPLOAD_CHUNK + 10)

        def post_bytes(url, data):
            if int(url.rsplit('/', 1)[1]):
                raise OSError('reset')
            return ''

        with self.assertRaises(mega_library.MegaDenied) as ctx:
            self._session(post_bytes).upload(
                self.fake.folder_handle, self.fake.folder_handle, self.fake.folder_key,
                'cards.zip', payload)
        self.assertEqual(str(ctx.exception), 'MEGA_UPLOAD_FAILED')
        self.assertEqual(self.fake.puts, [])

    def test_early_completion_token_is_rejected(self):
        payload = b'x' * (mega_writer.UPLOAD_CHUNK + 8)

        def post_bytes(url, data):
            return 'TOOEARLY'

        with self.assertRaises(mega_library.MegaDenied) as ctx:
            self._session(post_bytes).upload(
                self.fake.folder_handle, self.fake.folder_handle, self.fake.folder_key,
                'cards.zip', payload)
        self.assertEqual(str(ctx.exception), 'MEGA_UPLOAD_FAILED')
        self.assertEqual(self.fake.puts, [])


class MacTests(unittest.TestCase):
    def test_chunk_bounds(self):
        self.assertEqual(mega_writer.chunk_bounds(10), [(0, 10)])
        self.assertEqual(mega_writer.chunk_bounds(0x20000 + 5), [(0, 0x20000), (0x20000, 0x20005)])

    def test_upload_ranges_for_the_six_megabyte_zip(self):
        size = 5_973_238
        ranges = mega_writer.upload_ranges(size)
        self.assertEqual(len(ranges), 6)
        self.assertEqual(ranges[0], (0, mega_writer.UPLOAD_CHUNK))
        self.assertEqual(ranges[-1], (5 * mega_writer.UPLOAD_CHUNK, size))
        self.assertEqual(mega_writer.upload_ranges(0), [(0, 0)])
        self.assertEqual(mega_writer.upload_ranges(mega_writer.UPLOAD_CHUNK),
                         [(0, mega_writer.UPLOAD_CHUNK)])

    def test_upload_timeout_stays_30s_for_text_and_25s_per_binary_chunk(self):
        self.assertEqual(mega_writer.upload_timeout(512 * 1024), 30)
        self.assertEqual(mega_writer.upload_timeout(mega_writer.UPLOAD_CHUNK), 25)

    def test_meta_mac_is_8_bytes_and_deterministic(self):
        k, n = b'k' * 16, b'n' * 8
        self.assertEqual(len(mega_writer.meta_mac(k, n, b'abc')), 8)
        self.assertEqual(mega_writer.meta_mac(k, n, b'abc'), mega_writer.meta_mac(k, n, b'abc'))

    def test_post_bytes_timeout_follows_chunk_size(self):
        from unittest.mock import patch
        seen = {}

        class Response:
            def read(self):
                return b'TOK'
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        def urlopen(req, timeout=0):
            seen['timeout'] = timeout
            seen['n'] = len(req.data)
            return Response()

        with patch('mega_writer.urllib.request.urlopen', urlopen):
            self.assertEqual(mega_writer._post_bytes('https://ul.example/x/0', b'a' * 32), 'TOK')
            self.assertEqual(seen['timeout'], 30)
            mega_writer._post_bytes('https://ul.example/x/0', b'a' * mega_writer.UPLOAD_CHUNK)
            self.assertEqual(seen['timeout'], 25)
            self.assertEqual(seen['n'], mega_writer.UPLOAD_CHUNK)


if __name__ == '__main__':
    unittest.main()
