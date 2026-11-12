"""Fresh, explicitly enrolled batch host. Never imports legacy Source authority.

Only new, owner-scoped tasks belong to this generation. The existing Deer
Holder/repository/bound tools remain responsible for batch state and effects.
No native task is dispatched by this host.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import stat
import time
from types import SimpleNamespace

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


class HostDenied(ValueError):
    pass


def private_read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_size > 65536):
            raise HostDenied('PRIVATE_HOST_FILE_REQUIRED')
        return stream.read(65537)


def exclusive_write(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def initialize(root, *, owner, ttl_seconds=3600, now=None):
    """Trusted CLI only: enroll a NEW parent, not an old Work or old grant.

    Caller must obtain owner from the existing verified OAuth deployment
    configuration. It is never taken from a model's tool request.
    """
    if (not isinstance(owner, str) or len(owner) != 70 or not owner.startswith('oauth:')
            or any(c not in '0123456789abcdef' for c in owner[6:])
            or type(ttl_seconds) is not int or not 60 <= ttl_seconds <= 86400):
        raise HostDenied('FRESH_ENROLLMENT_SCOPE_REQUIRED')
    root = Path(root)
    root.parent.resolve(strict=True)
    # A failed initialization is deliberately not auto-repaired or overwritten.
    root.mkdir(mode=0o700)
    (root / 'attempts').mkdir(mode=0o700)
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    created = time.time() if now is None else now
    config = dict(schema='fresh-deer-batch-host-v1', generation=secrets.token_hex(16),
                  owner=owner, parent_id='deer-parent-' + secrets.token_hex(16),
                  created_at=created, expires_at=created + ttl_seconds,
                  public_key=public, actions=['submit', 'read', 'cancel'],
                  automatic_dispatch=False, authority='new-owner-enrollment')
    exclusive_write(root / 'signing.key', key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()))
    exclusive_write(root / 'ingress.key', secrets.token_bytes(32))
    exclusive_write(root / 'host.json', canonical(config))
    exclusive_write(root / 'config.sha256', digest(config).encode())
    return config


class FreshBatchHost:
    def __init__(self, root, *, clock=time.time):
        self.root = Path(root).resolve(strict=True)
        info = self.root.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise HostDenied('PRIVATE_HOST_DIRECTORY_REQUIRED')
        self.directory_pin = (info.st_dev, info.st_ino)
        self.clock = clock
        self.config = json.loads(private_read(self.root / 'host.json'))
        self.pin = private_read(self.root / 'config.sha256').decode()
        self.key = Ed25519PrivateKey.from_private_bytes(private_read(self.root / 'signing.key'))
        self.ingress = private_read(self.root / 'ingress.key')
        if self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex() != self.config['public_key']:
            raise HostDenied('SIGNER_CONFIG_MISMATCH')
        self.check('read')

    def check(self, action):
        info = self.root.lstat()
        if ((info.st_dev, info.st_ino) != self.directory_pin or info.st_mode & 0o077
                or (self.root / 'STOP').exists() or (self.root / 'STOP').is_symlink()
                or digest(json.loads(private_read(self.root / 'host.json'))) != self.pin
                or private_read(self.root / 'config.sha256').decode() != self.pin
                or not self.config['created_at'] <= self.clock() < self.config['expires_at']
                or action not in self.config['actions']):
            raise HostDenied('FRESH_HOST_REVOKED_EXPIRED_OR_CHANGED')
        return True

    @contextmanager
    def single_worker(self):
        """Release by closing the descriptor. Never remove the live lock file."""
        fd = os.open(self.root / 'worker.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise HostDenied('PRIVATE_WORKER_LOCK_REQUIRED')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.check('read')
            yield
        finally:
            os.close(fd)

    def verify_request(self, envelope):
        if not isinstance(envelope, dict) or set(envelope) != {'body', 'mac'}:
            raise HostDenied('SIGNED_INGRESS_REQUIRED')
        body = envelope['body']
        if (len(canonical(envelope)) > 65536 or not isinstance(envelope['mac'], str)
                or not hmac.compare_digest(envelope['mac'], hmac.new(self.ingress, canonical(body), 'sha256').hexdigest())):
            raise HostDenied('INGRESS_SIGNATURE_INVALID')
        if set(body) != {'generation', 'owner', 'parent_id', 'action', 'operation_key', 'payload', 'request_id', 'expires_at'}:
            raise HostDenied('INGRESS_SCHEMA_INVALID')
        action = body['action']
        self.check(action)
        if (any(body[k] != self.config[k] for k in ('generation', 'owner', 'parent_id'))
                or type(body['expires_at']) not in (int, float)
                or not self.clock() < body['expires_at'] <= min(self.config['expires_at'], self.clock() + 120)
                or not isinstance(body['operation_key'], str) or not 1 <= len(body['operation_key']) <= 128
                or body['request_id'] != digest([body['generation'], body['owner'], action, body['operation_key']])
                or not isinstance(body['payload'], dict)):
            raise HostDenied('INGRESS_SCOPE_INVALID')
        return body

    def reserve(self, body):
        """Persist the attempt before any original tool call. Never replay."""
        request_id = body['request_id']
        if len(request_id) != 64 or any(c not in '0123456789abcdef' for c in request_id):
            raise HostDenied('REQUEST_ID_INVALID')
        self.check(body['action'])
        try:
            exclusive_write(self.root / 'attempts' / request_id, canonical(body))
        except FileExistsError:
            raise HostDenied('ATTEMPTED_QUERY_ONLY') from None
        self.check(body['action'])

    def binding(self, action):
        self.check(action)
        c = self.config
        return dict(taskRef=c['parent_id'], version='1', operationRef=c['generation'],
                    ownerRef=c['owner'], sourceRevision='1', sourceDigest=self.pin)

    def compose(self, repository):
        """Instantiate the ORIGINAL Holder and bind the ORIGINAL tool triple."""
        from deerflow.subagents.batch_runtime import HolderBoundBatchSubmitter
        from deerflow.tools.builtins.batch_task_tool import bind_batch_tools, BatchTaskItem
        c = self.config
        holder = HolderBoundBatchSubmitter(repository=repository, binding=self.binding('read'),
            thread_id=c['parent_id'], run_id=c['generation'], check_current=self.binding)
        def provider():
            self.check('read')
            return holder
        runtime = SimpleNamespace(context={'user_id': c['owner'], 'thread_id': c['parent_id'],
                                           'run_id': c['generation']},
                                  config={'metadata': {'allowed_subagents': ['general-purpose']},
                                          'configurable': {'thread_id': c['parent_id']}})
        return runtime, bind_batch_tools(submitter_provider=provider), BatchTaskItem

    async def execute(self, envelope, *, repository):
        from batch_mcp_entry import Submit, Status, Cancel, _public_result
        body = self.verify_request(envelope)
        action = body['action']
        model = {'submit': Submit, 'read': Status, 'cancel': Cancel}[action]
        request = model.model_validate(dict(body['payload'], parent_id=body['parent_id'],
                                           operation_key=body['operation_key']))
        if action == 'submit' and (request.subagent_type != 'general-purpose'
                or len({i.key for i in request.items}) != len(request.items)):
            raise HostDenied('BATCH_ITEMS_OR_SUBAGENT_INVALID')
        runtime, tools, item_model = self.compose(repository)
        args = request.model_dump(exclude={'parent_id', 'operation_key'}, exclude_none=True)
        if action == 'submit':
            args['items'] = [item_model.model_validate(item) for item in args['items']]
            args['tool_call_id'] = body['operation_key']
        if action != 'read':
            self.reserve(body)
        self.check(action)
        value = await tools[('submit', 'read', 'cancel').index(action)].coroutine(runtime=runtime, **args)
        self.check(action)
        result = _public_result(value)
        response = {'generation': self.config['generation'], 'request_id': body['request_id'],
                    'request_sha256': digest(body), 'result': result,
                    'automatic_dispatch': False, 'native_execution_verified': False}
        return {'body': response, 'signature': base64.b64encode(self.key.sign(canonical(response))).decode()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['initialize', 'status', 'stop'])
    parser.add_argument('--root', required=True)
    parser.add_argument('--owner')
    parser.add_argument('--ttl-seconds', type=int, default=3600)
    args = parser.parse_args()
    if args.action == 'initialize':
        result = initialize(args.root, owner=args.owner, ttl_seconds=args.ttl_seconds)
    elif args.action == 'stop':
        root = Path(args.root).resolve(strict=True)
        try:
            exclusive_write(root / 'STOP', b'user-stop\n')
        except FileExistsError:
            pass
        result = {'stopped': True}
    else:
        result = {'active': FreshBatchHost(args.root).check('read')}
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
