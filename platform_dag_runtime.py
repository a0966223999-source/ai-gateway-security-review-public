"""Resume admitted DAG intents; never replay an item with a native send fence.

Only the original private ingress attempt can admit an intent. A worker restart
reconciles it against original SQL; arbitrary pending tasks are never dispatched.
"""
import asyncio
import base64
import fcntl
import hashlib
import json
import os
from typing import Annotated, Literal

from pydantic import Field
from batch_mcp_entry import RequestBase, Text
from fresh_batch_host import HostDenied, canonical, digest, private_read


class ExecuteDag(RequestBase):
    execution_mode: Literal['codex_dag_once']
    batch_id: Text
    timeout_seconds: Annotated[int, Field(strict=True, ge=10, le=90)] = 90


def original_body(host, request_id):
    if (not isinstance(request_id, str) or len(request_id) != 64
            or any(c not in '0123456789abcdef' for c in request_id)):
        raise HostDenied('DAG_ORIGINAL_REQUEST_ID_INVALID')
    body = json.loads(private_read(host.root / 'attempts' / request_id))
    if (body.get('request_id') != request_id or body.get('action') != 'execute'
            or request_id != digest([body.get('generation'), body.get('owner'),
                body.get('action'), body.get('operation_key')])
            or any(body.get(k) != host.config[k] for k in ('owner', 'generation', 'parent_id'))
            or body.get('payload', {}).get('execution_mode') not in ('codex_dag_once', 'codex_batch_once')):
        raise HostDenied('DAG_ORIGINAL_REQUEST_NOT_BOUND')
    return body


def scope_for(host, request, initial):
    request_id = digest([host.config['generation'], host.config['owner'], 'execute', request.operation_key])
    body = original_body(host, request_id)
    payload = request.model_dump(exclude={'parent_id', 'operation_key'})
    # Validate through the same schema to include default timeout, not byte guesses.
    saved = type(request).model_validate(dict(body['payload'], parent_id=body['parent_id'],
        operation_key=body['operation_key']))
    if saved != request:
        raise HostDenied('DAG_ORIGINAL_PAYLOAD_CHANGED')
    expiry = body['expires_at']
    if type(expiry) not in (int, float):
        raise HostDenied('DAG_ORIGINAL_DEADLINE_INVALID')
    return dict(request_id=request_id, request_sha256=digest(body), binding=initial['binding'],
        graph_sha256=digest(initial['graph']), payload=payload,
        # Fixed bound, never renewed on restart: <=100*90 sec plus admission margin.
        deadline=expiry + 9000)


async def save_runtime(host, repository, request, scope, *, phase, outcome=None):
    from deerflow.persistence.subagent_batches.model import SubagentBatchRow
    host.check('execute')
    async with repository._sf() as session:
        await repository._lock_native_writer(session, request.batch_id, host.config['owner'])
        row = await session.get(SubagentBatchRow, request.batch_id, populate_existing=True)
        if row is None or row.user_id != host.config['owner']:
            raise HostDenied('DAG_BATCH_NOT_BOUND')
        repository._native_control(row, user_id=host.config['owner'], binding=scope['binding'])
        graph = row.execution_spec.get('task_decomposition', {}).get('dag')
        if digest(graph) != scope['graph_sha256']:
            raise HostDenied('DAG_GRAPH_CHANGED')
        previous = row.execution_spec.get('dag_runtime_v2')
        if previous is not None and previous.get('scope') != scope:
            raise HostDenied('DAG_INTENT_CHANGED_QUERY_ONLY')
        if previous is not None and previous.get('phase') != 'active':
            raise HostDenied('DAG_TERMINAL_INTENT_QUERY_ONLY')
        host.check('execute')
        row.execution_spec = {**row.execution_spec, 'dag_runtime_v2':
            dict(schema='original-dag-intent-v2', scope=scope, phase=phase, outcome=outcome)}
        await session.commit()


def ensure_live(host, scope):
    host.check('execute')
    if host.clock() >= scope['deadline']:
        raise HostDenied('DAG_DEADLINE_EXHAUSTED_QUERY_ONLY')


