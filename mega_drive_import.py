"""Copy one allowlisted Google Drive file into the MEGA 知識庫. Default off.

  XJ_MEGA_KB_DRIVE_IMPORT_ENABLED=true
      also needs the MEGA reader, the MEGA writer, and the client allowlist
  XJ_DRIVE_SA_JSON                 Sensitive service-account key (drive.readonly)
  XJ_DRIVE_ALLOWED_FOLDER_IDS      comma-separated folder ids
  XJ_MEGA_KB_DRIVE_IMPORT_MAX_BYTES
      optional, default 26214400, never above the reader's 25 MiB cap

library_item_import_from_drive creates a new file. The MEGA name must use the
same extension as the Drive file. library_item_update_from_drive overwrites one
existing file at expected_version when those extensions match, and keeps one
previous version. Text create/update is unchanged.

A download or hash failure after reserve is denied: the operation key stays
used and counts toward the daily cap, and the destination name is freed so a
new operation_key can retry. Upload and readback failures stay pending.

The request digest covers drive_file_id, headRevisionId and sha256, so the same
operation_key with different inputs is rejected. A committed replay returns the
existing receipt and does not upload again. Pending stays pending: query
library_write_status and do not retry that key.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import unicodedata

from drive_client import (
    DRIVE_ID, DriveClient, parse_allowlist, parse_cap, parse_service_account, safe_code)
from library_catalog_gate import MEGA_SOURCE
from library_write_gate import OPKEY, REF, VERSION, request_digest
from mega_library import MegaDenied, version_of
from mega_writer import ROOT_REF, ClientLedger, MegaSession, _post_bytes

CLIENT = re.compile(r'[A-Za-z0-9_-]{1,200}\Z')
SHA = re.compile(r'[0-9a-fA-F]{64}\Z')
RECEIPT_KEYS = frozenset((
    'schema', 'source', 'owner', 'client_id', 'operation', 'target_ref',
    'expected_version', 'operation_key', 'ref', 'version', 'status', 'request_digest'))


def _extension(name, code, limit, reject_dotdot):
    if type(name) is not str or not 0 < len(name) <= limit:
        raise MegaDenied(code)
    if name != unicodedata.normalize('NFC', name).strip():
        raise MegaDenied(code)
    if name in ('.', '..') or '/' in name or '\\' in name or '\x00' in name:
        raise MegaDenied(code)
    if reject_dotdot and '..' in name:
        raise MegaDenied(code)
    return os.path.splitext(name)[1].casefold()


def _receipt(status, owner, client_id, operation, target_ref, expected_version,
             operation_key, ref, version, digest):
    return {
        'schema': 'original-library-write-receipt-v1', 'source': MEGA_SOURCE,
        'owner': owner, 'client_id': client_id, 'operation': operation,
        'target_ref': target_ref, 'expected_version': expected_version,
        'operation_key': operation_key, 'ref': ref, 'version': version,
        'status': status, 'request_digest': digest}


def _digest(owner, client_id, operation, target_ref, expected_version, operation_key,
            name, drive_file_id, head_revision_id, sha256, expected_size):
    return request_digest({
        'schema': 'mega-drive-import-request-v1',
        'owner': owner, 'client_id': client_id, 'operation': operation,
        'target_ref': target_ref, 'expected_version': expected_version,
        'operation_key': operation_key, 'name': name,
        'drive_file_id': drive_file_id, 'headRevisionId': head_revision_id,
        'sha256': sha256, 'expected_size': expected_size})


def _accept(receipt, pending):
    if (type(receipt) is not dict or set(receipt) != RECEIPT_KEYS
            or receipt['schema'] != 'original-library-write-receipt-v1'
            or receipt['source'] != MEGA_SOURCE
            or receipt['status'] != 'committed'
            or receipt['request_digest'] != pending['request_digest']
            or receipt['owner'] != pending['owner']
            or receipt['client_id'] != pending['client_id']
            or receipt['operation'] != pending['operation']
            or receipt['target_ref'] != pending['target_ref']
            or receipt['expected_version'] != pending['expected_version']
            or receipt['operation_key'] != pending['operation_key']
            or REF.fullmatch(receipt['ref']) is None
            or VERSION.fullmatch(receipt['version']) is None):
        raise MegaDenied('MEGA_RECEIPT_INVALID')
    return receipt


class DriveImporter:
    def __init__(self, folder, owner_of_deployment, policy, session_factory,
                 client_factory, drive, max_bytes):
        self.folder = folder
        self.owner_of_deployment = owner_of_deployment
        self.policy = policy
        self.session_factory = session_factory
        self.client_factory = client_factory
        self.drive = drive
        self.max_bytes = max_bytes

    async def import_file(self, ctx, owner, client_id, parent_ref, name, drive_file_id,
                          expected_sha256, expected_size, operation_key):
        del ctx
        return await asyncio.to_thread(
            self._run, 'create', owner, client_id, parent_ref, name, 'create-only',
            drive_file_id, expected_sha256, expected_size, operation_key)

    async def update_file(self, ctx, owner, client_id, target_ref, expected_version,
                          drive_file_id, expected_sha256, expected_size, operation_key):
        del ctx
        return await asyncio.to_thread(
            self._run, 'update', owner, client_id, target_ref, '', expected_version,
            drive_file_id, expected_sha256, expected_size, operation_key)

    def _locate(self, operation, target_ref, name, expected_version, drive_ext):
        self.folder._tree = None
        tree = self.folder.tree()
        if operation == 'update':
            match = re.fullmatch(r'm:([A-Za-z0-9_-]{8})', target_ref)
            old = tree.get(match.group(1)) if match else None
            if old is None or old['t'] != 0:
                raise MegaDenied('MEGA_REF_NOT_IN_FOLDER')
            if os.path.splitext(old['name'])[1].casefold() != drive_ext:
                raise MegaDenied('MEGA_EXTENSION_MISMATCH')
            if version_of(old) != expected_version:
                raise MegaDenied('MEGA_VERSION_CHANGED')
            parent = tree.get(old['p'])
            if parent is None or parent['t'] != 1:
                raise MegaDenied('MEGA_PARENT_NOT_IN_FOLDER')
            return parent, old['name'], old
        if _extension(name, 'MEGA_WRITE_NAME', 160, True) != drive_ext:
            raise MegaDenied('MEGA_EXTENSION_MISMATCH')
        if target_ref == ROOT_REF:
            parent = next((node for node in tree.values() if node['p'] not in tree), None)
        else:
            match = re.fullmatch(r'm:([A-Za-z0-9_-]{8})', target_ref)
            parent = tree.get(match.group(1)) if match else None
        if parent is None or parent['t'] != 1:
            raise MegaDenied('MEGA_PARENT_NOT_IN_FOLDER')
        if any(node['p'] == parent['h'] and node['name'] == name for node in tree.values()):
            raise MegaDenied('MEGA_NAME_EXISTS')
        return parent, name, None

    def _run(self, operation, owner, client_id, target_ref, name, expected_version,
             drive_file_id, expected_sha256, expected_size, operation_key):
        stage, reserved = 'check', False
        ledger = pending = held = None
        ticket = operation_key[:8] if isinstance(operation_key, str) else '?'
        try:
            if owner != self.owner_of_deployment() or type(owner) is not str:
                raise MegaDenied('MEGA_NOT_OWNER')
            if type(client_id) is not str or CLIENT.fullmatch(client_id) is None:
                raise MegaDenied('MEGA_CLIENT_NOT_ALLOWED')
            if operation not in ('create', 'update'):
                raise MegaDenied('MEGA_WRITE_OPERATION')
            need = 'create' if operation == 'create' else 'update'
            if self.policy.member(owner, client_id, need) is None:
                raise MegaDenied('MEGA_CLIENT_NOT_ALLOWED')
            if type(operation_key) is not str or OPKEY.fullmatch(operation_key) is None:
                raise MegaDenied('MEGA_OPERATION_KEY')
            if type(target_ref) is not str or REF.fullmatch(target_ref) is None:
                raise MegaDenied('MEGA_PARENT_NOT_IN_FOLDER' if operation == 'create'
                                 else 'MEGA_REF_NOT_IN_FOLDER')
            if operation == 'create':
                _extension(name, 'MEGA_WRITE_NAME', 160, True)
                if expected_version != 'create-only':
                    raise MegaDenied('MEGA_WRITE_OPERATION')
            elif (name != '' or type(expected_version) is not str
                  or VERSION.fullmatch(expected_version) is None):
                raise MegaDenied('MEGA_VERSION_CHANGED')
            if type(expected_sha256) is not str or SHA.fullmatch(expected_sha256) is None:
                raise MegaDenied('DRIVE_SHA_INVALID')
            sha = expected_sha256.lower()
            if type(expected_size) is not int or expected_size < 1:
                raise MegaDenied('DRIVE_SIZE_INVALID')
            if expected_size > self.max_bytes:
                raise MegaDenied('DRIVE_FILE_TOO_LARGE')
            if type(drive_file_id) is not str or DRIVE_ID.fullmatch(drive_file_id) is None:
                raise MegaDenied('DRIVE_ID_INVALID')

            stage = 'metadata'
            meta = self.drive.metadata(drive_file_id)
            if meta['size'] != expected_size:
                raise MegaDenied('DRIVE_SIZE_MISMATCH')
            drive_ext = _extension(meta['name'], 'DRIVE_NAME_INVALID', 255, False)
            digest = _digest(
                owner, client_id, operation, target_ref, expected_version, operation_key,
                name if operation == 'create' else '', drive_file_id,
                meta['headRevisionId'], sha, expected_size)
            pending = _receipt('pending', owner, client_id, operation, target_ref,
                               expected_version, operation_key, '', '', digest)
            ledger = ClientLedger(owner, client_id, self.client_factory)
            existing = ledger.load(operation_key)
            if existing is not None:
                return _accept(ClientLedger.match(existing, pending), pending)

            stage = 'locate'
            parent, mega_name, old = self._locate(
                operation, target_ref, name, expected_version, drive_ext)
            stage = 'login'
            session = self.session_factory()
            if self.policy.member(owner, client_id, need) is None:
                raise MegaDenied('MEGA_CLIENT_NOT_ALLOWED')
            if self.policy.daily_creates <= 0:
                raise MegaDenied('MEGA_DAILY_LIMIT')
            stage = 'reserve'
            slot = ('upd:' + old['h']) if old is not None else parent['h']
            slot_name = '' if old is not None else mega_name
            held = (slot, slot_name)
            replay = ledger.claim(operation_key, slot, slot_name, pending,
                                  self.policy.daily_creates)
            if replay is not None:
                return _accept(replay, pending)
            reserved = True
            if self.policy.member(owner, client_id, need) is None:
                ledger.save(operation_key, dict(pending, status='denied'))
                raise MegaDenied('MEGA_CLIENT_NOT_ALLOWED')

            stage = 'download'
            data, got = self.drive.download(drive_file_id, expected_size)
            if len(data) != expected_size:
                raise MegaDenied('DRIVE_SIZE_MISMATCH')
            if got != sha or hashlib.sha256(data).hexdigest() != sha:
                raise MegaDenied('DRIVE_SHA_MISMATCH')

            stage = 'upload'
            share_handle, share_key = self.folder.share()
            node = session.upload(parent['h'], share_handle, share_key, mega_name, data,
                                  overwrite=None if old is None else old['h'])
            stage = 'readback'
            self.folder._tree = None
            tree = self.folder.tree()
            created = tree.get(node.get('h')) if isinstance(node, dict) else None
            if (created is None or created['p'] != parent['h']
                    or created['name'] != mega_name):
                raise MegaDenied('MEGA_READBACK_NOT_IN_FOLDER')
            if old is not None and old['h'] in tree:
                raise MegaDenied('MEGA_OVERWRITE_LEFT_OLD_FILE')
            size, read_sha, _head = self.folder.read(created)
            if size != len(data) or read_sha != sha:
                raise MegaDenied('MEGA_READBACK_DIGEST_MISMATCH')
            pruned = 0
            if old is not None:
                try:
                    for extra in session.version_chain(created['h'])[1:][:1]:
                        session.delete(extra)
                        pruned += 1
                except Exception:
                    print(f'xj-mega-drive ticket={ticket} stage=prune code=MEGA_PRUNE_FAILED',
                          flush=True)
            receipt = _accept(_receipt(
                'committed', owner, client_id, operation, target_ref, expected_version,
                operation_key, 'm:' + created['h'], version_of(created), digest), pending)
            stage = 'save'
            ledger.save(operation_key, receipt)
            print(f'xj-mega-drive ticket={ticket} stage=done code=COMMITTED op={operation} '
                  f'bytes={len(data)}' + (f' pruned={pruned}' if old is not None else ''),
                  flush=True)
            return receipt
        except Exception as exc:
            code = safe_code(exc)
            print(f'xj-mega-drive ticket={ticket} stage={stage} code={code}', flush=True)
            if reserved and ledger is not None and pending is not None:
                try:
                    if stage in ('upload', 'readback', 'save'):
                        ledger.save(operation_key, dict(pending, status='pending'))
                    else:
                        # The hash or download failed before any MEGA put. Keep the
                        # operation key, but free the name so a new key can retry.
                        ledger.save(operation_key, dict(pending, status='denied'))
                        if held is not None:
                            ledger.release_destination(*held)
                except Exception:
                    pass
            raise MegaDenied(code) from None


def drive_import_from_env(env, folder, owner_of_deployment, policy, post_json,
                          post_bytes=None, client_factory=None, drive_post_form=None,
                          drive_open=None):
    """None unless the flag, client allowlist, MEGA login and Drive config all parse."""
    if env.get('XJ_MEGA_KB_DRIVE_IMPORT_ENABLED') != 'true' or policy is None:
        return None
    email, password = env.get('XJ_MEGA_EMAIL', ''), env.get('XJ_MEGA_PASSWORD', '')
    if not email or not password:
        return None
    try:
        account = parse_service_account(env.get('XJ_DRIVE_SA_JSON', ''))
        allowlist = parse_allowlist(env.get('XJ_DRIVE_ALLOWED_FOLDER_IDS', ''))
        cap = parse_cap(env.get('XJ_MEGA_KB_DRIVE_IMPORT_MAX_BYTES'))
    except MegaDenied as exc:
        print(f'xj-mega-drive stage=config code={safe_code(exc)}', flush=True)
        return None
    if client_factory is None:
        from chat_room import default_client as client_factory
    kwargs = {}
    if drive_post_form is not None:
        kwargs['post_form'] = drive_post_form
    if drive_open is not None:
        kwargs['open_get'] = drive_open
    drive = DriveClient(account, allowlist, **kwargs)
    poster = post_bytes or _post_bytes

    def session_factory():
        return MegaSession(email, password, post_json, poster)

    return DriveImporter(folder, owner_of_deployment, policy, session_factory,
                         client_factory, drive, cap)
