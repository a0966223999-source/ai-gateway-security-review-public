import asyncio
import hashlib
import json
import secrets
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import approval
import mega_writer
from knowledge_approval import MegaKnowledgeApproval, bound_plan, client_fingerprint
from library_catalog_mcp_entry import mount_library_catalog_tools
from library_write_gate import LibraryWriteDenied, authorized_write
from mega_library import binding_from_env
from library_original_host_binding import make_original_library_resolvers
from tests.test_mega_writer import CLIENT, OWNER, EMAIL, PASSWORD, FakeAccount, FakeRedis


class RedisFixture(FakeRedis):
    def eval(self, script, keys_count, *args):
        keys, argv = args[:keys_count], args[keys_count:]
        if script == approval.CLAIM:
            row = self.h[keys[0]]
            if row['state'] != 'approved':
                return row['state']
            if row['plan_sha256'] != argv[3] or row['expires'] <= argv[0]:
                return 'denied'
            row['state'] = 'claimed'
            return 'claimed_now'
        if script == mega_writer.RESERVE_APPROVED:
            if any(key in self.kv for key in keys):
                return 0
            self.kv.update(dict(zip(keys, (argv[0], argv[0], argv[1]))))
            return 1
        raise AssertionError('unexpected script')


class ScopedMegaTests(unittest.TestCase):
    def setUp(self):
        self.redis = RedisFixture()
        self.mega = FakeAccount()
        self.mega.add_folder('TestDir1', self.mega.folder_handle, '_gateway_test')
        other = 'fixture-claude'
        self.env = {
            'XJ_APPROVAL_ENABLED': 'true', 'XJ_MEGA_KB_APPROVAL_V1_ENABLED': 'true',
            'XJ_MEGA_KB_ENABLED': 'true', 'XJ_MEGA_KB_FOLDER_LINK': self.mega.link,
            'XJ_MEGA_KB_WRITE_ENABLED': 'true', 'XJ_MEGA_EMAIL': EMAIL, 'XJ_MEGA_PASSWORD': PASSWORD,
            'XJ_MEGA_KB_APPROVAL_CLIENTS_JSON': json.dumps({
                CLIENT: {'member': 'GPT', 'operations': ['read', 'create']},
                other: {'member': 'Claude', 'operations': ['read', 'create']},
            }),
        }
        self.other = other
        self.policy = MegaKnowledgeApproval(self.env, lambda: OWNER, lambda: self.redis)
        binding = binding_from_env(self.env, owner_of_deployment=lambda: OWNER,
                                   post_json=self.mega.post_json, get_stream=self.mega.get_stream,
                                   post_bytes=self.mega.post_bytes, client_factory=lambda: self.redis,
                                   approval_policy=self.policy)
        self.read, self.write = make_original_library_resolvers(binding)

    def ticket(self, *, operation='create', client=CLIENT, name='fixture.txt', ref='kb:test',
               version='create-only', key='fixture-op', state='approved', scope='knowledge'):
        ticket = secrets.token_hex(16)
        plan = dict(operation=operation, collection='shared_short_memory', target_ref=ref,
                    expected_version=version, operation_key=key, delta_sha256='')
        if operation == 'create':
            plan.update(name=name, mime_type='text/plain', delta_sha256=hashlib.sha256(b'fixture').hexdigest())
        saved = bound_plan(json.dumps(plan), client_fingerprint(OWNER, client))
        self.redis.h[approval.ApprovalStore(OWNER, lambda: self.redis)._key(ticket)] = {
            'scope': scope, 'plan': saved, 'plan_sha256': hashlib.sha256(saved.encode()).hexdigest(),
            'state': state, 'expires': int(time.time()) + 60,
        }
        return ticket

    def create(self, ticket, client=CLIENT, name='fixture.txt', content='fixture', parent='kb:test'):
        return asyncio.run(authorized_write(
            self.write, lambda: OWNER, lambda: client, None, 'create', parent, 'create-only',
            ticket, 'text/plain', content, 'text', name))

    def test_write_and_separately_approved_cross_client_read(self):
        rec = self.create(self.ticket())
        self.assertEqual(rec['status'], 'committed')
        registry = {}
        def tool(*, name, **_):
            def add(fn):
                registry[name] = fn
                return fn
            return add
        server = SimpleNamespace(tool=tool, list_tools=AsyncMock(return_value=[]))
        asyncio.run(mount_library_catalog_tools(
            server, resolve_scope=self.read, principal=lambda: OWNER, client=lambda: self.other,
            mega_approval=self.policy))
        read = registry['library_item_read']
        ref, version = rec['ref'], rec['version']
        other_ticket = self.ticket(operation='read', client=self.other, ref=ref, version=version)
        wrong_ticket = self.ticket(operation='read', client=CLIENT, ref=ref, version=version)
        for ticket in ('', wrong_ticket):
            with self.assertRaisesRegex(Exception, 'ORIGINAL_LIBRARY_READ_UNAVAILABLE'):
                asyncio.run(read(ref, version, None, ticket))
        self.assertEqual(asyncio.run(read(ref, version, None, other_ticket))['preview'], 'fixture')
        with self.assertRaisesRegex(Exception, 'ORIGINAL_LIBRARY_READ_UNAVAILABLE'):
            asyncio.run(read(ref, version, None, other_ticket))

    def test_wrong_client_content_unsigned_and_legacy_scope_do_not_upload(self):
        for ticket, kw in [(self.ticket(), {'client': self.other}),
                           (self.ticket(), {'content': 'different'}),
                           (self.ticket(state='pending'), {}),
                           (self.ticket(scope='deploy'), {})]:
            with self.assertRaises(LibraryWriteDenied):
                self.create(ticket, **kw)
        self.assertFalse(self.mega.puts)

    def test_new_ticket_cannot_reuse_operation_key(self):
        self.create(self.ticket())
        with self.assertRaises(LibraryWriteDenied):
            self.create(self.ticket(name='second.txt'), name='second.txt')
        self.assertEqual(len(self.mega.puts), 1)

    def test_write_lands_in_test_folder_only(self):
        rec = self.create(self.ticket())
        self.assertEqual(rec['status'], 'committed')
        self.assertEqual(self.mega.puts[0]['t'], 'TestDir1')

    def test_root_or_other_parent_denied(self):
        from knowledge_approval import parse_plan
        from approval import ApprovalError
        for ref in ('kb:root', 'm:SubDir01'):
            with self.assertRaises(ApprovalError):
                parse_plan(json.dumps(dict(operation='create', collection='shared_reflection',
                                           target_ref=ref, expected_version='create-only',
                                           operation_key='k1', delta_sha256='0' * 64,
                                           name='x.txt', mime_type='text/plain')))
            with self.assertRaises(LibraryWriteDenied):
                self.create(self.ticket(), parent=ref)
        self.assertFalse(self.mega.puts)

    def test_missing_test_folder_denied(self):
        self.mega.nodes = [n for n in self.mega.nodes if n['h'] != 'TestDir1']
        with self.assertRaises(LibraryWriteDenied):
            self.create(self.ticket())
        self.assertFalse(self.mega.puts)

    def test_revoked_client_denied(self):
        ticket = self.ticket()
        self.env['XJ_MEGA_KB_APPROVAL_CLIENTS_JSON'] = '{}'
        with self.assertRaises(LibraryWriteDenied):
            self.create(ticket)
        self.assertFalse(self.mega.puts)