async def wait_ready(host, repository, batch_id, key, scope):
    from platform_codex_dag import snapshot, external_node, verified_result
    from platform_codex_stop import registry
    ensure_live(host, scope)
    view = await snapshot(host, repository, batch_id)
    waiting = False
    for parent in view['graph']['dependencies'][key]:
        if isinstance(parent, str):
            node, parent_batch = view['nodes'][parent], batch_id
        else:
            node = await external_node(host, repository, parent)
            parent_batch = parent['batch_id']
        if node['status'] == 'succeeded':
            verified_result(node)
        elif (node['status'] == 'pending' and node['attempt'] == 0 and not node['cancelled']
                and node['receipt'] is None):
            waiting = True
        elif (parent_batch, node['id']) in registry(host):
            waiting = True
        else:
            raise HostDenied('DAG_DEPENDENCY_UNKNOWN_QUERY_ONLY')
    # Yield the worker slot; a persistent intent, not an idle coroutine, waits.
    return not waiting and getattr(host, '_codex_execution_count', 0) < 2


async def execute_durable_dag(host, repository, request, initial):
    from platform_codex_dag import snapshot, verified_result, SCHEMA_V2
    from platform_codex_batch import ExecuteBatch, child_key, validate_pending
    from platform_codex_executor import Execute, execute_item
    if initial['graph']['schema'] != SCHEMA_V2 or not isinstance(request, (ExecuteDag, ExecuteBatch)):
        raise HostDenied('DAG_V2_EXPLICIT_MODE_REQUIRED')
    if isinstance(request, ExecuteBatch) and set(request.item_ids) != {n['id'] for n in initial['nodes'].values()}:
        raise HostDenied('DAG_FULL_NODE_SET_REQUIRED')
    scope = scope_for(host, request, initial)
    identity = hashlib.sha256(request.batch_id.encode()).hexdigest()
    fd = os.open(host.root / ('codex-batch-' + identity + '.lock'),
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise HostDenied('DAG_COORDINATOR_BUSY_QUERY_ONLY') from None
        initial = await snapshot(host, repository, request.batch_id, allow_stopped=True)
        runtime = initial['runtime']
        if runtime is None:
            ensure_live(host, scope)
            await validate_pending(host, repository, request, [n['id'] for n in initial['nodes'].values()])
            await save_runtime(host, repository, request, scope, phase='active')
        else:
            if runtime.get('scope') != scope:
                raise HostDenied('DAG_INTENT_CHANGED_QUERY_ONLY')
            if runtime.get('phase') in ('finished', 'halted'):
                return runtime['outcome']
            if runtime.get('phase') != 'active':
                raise HostDenied('DAG_INTENT_INVALID')
        children, reason = [], None
        try:
            for key in initial['graph']['order']:
                ensure_live(host, scope)
                view = await snapshot(host, repository, request.batch_id)
                node = view['nodes'][key]
                if node['status'] == 'succeeded':
                    result = verified_result(node)
                    children.append(dict(key=key, item_id=node['id'],
                        result_sha256=result['result_sha256'], reused_saved_result=True))
                    continue
                if not node['pristine']:
                    raise HostDenied('DAG_NODE_UNCERTAIN_QUERY_ONLY')
                if not await wait_ready(host, repository, request.batch_id, key, scope):
                    return dict(status='CODEX_DAG_WAITING_DEPENDENCIES', batch_id=request.batch_id,
                        coordinator='original-worker-durable-dag-v2')
                ensure_live(host, scope)
                remaining = int(scope['deadline'] - host.clock())
                if remaining < 10:
                    raise HostDenied('DAG_DEADLINE_EXHAUSTED_QUERY_ONLY')
                child = Execute(parent_id=request.parent_id, operation_key=child_key(request, node['id']),
                    execution_mode='codex_once', batch_id=request.batch_id, item_id=node['id'],
                    timeout_seconds=min(request.timeout_seconds, remaining))
                response = await execute_item(host, repository, child)
                if (response.get('original_result_saved') is not True
                        or response.get('observed_execution_verified') is not True):
                    raise HostDenied('DAG_CHILD_NOT_SUCCESSFUL_QUERY_ONLY')
                result = verified_result((await snapshot(host, repository, request.batch_id))['nodes'][key])
                children.append(dict(key=key, item_id=node['id'],
                    result_sha256=result['result_sha256'], reused_saved_result=False))
                await save_runtime(host, repository, request, scope, phase='active')
        except asyncio.CancelledError:
            # Leave admitted intent active, retaining every native attempt fence.
            raise
        except Exception as error:
            reason = str(error) if isinstance(error, HostDenied) else 'DAG_CHILD_UNKNOWN_QUERY_ONLY'
        final = await snapshot(host, repository, request.batch_id, allow_stopped=True)
        verified, pristine = set(), set()
        for node in final['nodes'].values():
            if node['pristine']:
                pristine.add(node['id'])
            else:
                try:
                    verified_result(node)
                    verified.add(node['id'])
                except HostDenied:
                    pass  # Explicitly classified below, never silently accepted.
        all_ids = {n['id'] for n in final['nodes'].values()}
        if reason is None and verified != all_ids:
            reason = 'DAG_FINAL_RESULT_NOT_VERIFIED_QUERY_ONLY'
        outcome = dict(status='CODEX_DAG_FINISHED' if reason is None else 'CODEX_DAG_HALTED_QUERY_ONLY',
            batch_id=request.batch_id, children=children, reason=reason,
            unstarted_item_ids=sorted(pristine), unverified_item_ids=sorted(all_ids-pristine-verified),
            coordinator='original-worker-durable-dag-v2', host_dag_enforced=True,
            automatic_dispatch=False, native_work_dispatch=False, retry_performed=False)
        await save_runtime(host, repository, request, scope,
            phase='finished' if reason is None else 'halted', outcome=outcome)
        return outcome
    finally:
        os.close(fd)


async def recoverable_bodies(host, repository):
    """Discover only already-admitted intents in this original owner's SQL rows."""
    from sqlalchemy import select
    from deerflow.persistence.subagent_batches.model import SubagentBatchRow
    host.check('execute')
    bodies = []
    async with repository._sf() as session:
        rows = (await session.execute(select(SubagentBatchRow).where(
            SubagentBatchRow.user_id == host.config['owner']))).scalars()
        for row in rows:
            runtime = row.execution_spec.get('dag_runtime_v2')
            if not isinstance(runtime, dict) or runtime.get('schema') != 'original-dag-intent-v2':
                continue
            try:
                scope = runtime['scope']
                expected = host.binding('execute')
                if {k: scope['binding'].get(k) for k in expected} != expected:
                    continue
                body = original_body(host, scope['request_id'])
                if digest(body) != scope['request_sha256'] or body['payload']['batch_id'] != row.id:
                    continue
                if runtime.get('phase') == 'active' or (runtime.get('phase') in ('finished', 'halted')
                        and not (host.root / ('dag-response-' + body['request_id'])).exists()):
                    bodies.append(body)
            except (HostDenied, KeyError, ValueError, OSError, TypeError):
                # Invalid/changed old intents get no dispatch capability.
                continue
    return bodies


async def recover_response(host, repository, body):
    from platform_codex_dag import snapshot
    host.check('execute')
    if original_body(host, body['request_id']) != body:
        raise HostDenied('DAG_ORIGINAL_BODY_CHANGED')
    from platform_codex_batch import ExecuteBatch
    model = ExecuteDag if body['payload']['execution_mode'] == 'codex_dag_once' else ExecuteBatch
    request = model.model_validate(dict(body['payload'], parent_id=body['parent_id'],
        operation_key=body['operation_key']))
    initial = await snapshot(host, repository, request.batch_id, allow_stopped=True)
    result = await execute_durable_dag(host, repository, request, initial)
    host.check('execute')
    response = dict(generation=host.config['generation'], request_id=body['request_id'],
        request_sha256=digest(body), result=result, automatic_dispatch=False, native_execution_verified=False)
    return dict(body=response, signature=base64.b64encode(host.key.sign(canonical(response))).decode())
