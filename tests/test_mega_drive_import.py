"""Offline MEGA ← Google Drive import tests. Fake Drive and fake MEGA, no network.

Run: python -m unittest tests.test_mega_drive_import -v
"""
import asyncio
import contextlib
import hashlib
import io
import json
import os
import sys
import unittest
import urllib.request
from urllib.parse import parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fakeredis  # noqa: E402
import jwt  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

import drive_client  # noqa: E402
import mega_library  # noqa: E402
import mega_writer  # noqa: E402
from drive_client import parse_allowlist, parse_cap  # noqa: E402
from library_catalog_gate import authorized_read, authorized_search  # noqa: E402
from library_original_host_binding import make_original_library_resolvers  # noqa: E402
from library_write_gate import LibraryWriteDenied, authorized_status, authorized_write  # noqa: E402
from mega_drive_import import RECEIPT_KEYS  # noqa: E402
from mega_library import MegaDenied  # noqa: E402
from tests.test_mega_clients import CLAUDE, CREATOR, GPT, READER, STRANGER  # noqa: E402
from tests.test_mega_library import ZIP_NAME  # noqa: E402
from tests.test_mega_writer import EMAIL, OWNER, PASSWORD, FakeAccount  # noqa: E402

FOLDER = 'AllowedFoldr1'
SECOND = 'SecondFolder2'
FILE = 'DriveFileZZ1'
TOKEN = 'ya29.test-token'


def service_account():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    raw = json.dumps({
        'type': 'service_account',
        'client_email': 'kb-importer@example.iam.gserviceaccount.com',
        'private_key': pem,
        'token_uri': 'https://oauth2.googleapis.com/token',
    })
    return raw, pem, key.public_key()


class FakeDrive:
    def __init__(self, file_id, parent, data, name='other.ZIP'):
        self.file_id, self.parent, self.data, self.name = file_id, parent, data, name
        self.extra_parents, self.revision = [], 'rev-1'
        self.mime, self.omit_size, self.report_id = 'application/zip', False, None
        self.token_calls = self.meta_calls = self.download_calls = 0
        self.urls, self.bodies, self.token_urls, self.auth_headers = [], [], [], []

    def post_form(self, url, body):
        self.token_calls += 1
        self.token_urls.append(url)
        self.bodies.append(body)
        return {'access_token': TOKEN, 'expires_in': 3600}

    def open_get(self, url, headers):
        self.urls.append(url)
        self.auth_headers.append(headers.get('Authorization'))
        if 'alt=media' in url:
            self.download_calls += 1
            return io.BytesIO(self.data)
        self.meta_calls += 1
        body = {'id': self.report_id or self.file_id, 'name': self.name,
                'mimeType': self.mime, 'parents': [self.parent, *self.extra_parents],
                'headRevisionId': self.revision}
        if not self.omit_size:
            body['size'] = str(len(self.data))
        return io.BytesIO(json.dumps(body).encode())


