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


def enrollment_active(config, now):
    """Only explicit owner service enrollments have no calendar expiry."""
    expiry = config.get('expires_at')
    service = (config.get('lifecycle') == 'owner-platform-service-v1'
               and config.get('authority') == 'owner-platform-service-enrollment')
    if expiry is None:
        return service and config['created_at'] <= now
    return type(expiry) in (int, float) and config['created_at'] <= now < expiry


def request_deadline(config, now, seconds):
    expiry = config.get('expires_at')
    return now + seconds if expiry is None else min(expiry, now + seconds)


def initialize(root, *, owner, ttl_seconds=3600, now=None, platform_service=False, enable_codex=False):
    """Trusted CLI only: enroll a NEW parent, not an old Work or old grant.

    Caller must obtain owner from the existing verified OAuth deployment
    configuration. It is never taken from a model's tool request.
    """
    if (not isinstance(owner, str) or len(owner) != 70 or not owner.startswith('oauth:')
            or any(c not in '0123456789abcdef' for c in owner[6:])
            or type(ttl_seconds) is not int or not 60 <= ttl_seconds <= 86400
            or type(platform_service) is not bool or type(enable_codex) is not bool
            or (enable_codex and not platform_service)):
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
                  created_at=created, expires_at=None if platform_service else created + ttl_seconds,
                  public_key=public, actions=['submit', 'read', 'cancel'],
                  automatic_dispatch=False, authority='new-owner-enrollment')
    if platform_service:
        config.update(lifecycle='owner-platform-service-v1', authority='owner-platform-service-enrollment')
    if enable_codex:
        config['actions'].append('execute')
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
                or not enrollment_active(self.config, self.clock())
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
                or not self.clock() < body['expires_at'] <= request_deadline(self.config, self.clock(), 120)
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

    def compose(self, repository, *, decomposition=None, fixed_agent=None):
        """Instantiate the ORIGINAL Holder and bind the ORIGINAL tool triple."""
        from deerflow.subagents.batch_runtime import HolderBoundBatchSubmitter
        from deerflow.tools.builtins.batch_task_tool import bind_batch_tools, BatchTaskItem
        c = self.config
        holder = HolderBoundBatchSubmitter(repository=repository, binding=self.binding('read'),
            thread_id=c['parent_id'], run_id=c['generation'], check_current=self.binding)
        if decomposition is not None or fixed_agent is not None:
            from dataclasses import replace
            from copy import deepcopy
            original_holder = holder
            frozen_plan = json.loads(canonical(decomposition))
            frozen_role = json.loads(canonical(fixed_agent))
            class PlanSubmitter:
                def __getattr__(self, name):
                    return getattr(original_holder, name)

                async def submit(self, request):
                    # The ORIGINAL Holder hashes and atomically stores this metadata
                    # together with its original child items in the same SQL batch.
                    enriched = replace(request, execution_spec={
                        **deepcopy(request.execution_spec),
                        **({'task_decomposition': deepcopy(frozen_plan)} if frozen_plan is not None else {}),
                        **({'fixed_agent_runtime': deepcopy(frozen_role)} if frozen_role is not None else {}),
                    })
                    return await original_holder.submit(enriched)
            holder = PlanSubmitter()
        def provider():
            self.check('read')
            return holder
        runtime = SimpleNamespace(context={'user_id': c['owner'], 'thread_id': c['parent_id'],
                                           'run_id': c['generation']},
                                  config={'metadata': {'allowed_subagents': ['general-purpose']},
                                          'configurable': {'thread_id': c['parent_id']}})
        return runtime, bind_batch_tools(submitter_provider=provider), BatchTaskItem

    async def execute(self, envelope, *, repository, agent_management=None, agent_runtime=None):
        from batch_mcp_entry import Submit, Status, _public_result
        from platform_pending_cancel import PendingCancel as Cancel, cancel_pending_items
        body = self.verify_request(envelope)
        action = body['action']
        if body['payload'].get('execution_mode') == 'original-deer-fixed-runtime-v1':
            from deer_fixed_runtime_host import FixedAgentRuntimeService
            from deer_agent_management import AgentManagementDenied
            if type(agent_runtime) is not FixedAgentRuntimeService:
                raise HostDenied('ORIGINAL_FIXED_RUNTIME_NOT_BOUND')
            try:
                result = await agent_runtime.execute(body, host=self, repository=repository)
            except AgentManagementDenied as error:
                result = {'kind': 'original-deer-fixed-runtime-v1',
                          'status': 'DENIED_OR_UNKNOWN_QUERY_ONLY', 'reason': str(error)}
            self.check(action)
            response = dict(generation=self.config['generation'], request_id=body['request_id'],
                            request_sha256=digest(body), result=result, automatic_dispatch=False,
                            native_execution_verified=False)
            return dict(body=response, signature=base64.b64encode(self.key.sign(canonical(response))).decode())
        if body['payload'].get('execution_mode') == 'original-deer-agent-management-v1':
            if agent_management is None:
                raise HostDenied('ORIGINAL_AGENT_MANAGEMENT_NOT_BOUND')
            from deer_management_host import ManagementDefinitionService
            from deer_agent_management import AgentManagementDenied
            from deer_management_rpc import MODE
            if type(agent_management) is not ManagementDefinitionService:
                raise HostDenied('ORIGINAL_AGENT_MANAGEMENT_CAPABILITY_REQUIRED')
            try:
                result = await agent_management.execute(body)
            except AgentManagementDenied as error:
                result = {'kind': MODE, 'status': 'DENIED_OR_UNKNOWN_QUERY_ONLY', 'reason': str(error),
                          'execution_started': False}
            self.check(action)
            response = dict(generation=self.config['generation'], request_id=body['request_id'],
                            request_sha256=digest(body), result=result, automatic_dispatch=False,
                            native_execution_verified=False)
            return dict(body=response, signature=base64.b64encode(self.key.sign(canonical(response))).decode())
        if action == 'execute':
            from platform_codex_executor import Execute, execute_item
            from platform_codex_stop import Stop, stop_item
            payload = body['payload']
            from platform_codex_batch import ExecuteBatch, execute_batch
            from platform_dag_runtime import ExecuteDag, execute_durable_dag
            model = {'stop_codex_once': Stop, 'codex_batch_once': ExecuteBatch, 'codex_dag_once': ExecuteDag}.get(
                payload.get('execution_mode'), Execute)
            request = model.model_validate(dict(payload, parent_id=body['parent_id'],
                                                  operation_key=body['operation_key']))
            self.reserve(body)
            if isinstance(request, Stop):
                result = await stop_item(self, repository, request)
            elif isinstance(request, ExecuteDag):
                from platform_codex_dag import snapshot
                initial = await snapshot(self, repository, request.batch_id)
                if initial is None or initial['graph']['schema'] != 'deer-codex-dag-v2':
                    raise HostDenied('DAG_V2_SAVED_GRAPH_REQUIRED')
                result = await execute_durable_dag(self, repository, request, initial)
            elif isinstance(request, ExecuteBatch):
                result = await execute_batch(self, repository, request)
            else:
                result = await execute_item(self, repository, request)
            self.check('execute')
            response = dict(generation=self.config['generation'], request_id=body['request_id'],
                            request_sha256=digest(body), result=result, automatic_dispatch=False,
                            native_execution_verified=False)
            return dict(body=response, signature=base64.b64encode(self.key.sign(canonical(response))).decode())
        model = {'submit': Submit, 'read': Status, 'cancel': Cancel}[action]
        request = model.model_validate(dict(body['payload'], parent_id=body['parent_id'],
                                           operation_key=body['operation_key']))
        if action == 'cancel' and request.pending_only:
            self.reserve(body)
            result = await cancel_pending_items(self, repository, request)
            self.check('cancel')
            response = dict(generation=self.config['generation'], request_id=body['request_id'],
                            request_sha256=digest(body), result=result, automatic_dispatch=False,
                            native_execution_verified=False)
            return dict(body=response, signature=base64.b64encode(self.key.sign(canonical(response))).decode())
        if action == 'submit' and (request.items is None or request.subagent_type != 'general-purpose'
                or len({i.key for i in request.items}) != len(request.items)):
            raise HostDenied('BATCH_ITEMS_OR_SUBAGENT_INVALID')
        decomposition = None
        if action == 'submit' and request.goal is not None:
            decomposition = dict(schema='deer-task-decomposition-v1', goal=request.goal,
                goal_sha256=hashlib.sha256(request.goal.encode()).hexdigest(),
                parent_id=self.config['parent_id'], planner='current_gpt_work',
                item_keys=[item.key for item in request.items], automatic_dispatch=False)
            from platform_codex_dag import parse_goal, validate_external_graph
            graph = parse_goal(request.goal, decomposition['item_keys'])
            if graph is not None:
                await validate_external_graph(self, repository, graph)
                decomposition['dag'] = graph
        runtime, tools, item_model = self.compose(repository, decomposition=decomposition)
        args = request.model_dump(exclude={'parent_id', 'operation_key'}, exclude_none=True)
        args.pop('goal', None)
        args.pop('pending_only', None)
        include_plan = args.pop('include_plan', False)
        if action == 'submit':
            args['items'] = [item_model.model_validate(item) for item in args['items']]
            args['tool_call_id'] = body['operation_key']
        if action != 'read':
            self.reserve(body)
        self.check(action)
        value = await tools[('submit', 'read', 'cancel').index(action)].coroutine(runtime=runtime, **args)
        self.check(action)
        result = _public_result(value)
        if action == 'read' and include_plan:
            # The ordinary repository projection intentionally excludes the
            # worker execution spec. Read only our plan field through original
            # ORM/session, rechecking the original owner/source binding.
            from deerflow.persistence.subagent_batches.model import SubagentBatchRow
            async with repository._sf() as session:
                row = await session.get(SubagentBatchRow, request.batch_id)
                expected = self.binding('read')
                if row is None or row.user_id != self.config['owner']:
                    raise HostDenied('DECOMPOSITION_BATCH_NOT_FOUND')
                binding = row.execution_spec.get('native_control', {}).get('binding', {})
                if {k: binding.get(k) for k in expected} != expected:
                    raise HostDenied('DECOMPOSITION_SOURCE_MISMATCH')
                repository._native_control(row, user_id=self.config['owner'], binding=binding)
                saved_plan = json.loads(canonical(row.execution_spec.get('task_decomposition')))
                public_payload_digest = binding['payloadDigest']
                dag_runtime = json.loads(canonical(row.execution_spec.get('dag_runtime_v2')))
            if saved_plan is not None:
                if (saved_plan.get('parent_id') != self.config['parent_id']
                        or hashlib.sha256(saved_plan['goal'].encode()).hexdigest() != saved_plan['goal_sha256']):
                    raise HostDenied('DECOMPOSITION_SNAPSHOT_INVALID')
                children = await repository.list_items(request.batch_id, user_id=self.config['owner'],
                    offset=request.item_offset or 0, limit=request.item_limit, status=request.item_status,
                    include_prompt=True, include_result=False, native_binding=self.binding('read'))
                result['decomposition'] = dict(saved_plan, batch_id=request.batch_id,
                    native_payload_digest=public_payload_digest, dag_runtime=dag_runtime,
                    children=children, offset=request.item_offset or 0, limit=request.item_limit)
            else:
                result['decomposition'] = None
            self.check('read')
        if decomposition is not None:
            result['decomposition'] = decomposition
        if len(canonical(result)) > 65536:
            raise HostDenied('DECOMPOSITION_RESPONSE_TOO_LARGE')
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
    parser.add_argument('--platform-service', action='store_true')
    parser.add_argument('--enable-codex', action='store_true')
    args = parser.parse_args()
    if args.action == 'initialize':
        result = initialize(args.root, owner=args.owner, ttl_seconds=args.ttl_seconds,
                            platform_service=args.platform_service, enable_codex=args.enable_codex)
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


