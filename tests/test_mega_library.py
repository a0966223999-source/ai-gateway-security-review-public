"""Offline MEGA 知識庫 Reader tests against a fake MEGA API that uses the real
MEGA folder-link crypto (AES-ECB node keys, AES-CBC attributes, AES-CTR content).

No network, no real link. Run: python -m unittest tests.test_mega_library -v
"""
import asyncio
import base64
import hashlib
import io
import json
import os
import secrets
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # noqa: E402

import mega_library  # noqa: E402
from library_catalog_gate import LibraryReadDenied, authorized_read, authorized_search  # noqa: E402
from library_original_host_binding import (  # noqa: E402
    LibraryNativeBindingDenied, OriginalLibraryHostBinding, library_mount_state,
    make_original_library_resolvers)

OWNER = 'oauth:' + 'a' * 64
CLIENT = 'tpc_owner_client'
ZIP_NAME = 'XJ_Starry_Shared_Memory_Reflection_Cards_Library_v7_20261116.zip'


def b64e(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def ecb(key, data):
    e = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return e.update(data) + e.finalize()


def attrs(key, obj):
    plain = b'MEGA' + json.dumps(obj).encode()
    plain += b'\0' * (-len(plain) % 16)
    e = Cipher(algorithms.AES(key), modes.CBC(b'\0' * 16)).encryptor()
    return b64e(e.update(plain) + e.finalize())


class FakeMega:
    """Exported folder 'root_name' with files; serves 'f' and 'g' like MEGA."""

    def __init__(self, root_name='知識庫', extra=()):
        self.folder_handle = 'RootH001'
        self.folder_key = secrets.token_bytes(16)
        self.link = f'https://mega.nz/folder/{self.folder_handle}#{b64e(self.folder_key)}'
        self.nodes, self.blobs, self.calls = [], {}, []
        self.add_folder(self.folder_handle, '', root_name, key=self.folder_key)
        self.add_folder('SubDir01', self.folder_handle, '卡片')
        self.zip_bytes = secrets.token_bytes(9406)
        self.add_file('ZipFile1', self.folder_handle, ZIP_NAME, self.zip_bytes)
        self.add_file('NoteTxt1', 'SubDir01', 'note.md', '# 筆記\n內容'.encode())
        self.add_file('DotDot01', self.folder_handle, '..evil.txt', b'x')
        for args in extra:
            self.add_file(*args)

    def add_folder(self, h, parent, name, key=None):
        key = key or secrets.token_bytes(16)
        self.nodes.append({'h': h, 'p': parent, 't': 1, 'ts': 1795000000,
                           'k': f'{self.folder_handle}:{b64e(ecb(self.folder_key, key))}',
                           'a': attrs(key, {'n': name})})

    def add_file(self, h, parent, name, data):
        aes, nonce, mac = secrets.token_bytes(16), secrets.token_bytes(8), secrets.token_bytes(8)
        tail = nonce + mac
        packed = bytes(a ^ b for a, b in zip(aes, tail)) + tail
        enc = Cipher(algorithms.AES(aes), modes.CTR(nonce + b'\0' * 8)).encryptor()
        self.blobs[h] = enc.update(data) + enc.finalize()
        self.nodes.append({'h': h, 'p': parent, 't': 0, 's': len(data), 'ts': 1795100000,
                           'k': f'{self.folder_handle}:{b64e(ecb(self.folder_key, packed))}',
                           'a': attrs(aes, {'n': name})})

    def post_json(self, url, payload):
        self.calls.append(url)
        assert f'n={self.folder_handle}' in url
        cmd = payload[0]
        if cmd['a'] == 'f':
            return [{'f': self.nodes}]
        if cmd['a'] == 'g':
            if cmd['n'] not in self.blobs:
                return [-9]
            return [{'g': f'https://dl.example/{cmd["n"]}', 's': len(self.blobs[cmd['n']])}]
        return [-2]

    def get_stream(self, url):
        return io.BytesIO(self.blobs[url.rsplit('/', 1)[1]])


def folder_for(fake, **kw):
    return mega_library.MegaFolder(fake.link, post_json=fake.post_json,
                                   get_stream=fake.get_stream, **kw)


def binding_for(fake, owner=OWNER, **kw):
    env = {'XJ_MEGA_KB_ENABLED': 'true', 'XJ_MEGA_KB_FOLDER_LINK': fake.link}
    return mega_library.binding_from_env(env, owner_of_deployment=lambda: owner,
                                         post_json=fake.post_json, get_stream=fake.get_stream, **kw)


class FolderTests(unittest.TestCase):
    def test_tree_paths_and_dotdot_excluded(self):
        fake = FakeMega()
        tree = folder_for(fake).tree()
        paths = sorted(n['path'] for n in tree.values())
        self.assertIn(f'知識庫/{ZIP_NAME}', paths)
        self.assertIn('知識庫/卡片/note.md', paths)
        self.assertNotIn('DotDot01', tree)

    def test_wrong_root_name_denied(self):
        fake = FakeMega(root_name='Other')
        with self.assertRaises(mega_library.MegaDenied):
            folder_for(fake).tree()

    def test_read_decrypts_and_hashes(self):
        fake = FakeMega()
        folder = folder_for(fake)
        node = folder.file_node('m:ZipFile1')
        size, sha, _ = folder.read(node)
        self.assertEqual(size, 9406)
        self.assertEqual(sha, hashlib.sha256(fake.zip_bytes).hexdigest())

    def test_refs_outside_tree_or_paths_rejected(self):
        folder = folder_for(FakeMega())
        for ref in ('m:Missing1', 'm:DotDot01', 'm:SubDir01', '知識庫/../x', '../ZipFile1',
                    'ZipFile1', 'm:ZipFile1/..'):
            with self.assertRaises(mega_library.MegaDenied):
                folder.file_node(ref)

    def test_bad_links_rejected_without_echo(self):
        for link in ('', 'https://evil.example/folder/RootH001#' + 'A' * 22,
                     'https://mega.nz/file/RootH001#' + 'A' * 22, 'https://mega.nz/folder/../x#y'):
            with self.assertRaises(mega_library.MegaDenied) as ctx:
                mega_library.MegaFolder(link)
            self.assertNotIn('mega.nz', str(ctx.exception))

    def test_api_error_codes_do_not_leak_link(self):
        fake = FakeMega()
        folder = mega_library.MegaFolder(fake.link, post_json=lambda u, p: (_ for _ in ()).throw(OSError(u)),
                                         get_stream=fake.get_stream)
        with self.assertRaises(mega_library.MegaDenied) as ctx:
            folder.tree()
        self.assertEqual(str(ctx.exception), 'MEGA_UNREACHABLE')
        self.assertIsNone(ctx.exception.__cause__)


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeMega()
        read, write = make_original_library_resolvers(binding_for(self.fake))
        self.read, self.write = read, write

    def run_search(self, owner=OWNER, query=''):
        return asyncio.run(authorized_search(self.read, lambda: owner, lambda: CLIENT, None, query, '', 50))

    def test_status_registers_catalog_only(self):
        state = library_mount_state(self.read, self.write)
        self.assertEqual(state['state'], 'RESOLVER_REGISTERED_NOT_LIVE_VERIFIED')
        self.assertTrue(state['catalog_resolver_registered'])
        self.assertFalse(state['write_resolver_registered'])

    def test_search_and_read_zip_manifest(self):
        page = self.run_search(query='Reflection_Cards')
        self.assertEqual(page['source'], 'mega-knowledge-base')
        [item] = page['items']
        self.assertEqual((item['size_bytes'], item['kind']), (9406, 'file'))
        view = asyncio.run(authorized_read(self.read, lambda: OWNER, lambda: CLIENT, None,
                                           item['ref'], item['version']))
        self.assertEqual(view['preview_kind'], 'binary_manifest')
        manifest = json.loads(view['preview'])
        self.assertEqual(manifest['size_bytes'], 9406)
        self.assertEqual(manifest['sha256'], hashlib.sha256(self.fake.zip_bytes).hexdigest())
        self.assertNotIn(self.fake.link, json.dumps(view, ensure_ascii=False))
        self.assertNotIn('dl.example', json.dumps(view))

    def test_small_text_returns_excerpt(self):
        [item] = self.run_search(query='note.md')['items']
        view = asyncio.run(authorized_read(self.read, lambda: OWNER, lambda: CLIENT, None,
                                           item['ref'], item['version']))
        self.assertEqual(view['preview_kind'], 'text_excerpt')
        self.assertIn('筆記', view['preview'])

    def test_non_owner_denied(self):
        with self.assertRaises(LibraryReadDenied):
            self.run_search(owner='oauth:' + 'b' * 64)

    def test_stale_version_denied(self):
        [item] = self.run_search(query='Reflection_Cards')['items']
        with self.assertRaises(LibraryReadDenied):
            asyncio.run(authorized_read(self.read, lambda: OWNER, lambda: CLIENT, None,
                                        item['ref'], 'v:1:1'))

    def test_listing_never_contains_link_or_dotdot(self):
        page = self.run_search()
        text = json.dumps(page, ensure_ascii=False)
        self.assertNotIn(self.fake.link, text)
        self.assertNotIn(b64e(self.fake.folder_key), text)
        self.assertNotIn('..evil', text)


class BindingTests(unittest.TestCase):
    def test_disabled_or_bad_link_gives_no_binding(self):
        self.assertIsNone(mega_library.binding_from_env({}))
        self.assertIsNone(mega_library.binding_from_env(
            {'XJ_MEGA_KB_ENABLED': 'true', 'XJ_MEGA_KB_FOLDER_LINK': 'nope'}))

    def test_reader_only_without_write_env(self):
        # Stage 2 (2026-11-21) allows a MEGA writer, but only via XJ_MEGA_KB_WRITE_ENABLED.
        b = binding_for(FakeMega())
        self.assertIsNone(b.resolve_writer)

    def test_mega_writer_resolver_refuses_update(self):
        b = binding_for(FakeMega())
        calls = []

        async def writer(*a):
            calls.append(a)
            raise AssertionError('must not be reached for update')
        withw = OriginalLibraryHostBinding(source=b.source, generation=b.generation,
                                           is_current=b.is_current, resolve_reader=b.resolve_reader,
                                           resolve_writer=writer)
        _, write = make_original_library_resolvers(withw)
        with self.assertRaises(LibraryNativeBindingDenied):
            asyncio.run(write(None, OWNER, CLIENT, 'update', 'm:ZipFile1', 'v:1:1', 'k' * 32))
        self.assertEqual(calls, [])

    def test_unknown_source_still_denied(self):
        b = binding_for(FakeMega())
        bad = OriginalLibraryHostBinding(source='google-drive', generation=b.generation,
                                         is_current=b.is_current, resolve_reader=b.resolve_reader)
        with self.assertRaises(LibraryNativeBindingDenied):
            make_original_library_resolvers(bad)


if __name__ == '__main__':
    unittest.main()