class DriveHttpTests(unittest.TestCase):
    def test_cap_and_allowlist_fail_closed(self):
        self.assertEqual(parse_cap(None), 25 * 1024 * 1024)
        self.assertEqual(parse_cap(''), 25 * 1024 * 1024)
        self.assertEqual(parse_cap(str(25 * 1024 * 1024)), 25 * 1024 * 1024)
        for bad in ('0', '26214401', '1.5', ' 12', '0x10'):
            with self.assertRaises(MegaDenied):
                parse_cap(bad)
        self.assertEqual(parse_allowlist('AllowedFoldr1, SecondFolder2'),
                         frozenset({FOLDER, SECOND}))
        for bad in ('', 'short', 'AllowedFoldr1,', 'has space id!!'):
            with self.assertRaises(MegaDenied):
                parse_allowlist(bad)

    def test_redirect_off_google_is_refused_without_echoing_the_target(self):
        handler = drive_client._GoogleRedirect()
        req = urllib.request.Request(
            'https://www.googleapis.com/drive/v3/files/' + FILE + '?alt=media',
            headers={'Authorization': 'Bearer ' + TOKEN})
        with self.assertRaises(MegaDenied) as caught:
            handler.redirect_request(req, None, 302, 'Found', {}, 'https://evil.example/collect')
        self.assertEqual(str(caught.exception), 'DRIVE_UNREACHABLE')
        self.assertNotIn('evil.example', repr(caught.exception))
        self.assertNotIn('ya29', repr(caught.exception))

    def test_cross_host_google_redirect_drops_the_bearer(self):
        handler = drive_client._GoogleRedirect()
        req = urllib.request.Request(
            'https://www.googleapis.com/drive/v3/files/' + FILE + '?alt=media',
            method='GET', headers={'Authorization': 'Bearer ' + TOKEN})
        new = handler.redirect_request(
            req, None, 302, 'Found', {},
            'https://doc-abc.googleusercontent.com/download/xyz')
        self.assertIsNone(new.get_header('Authorization'))

    def test_token_endpoint_refuses_redirects(self):
        handler = drive_client._NoRedirect()
        req = urllib.request.Request(drive_client.TOKEN_URL, data=b'x', method='POST')
        with self.assertRaises(MegaDenied) as caught:
            handler.redirect_request(req, None, 302, 'Found', {}, 'https://evil.example/token')
        self.assertEqual(str(caught.exception), 'DRIVE_AUTH_FAILED')
        self.assertNotIn('evil.example', repr(caught.exception))


class ImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sa, cls.pem, cls.public = service_account()

    def setUp(self):
        self.account = FakeAccount()
        self.data = b'pk\x03\x04drive-import-test'
        self.sha = hashlib.sha256(self.data).hexdigest()
        self.drive = FakeDrive(FILE, FOLDER, self.data)
        self.redis = fakeredis.FakeRedis(decode_responses=True)
        self.byte_post = self.account.post_bytes
        self.env = {
            'XJ_MEGA_KB_ENABLED': 'true', 'XJ_MEGA_KB_FOLDER_LINK': self.account.link,
            'XJ_MEGA_KB_WRITE_ENABLED': 'true', 'XJ_MEGA_EMAIL': EMAIL,
            'XJ_MEGA_PASSWORD': PASSWORD, 'XJ_MEGA_KB_CLIENTS_ENABLED': 'true',
            'XJ_MEGA_KB_CLIENTS_JSON': json.dumps({
                GPT: {'member': 'GPT', 'operations': ['read', 'create', 'update']},
                CLAUDE: {'member': 'Claude', 'operations': ['read', 'create', 'update']},
                CREATOR: {'member': 'Gemini', 'operations': ['read', 'create']},
                READER: {'member': 'Grok', 'operations': ['read']},
            }),
            'XJ_MEGA_KB_DRIVE_IMPORT_ENABLED': 'true',
            'XJ_DRIVE_SA_JSON': self.sa,
            'XJ_DRIVE_ALLOWED_FOLDER_IDS': FOLDER,
        }
        self.mount()

    def mount(self):
        def post_bytes(url, data):
            return self.byte_post(url, data)
        self.binding = mega_library.binding_from_env(
            self.env, owner_of_deployment=lambda: OWNER,
            post_json=self.account.post_json, get_stream=self.account.get_stream,
            post_bytes=post_bytes, client_factory=lambda: self.redis,
            drive_post_form=self.drive.post_form, drive_open=self.drive.open_get)
        self.read, self.write = make_original_library_resolvers(self.binding)
        self.importer = self.binding.drive_import

    def capture(self, fn):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                return fn(), buf.getvalue(), None
            except MegaDenied as exc:
                return None, buf.getvalue(), exc

    def assert_quiet(self, log, receipt=None):
        blob = log + (json.dumps(receipt) if receipt else '')
        for secret in (self.pem, 'PRIVATE KEY', TOKEN, 'ya29', FILE, FOLDER, PASSWORD,
                       self.account.link, 'https://', 'evil.example'):
            self.assertNotIn(secret, blob)

    def do_import(self, client=GPT, key='op-drive-1', name='fresh.zip', owner=OWNER,
                  parent='kb:root', sha=None, size=None, file_id=FILE):
        return asyncio.run(self.importer.import_file(
            None, owner, client, parent, name, file_id,
            self.sha if sha is None else sha,
            len(self.drive.data) if size is None else size, key))

    def do_update(self, ref='m:ZipFile1', version='v:1795100000:9406', key='up-1',
                  client=GPT, sha=None, size=None):
        return asyncio.run(self.importer.update_file(
            None, OWNER, client, ref, version, FILE,
            self.sha if sha is None else sha,
            len(self.drive.data) if size is None else size, key))

    def test_flag_off_leaves_text_writer_and_mounts_no_importer(self):
        self.env.pop('XJ_MEGA_KB_DRIVE_IMPORT_ENABLED')
        self.mount()
        self.assertIsNone(self.importer)
        self.assertIsNotNone(self.write)

    def test_client_allowlist_off_leaves_importer_off(self):
        self.env.pop('XJ_MEGA_KB_CLIENTS_ENABLED')
        self.mount()
        self.assertIsNone(self.importer)
        self.assertIsNotNone(self.write)

    def test_bad_service_account_is_not_logged(self):
        info = json.loads(self.sa)
        info['token_uri'] = 'https://evil.example/token'
        self.env['XJ_DRIVE_SA_JSON'] = json.dumps(info)
        _binding, log, _exc = self.capture(self.mount)
        self.assertIsNone(self.importer)
        self.assertIn('DRIVE_SA_INVALID', log)
        self.assert_quiet(log)

    def test_import_then_read_back_and_replay_does_not_upload_again(self):
        receipt, log, exc = self.capture(lambda: self.do_import(sha=self.sha.upper()))
        self.assertIsNone(exc)
        self.assertEqual(set(receipt), RECEIPT_KEYS)
        self.assertEqual(receipt['status'], 'committed')
        self.assertEqual(receipt['source'], 'mega-knowledge-base')
        self.assertEqual(receipt['operation'], 'create')
        self.assert_quiet(log, receipt)
        again, log2, exc2 = self.capture(lambda: self.do_import(sha=self.sha))
        self.assertIsNone(exc2)
        self.assertEqual(again['ref'], receipt['ref'])
        self.assertEqual(len(self.account.puts), 1)
        self.assertEqual(self.drive.download_calls, 1)
        self.assertEqual(self.drive.meta_calls, 2)
        self.assertEqual(self.drive.token_calls, 1)
        self.assertEqual(self.drive.token_urls, ['https://oauth2.googleapis.com/token'])
        self.assertNotIn('PRIVATE', self.drive.bodies[0].decode())
        claims = jwt.decode(parse_qs(self.drive.bodies[0].decode())['assertion'][0],
                            self.public, algorithms=['RS256'],
                            audience='https://oauth2.googleapis.com/token')
        self.assertEqual(claims['scope'], 'https://www.googleapis.com/auth/drive.readonly')
        self.assertTrue(all(FILE in url and 'supportsAllDrives=true' in url
                           for url in self.drive.urls))
        self.assertTrue(any('alt=media' in url for url in self.drive.urls))
        self.assertTrue(all(header == 'Bearer ' + TOKEN for header in self.drive.auth_headers))
        self.assertNotIn(TOKEN, ''.join(self.drive.urls))
        page = asyncio.run(authorized_search(
            self.read, lambda: OWNER, lambda: CLAUDE, None, 'fresh.zip', '', 50))
        [item] = [row for row in page['items'] if row['name'].endswith('/fresh.zip')]
        view = asyncio.run(authorized_read(
            self.read, lambda: OWNER, lambda: CLAUDE, None, item['ref'], item['version']))
        self.assertEqual(json.loads(view['preview'])['sha256'], self.sha)
        status = asyncio.run(authorized_status(
            self.write, lambda: OWNER, lambda: GPT, None, 'op-drive-1'))
        self.assertEqual(status['status'], 'committed')
        hidden = asyncio.run(authorized_status(
            self.write, lambda: OWNER, lambda: CLAUDE, None, 'op-drive-1'))
        self.assertEqual(hidden['status'], 'denied')
        self.assert_quiet(log2, again)

    def test_reused_key_with_different_inputs_is_rejected(self):
        self.do_import()
        self.drive.revision = 'rev-2'
        _receipt, _log, exc = self.capture(lambda: self.do_import(sha='cd' * 32))
        self.assertEqual(str(exc), 'MEGA_OPERATION_DIGEST_MISMATCH')
        self.assertEqual(len(self.account.puts), 1)
        self.assertEqual(self.drive.download_calls, 1)

    def test_sha_mismatch_denies_before_upload_and_blocks_the_key(self):
        _receipt, log, exc = self.capture(lambda: self.do_import(sha='ab' * 32))
        self.assertEqual(str(exc), 'DRIVE_SHA_MISMATCH')
        self.assertEqual(self.account.puts, [])
        self.assertEqual(self.drive.download_calls, 1)
        self.assert_quiet(log)
        status = asyncio.run(authorized_status(
            self.write, lambda: OWNER, lambda: GPT, None, 'op-drive-1'))
        self.assertEqual(status['status'], 'denied')
        _receipt, _log, again = self.capture(lambda: self.do_import(sha='ab' * 32))
        self.assertEqual(str(again), 'MEGA_OPERATION_OR_NAME_ALREADY_USED')
        fixed, _log, exc2 = self.capture(lambda: self.do_import(key='op-fixed'))
        self.assertIsNone(exc2)
        self.assertEqual(fixed['status'], 'committed')

    def test_upload_failure_stays_pending_and_is_not_retried(self):
        def boom(url, data):
            raise OSError('net')
        self.byte_post = boom
        _receipt, log, exc = self.capture(self.do_import)
        self.assertEqual(str(exc), 'MEGA_UPLOAD_FAILED')
        self.assertNotIn('net', log)
        self.assert_quiet(log)
        self.assertEqual(self.account.puts, [])
        status = asyncio.run(authorized_status(
            self.write, lambda: OWNER, lambda: GPT, None, 'op-drive-1'))
        self.assertEqual(status['status'], 'pending')
        _receipt, _log, again = self.capture(self.do_import)
        self.assertEqual(str(again), 'MEGA_OPERATION_PENDING')
        self.assertEqual(self.account.puts, [])

    def test_parent_must_be_allowlisted_before_reserve(self):
        self.drive.parent = 'OtherFolder99'
        _receipt, _log, exc = self.capture(self.do_import)
        self.assertEqual(str(exc), 'DRIVE_PARENT_NOT_ALLOWED')
        self.assertEqual(self.account.puts, [])
        self.drive.parent = FOLDER
        self.drive.extra_parents = ['OtherFolder99']
        _receipt, _log, exc = self.capture(self.do_import)
        self.assertEqual(str(exc), 'DRIVE_PARENT_NOT_ALLOWED')
        self.drive.extra_parents = []
        receipt, _log, exc = self.capture(self.do_import)
        self.assertIsNone(exc)
        self.assertEqual(receipt['status'], 'committed')

    def test_every_parent_must_be_allowlisted(self):
        self.env['XJ_DRIVE_ALLOWED_FOLDER_IDS'] = FOLDER + ',' + SECOND
        self.mount()
        self.drive.extra_parents = [SECOND]
        receipt, _log, exc = self.capture(self.do_import)
        self.assertIsNone(exc)
        self.assertEqual(receipt['status'], 'committed')

    def test_size_hash_and_id_are_checked_before_upload(self):
        _receipt, _log, exc = self.capture(lambda: self.do_import(size=len(self.data) + 5))
        self.assertEqual(str(exc), 'DRIVE_SIZE_MISMATCH')
        self.assertEqual(self.drive.download_calls, 0)
        self.env['XJ_MEGA_KB_DRIVE_IMPORT_MAX_BYTES'] = '8'
        self.mount()
        tokens = self.drive.token_calls
        _receipt, _log, exc = self.capture(self.do_import)
        self.assertEqual(str(exc), 'DRIVE_FILE_TOO_LARGE')
        self.assertEqual(self.drive.token_calls, tokens)
        self.env.pop('XJ_MEGA_KB_DRIVE_IMPORT_MAX_BYTES')
        self.mount()
        tokens = self.drive.token_calls
        _receipt, _log, exc = self.capture(lambda: self.do_import(file_id='../secret'))
        self.assertEqual(str(exc), 'DRIVE_ID_INVALID')
        self.assertEqual(self.drive.token_calls, tokens)
        _receipt, _log, exc = self.capture(lambda: self.do_import(sha='nope'))
        self.assertEqual(str(exc), 'DRIVE_SHA_INVALID')
        self.assertEqual(self.drive.token_calls, tokens)
        self.drive.mime = 'application/vnd.google-apps.document'
        self.drive.omit_size = True
        _receipt, _log, exc = self.capture(self.do_import)
        self.assertEqual(str(exc), 'DRIVE_NOT_DOWNLOADABLE')
        self.assertEqual(self.account.puts, [])

    def test_invalid_cap_disables_import_without_a_secret_log(self):
        self.env['XJ_MEGA_KB_DRIVE_IMPORT_MAX_BYTES'] = '26214401'
        _binding, log, exc = self.capture(self.mount)
        self.assertIsNone(exc)
        self.assertIsNone(self.importer)
        self.assertIsNotNone(self.write)
        self.assertIn('DRIVE_CAP_INVALID', log)
        self.assert_quiet(log)

    def test_callers_without_create_never_reach_drive(self):
        for client in (STRANGER, READER):
            _receipt, _log, exc = self.capture(lambda client=client: self.do_import(client=client))
            self.assertEqual(str(exc), 'MEGA_CLIENT_NOT_ALLOWED')
        _receipt, _log, exc = self.capture(
            lambda: self.do_import(owner='oauth:' + 'b' * 64))
        self.assertEqual(str(exc), 'MEGA_NOT_OWNER')
        self.assertEqual(self.drive.token_calls, 0)
        self.assertEqual(self.account.puts, [])

    def test_login_failure_does_not_consume_the_key(self):
        self.env['XJ_MEGA_PASSWORD'] = 'wrong'
        self.mount()
        _receipt, log, exc = self.capture(self.do_import)
        self.assertEqual(str(exc), 'MEGA_LOGIN_FAILED')
        self.assert_quiet(log)
        self.assertEqual(self.account.puts, [])
        self.env['XJ_MEGA_PASSWORD'] = PASSWORD
        self.mount()
        receipt, _log, exc = self.capture(self.do_import)
        self.assertIsNone(exc)
        self.assertEqual(receipt['status'], 'committed')

    def test_name_collision_and_extension_do_not_consume_the_key(self):
        _receipt, _log, exc = self.capture(lambda: self.do_import(name=ZIP_NAME))
        self.assertEqual(str(exc), 'MEGA_NAME_EXISTS')
        self.drive.name = 'notes.txt'
        _receipt, _log, exc = self.capture(self.do_import)
        self.assertEqual(str(exc), 'MEGA_EXTENSION_MISMATCH')
        self.assertEqual(self.account.puts, [])
        self.drive.name = 'other.ZIP'
        receipt, _log, exc = self.capture(self.do_import)
        self.assertIsNone(exc)
        self.assertEqual(receipt['status'], 'committed')

    def test_daily_cap_is_shared_with_text_writes(self):
        self.env['XJ_MEGA_KB_DAILY_CREATES'] = '2'
        self.mount()
        asyncio.run(authorized_write(
            self.write, lambda: OWNER, lambda: GPT, None, 'create', 'kb:root', 'create-only',
            'text-1', 'text/plain', 'hello', 'text', 'a.txt'))
        self.assertEqual(self.do_import(name='b.zip', key='drive-1')['status'], 'committed')
        _receipt, _log, exc = self.capture(lambda: self.do_import(name='c.zip', key='drive-2'))
        self.assertEqual(str(exc), 'MEGA_DAILY_LIMIT')
        self.assertEqual(len(self.account.puts), 2)

    def test_text_update_still_refuses_a_binary(self):
        with self.assertRaises(LibraryWriteDenied):
            asyncio.run(authorized_write(
                self.write, lambda: OWNER, lambda: GPT, None, 'update', 'm:ZipFile1',
                'v:1795100000:9406', 'k-zip', 'text/plain', 'x', 'text', ''))
        self.assertEqual(self.account.puts, [])

    def test_binary_overwrite_matches_extension_and_version(self):
        _receipt, _log, exc = self.capture(lambda: self.do_update(version='v:1:1', key='stale'))
        self.assertEqual(str(exc), 'MEGA_VERSION_CHANGED')
        self.drive.name = 'notes.txt'
        _receipt, _log, exc = self.capture(self.do_update)
        self.assertEqual(str(exc), 'MEGA_EXTENSION_MISMATCH')
        self.assertEqual(self.account.puts, [])
        self.assertEqual(self.drive.download_calls, 0)
        self.drive.name = 'replacement.zip'
        first, _log, exc = self.capture(lambda: self.do_update(key='u1'))
        self.assertIsNone(exc)
        self.assertEqual(first['operation'], 'update')
        self.assertNotEqual(first['ref'], 'm:ZipFile1')
        page = asyncio.run(authorized_search(
            self.read, lambda: OWNER, lambda: GPT, None, 'Reflection_Cards', '', 50))
        [item] = [row for row in page['items'] if row['kind'] == 'file']
        view = asyncio.run(authorized_read(
            self.read, lambda: OWNER, lambda: GPT, None, item['ref'], item['version']))
        self.assertEqual(json.loads(view['preview'])['sha256'], self.sha)
        self.drive.data = b'pk\x03\x04second-version'
        self.drive.revision = 'rev-2'
        second_sha = hashlib.sha256(self.drive.data).hexdigest()
        second, _log, exc = self.capture(lambda: self.do_update(
            ref=first['ref'], version=first['version'], key='u2', sha=second_sha,
            size=len(self.drive.data)))
        self.assertIsNone(exc)
        self.assertEqual(self.account.deleted, ['ZipFile1'])
        self.assertEqual(second['status'], 'committed')
        _receipt, _log, exc = self.capture(
            lambda: self.do_update(ref=second['ref'], version=second['version'],
                                   key='u3', client=CREATOR))
        self.assertEqual(str(exc), 'MEGA_CLIENT_NOT_ALLOWED')

    def test_chunked_import_round_trips_past_one_mebibyte(self):
        payload = b'Z' * (mega_writer.UPLOAD_CHUNK + 64)
        self.drive.data = payload
        self.drive.name = 'big.zip'
        posts, parts = [], {}

        def post_bytes(url, data):
            off = int(url.rsplit('/', 1)[1])
            posts.append(off)
            parts[off] = data
            if off + len(data) != len(payload):
                return ''
            token = 'TOKDRIVE1'
            self.account.uploads[token] = b''.join(parts[index] for index in sorted(parts))
            return token

        self.byte_post = post_bytes
        sha = hashlib.sha256(payload).hexdigest()
        receipt, log, exc = self.capture(
            lambda: self.do_import(name='big.zip', sha=sha, size=len(payload), key='big-1'))
        self.assertIsNone(exc, exc)
        self.assertEqual(receipt['status'], 'committed')
        self.assertEqual(posts, [0, mega_writer.UPLOAD_CHUNK])
        self.assert_quiet(log, receipt)
        page = asyncio.run(authorized_search(
            self.read, lambda: OWNER, lambda: GPT, None, 'big.zip', '', 50))
        [item] = [row for row in page['items'] if row['name'].endswith('/big.zip')]
        view = asyncio.run(authorized_read(
            self.read, lambda: OWNER, lambda: GPT, None, item['ref'], item['version']))
        self.assertEqual(json.loads(view['preview'])['sha256'], sha)


if __name__ == '__main__':
    unittest.main()
