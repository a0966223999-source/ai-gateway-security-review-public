"""Client-allowlist mode for the MEGA 知識庫 bridge (no per-operation approval).

Fake MEGA account (real crypto) plus fakeredis (real Lua). Run:
python -m unittest tests.test_mega_clients -v
"""
import asyncio
import hashlib
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fakeredis  # noqa: E402

import mega_library  # noqa: E402
from library_catalog_gate import LibraryReadDenied, authorized_read, authorized_search  # noqa: E402
from library_original_host_binding import library_mount_state, make_original_library_resolvers  # noqa: E402
from library_write_gate import LibraryWriteDenied, authorized_status, authorized_write  # noqa: E402
from tests.test_mega_writer import EMAIL, OWNER, PASSWORD, FakeAccount  # noqa: E402

GPT, CLAUDE, STRANGER, READER = 'tpc_gpt_client', 'tpc_claude_client', 'tpc_unknown', 'tpc_read_only'
CREATOR = 'tpc_create_only'


class ClientModeTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeAccount()
        self.redis = fakeredis.FakeRedis(decode_responses=True)
        self.env = {
            'XJ_MEGA_KB_ENABLED': 'true', 'XJ_MEGA_KB_FOLDER_LINK': self.fake.link,
            'XJ_MEGA_KB_WRITE_ENABLED': 'true', 'XJ_MEGA_EMAIL': EMAIL, 'XJ_MEGA_PASSWORD': PASSWORD,
            'XJ_MEGA_KB_CLIENTS_ENABLED': 'true',
            'XJ_MEGA_KB_CLIENTS_JSON': json.dumps({
                GPT: {'member': 'GPT', 'operations': ['read', 'create', 'update']},
                CLAUDE: {'member': 'Claude', 'operations': ['read', 'create', 'update']},
                CREATOR: {'member': 'Gemini', 'operations': ['read', 'create']},
                READER: {'member': 'Grok', 'operations': ['read']},
            }),
        }
        self.mount()

    def mount(self):
        binding = mega_library.binding_from_env(
            self.env, owner_of_deployment=lambda: OWNER, post_json=self.fake.post_json,
            get_stream=self.fake.get_stream, post_bytes=self.fake.post_bytes,
            client_factory=lambda: self.redis)
        self.read, self.write = make_original_library_resolvers(binding)

    def create(self, client, name='note.txt', content='hello', key='op-1', parent='kb:root',
               owner=OWNER):
        return asyncio.run(authorized_write(
            self.write, lambda: owner, lambda: client, None, 'create', parent, 'create-only',
            key, 'text/plain', content, 'text', name))

    def search(self, client, query=''):
        return asyncio.run(authorized_search(self.read, lambda: OWNER, lambda: client, None,
                                             query, '', 50))

    def read_item(self, client, ref, version):
        return asyncio.run(authorized_read(self.read, lambda: OWNER, lambda: client, None,
                                           ref, version))

    def test_status_registers_both(self):
        state = library_mount_state(self.read, self.write)
        self.assertTrue(state['catalog_resolver_registered'] and state['write_resolver_registered'])

    def test_write_without_ticket_then_other_ai_reads_back(self):
        rec = self.create(GPT, content='shared note')
        self.assertEqual(rec['status'], 'committed')
        self.assertEqual(self.fake.puts[0]['t'], self.fake.folder_handle)
        [item] = [i for i in self.search(CLAUDE, 'note.txt')['items'] if i['name'] == '知識庫/note.txt']
        view = self.read_item(CLAUDE, item['ref'], item['version'])
        self.assertEqual(hashlib.sha256(view['preview'].encode()).hexdigest(),
                         hashlib.sha256(b'shared note').hexdigest())
        status = asyncio.run(authorized_status(self.write, lambda: OWNER, lambda: GPT, None, 'op-1'))
        self.assertEqual(status['status'], 'committed')

    def test_unlisted_client_cannot_read_or_write(self):
        with self.assertRaises(LibraryReadDenied):
            self.search(STRANGER)
        with self.assertRaises(LibraryWriteDenied):
            self.create(STRANGER)
        self.assertFalse(self.fake.puts)

    def test_read_only_client_cannot_write(self):
        self.assertTrue(self.search(READER)['items'])
        with self.assertRaises(LibraryWriteDenied):
            self.create(READER)
        self.assertFalse(self.fake.puts)

    def test_non_owner_subject_denied(self):
        with self.assertRaises(LibraryWriteDenied):
            self.create(GPT, owner='oauth:' + 'b' * 64)
        self.assertFalse(self.fake.puts)

    def test_create_anywhere_inside_kb(self):
        self.assertEqual(self.create(GPT, parent='m:SubDir01', key='k-sub')['status'], 'committed')
        self.assertEqual(self.fake.puts[0]['t'], 'SubDir01')
        for parent in ('m:Outside1', 'm:ZipFile1', 'm:DotDot01'):  # not a folder in 知識庫
            with self.assertRaises(LibraryWriteDenied):
                self.create(GPT, parent=parent, key='k-' + parent[2:])
        self.assertEqual(len(self.fake.puts), 1)

    def test_no_overwrite_and_key_replay(self):
        self.create(GPT)
        with self.assertRaises(LibraryWriteDenied):  # same name, other AI, new key
            self.create(CLAUDE, key='op-2')
        with self.assertRaises(LibraryWriteDenied):  # same key, new name
            self.create(GPT, name='other.txt')
        self.assertEqual(len(self.fake.puts), 1)

    def test_same_key_is_per_client(self):
        self.create(GPT, name='a.txt', key='same')
        self.assertEqual(self.create(CLAUDE, name='b.txt', key='same')['status'], 'committed')
        other = asyncio.run(authorized_status(self.write, lambda: OWNER, lambda: CLAUDE, None, 'same'))
        self.assertEqual(other['status'], 'committed')

    def test_status_of_other_clients_key_is_not_visible(self):
        self.create(GPT, key='private-key')
        status = asyncio.run(authorized_status(self.write, lambda: OWNER, lambda: CLAUDE, None,
                                               'private-key'))
        self.assertEqual(status['status'], 'denied')

    def test_daily_limit(self):
        self.env['XJ_MEGA_KB_DAILY_CREATES'] = '2'
        self.mount()
        self.create(GPT, name='1.txt', key='k1')
        self.create(GPT, name='2.txt', key='k2')
        with self.assertRaises(LibraryWriteDenied):
            self.create(GPT, name='3.txt', key='k3')
        self.assertEqual(self.create(CLAUDE, name='4.txt', key='k4')['status'], 'committed')
        self.assertEqual(len(self.fake.puts), 3)

    def update(self, client, ref, version, content, key):
        return asyncio.run(authorized_write(self.write, lambda: OWNER, lambda: client, None,
                                            'update', ref, version, key, 'text/plain', content,
                                            'text', ''))

    def item(self, client, name='note.txt'):
        [it] = [i for i in self.search(client, name)['items']
                if i['kind'] == 'file' and i['name'].rsplit('/', 1)[-1] == name]
        return it

    def test_overwrite_keeps_one_file_and_only_previous_version(self):
        self.create(GPT, content='v1')
        first = self.item(CLAUDE)
        rec2 = self.update(CLAUDE, first['ref'], first['version'], 'v2', 'u2')
        self.assertEqual(rec2['status'], 'committed')
        self.assertNotEqual(rec2['ref'], first['ref'])  # MEGA gives the new node a new handle
        second = self.item(GPT)
        self.assertEqual(self.read_item(GPT, second['ref'], second['version'])['preview'], 'v2')
        self.assertEqual(self.fake.deleted, [])  # v1 kept as the previous version
        rec3 = self.update(GPT, second['ref'], second['version'], 'v3', 'u3')
        third = self.item(CLAUDE)
        self.assertEqual(third['ref'], rec3['ref'])
        self.assertEqual(self.read_item(CLAUDE, third['ref'], third['version'])['preview'], 'v3')
        # Folder still shows one file; v2 is the only previous version; v1 deleted.
        files = [i for i in self.search(GPT, 'note.txt')['items'] if i['name'].endswith('/note.txt')]
        self.assertEqual(len(files), 1)
        under = [n for n in self.fake.nodes if n['p'] == rec3['ref'][2:]]
        self.assertEqual([n['h'] for n in under], [second['ref'][2:]])
        self.assertEqual(self.fake.deleted, [first['ref'][2:]])
        self.assertFalse([n for n in self.fake.nodes if n['p'] == second['ref'][2:]])

    def test_stale_version_cannot_overwrite(self):
        self.create(GPT, content='v1')
        first = self.item(GPT)
        self.update(GPT, first['ref'], first['version'], 'v2', 'u2')
        with self.assertRaises(LibraryWriteDenied):  # old ref/version is gone
            self.update(CLAUDE, first['ref'], first['version'], 'v2b', 'u2b')
        second = self.item(GPT)
        with self.assertRaises(LibraryWriteDenied):  # right ref, wrong version
            self.update(CLAUDE, second['ref'], 'v:1:1', 'v3', 'u3')
        self.assertEqual(len(self.fake.puts), 2)

    def test_two_writers_same_version_only_one_wins(self):
        self.create(GPT, content='v1')
        first = self.item(GPT)
        # Simulate a race: reserve the slot as if the other writer got there first.
        from mega_writer import ClientLedger
        ClientLedger(OWNER, GPT, lambda: self.redis).reserve(
            'racer', 'upd:' + first['ref'][2:], '', {'status': 'pending'}, 50)
        with self.assertRaises(LibraryWriteDenied):
            self.update(CLAUDE, first['ref'], first['version'], 'mine', 'u-race')
        self.assertEqual(len(self.fake.puts), 1)

    def test_overwrite_existing_kb_text_file_but_never_a_binary(self):
        with self.assertRaises(LibraryWriteDenied):  # the v7 zip backup is not text
            self.update(GPT, 'm:ZipFile1', 'v:1795100000:9406', 'x', 'k-zip')
        self.assertFalse(self.fake.puts)
        note = self.item(GPT, 'note.md')
        rec = self.update(GPT, note['ref'], note['version'], '# 新筆記', 'k-note')
        self.assertEqual(rec['status'], 'committed')
        self.assertEqual(self.fake.puts[0]['t'], 'SubDir01')
        again = self.item(CLAUDE, 'note.md')
        self.assertEqual(again['name'], '知識庫/卡片/note.md')
        self.assertEqual(self.read_item(CLAUDE, again['ref'], again['version'])['preview'], '# 新筆記')

    def test_large_card_library_round_trip(self):
        big = json.dumps({'cards': [{'id': f'c{i}', 'notes': ['x' * 200] * 10} for i in range(100)]})
        self.assertGreater(len(big), 100_000)
        self.create(GPT, name='cards.json', content=big, key='big')
        it = self.item(CLAUDE, 'cards.json')
        self.assertEqual(json.loads(self.read_item(CLAUDE, it['ref'], it['version'])['preview']),
                         json.loads(big))

    def test_update_needs_update_permission(self):
        self.create(GPT, content='v1')
        first = self.item(GPT)
        with self.assertRaises(LibraryWriteDenied):
            self.update(CREATOR, first['ref'], first['version'], 'x', 'k')
        with self.assertRaises(LibraryWriteDenied):
            self.update(READER, first['ref'], first['version'], 'x', 'k2')
        self.assertEqual(len(self.fake.puts), 1)

    def test_versions_never_listed_or_readable(self):
        self.create(GPT, content='v1')
        first = self.item(GPT)
        self.update(GPT, first['ref'], first['version'], 'v2', 'u2')
        refs = [i['ref'] for i in self.search(GPT)['items']]
        self.assertNotIn(first['ref'], refs)
        with self.assertRaises(LibraryReadDenied):
            self.read_item(GPT, first['ref'], first['version'])

    def test_revocation_on_next_config(self):
        self.env['XJ_MEGA_KB_CLIENTS_JSON'] = json.dumps({CLAUDE: {'member': 'Claude',
                                                                   'operations': ['read']}})
        with self.assertRaises(LibraryReadDenied):
            self.search(GPT)
        with self.assertRaises(LibraryWriteDenied):
            self.create(CLAUDE)
        self.assertFalse(self.fake.puts)

    def test_whoami_describe(self):
        from mega_policy import policy_from_env
        policy = policy_from_env(self.env, lambda: OWNER)
        self.assertEqual(policy.describe(OWNER, GPT),
                         {'member': 'GPT', 'operations': ['read', 'create', 'update'],
                          'daily_writes': 50})
        self.assertEqual(policy.describe(OWNER, STRANGER)['operations'], [])
        self.assertEqual(policy.describe('oauth:' + 'b' * 64, GPT)['operations'], [])

    def test_malformed_list_denies_everyone(self):
        self.env['XJ_MEGA_KB_CLIENTS_JSON'] = '{not json'
        with self.assertRaises(LibraryReadDenied):
            self.search(GPT)

    def test_client_mode_off_keeps_owner_ticket_mode(self):
        self.env.pop('XJ_MEGA_KB_CLIENTS_ENABLED')
        self.mount()
        self.assertTrue(self.search(STRANGER)['items'])  # stage-1/2 behaviour: owner subject only
        with self.assertRaises(LibraryWriteDenied):  # writes still need an Owner ticket
            self.create(GPT)

    def test_upload_failure_reports_pending_and_is_not_retried(self):
        self.fake.post_bytes = lambda url, data: (_ for _ in ()).throw(OSError('net'))
        self.mount()
        with self.assertRaises(LibraryWriteDenied):
            self.create(GPT, key='flaky')
        status = asyncio.run(authorized_status(self.write, lambda: OWNER, lambda: GPT, None, 'flaky'))
        self.assertEqual(status['status'], 'pending')
        with self.assertRaises(LibraryWriteDenied):
            self.create(GPT, key='flaky')


if __name__ == '__main__':
    unittest.main()
