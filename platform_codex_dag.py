"""Explicit, bounded DAG on the original worker, Holder SQL and native fences.

The signed original goal carries the graph; JSON supplies task data, not authority.
One call advances at most three saved nodes. Crash/UNKNOWN never replays a send.
"""
import asyncio
import fcntl
import hashlib
import json
import os
import time

from fresh_batch_host import HostDenied, canonical, exclusive_write

SCHEMA = 'deer-codex-dag-v1'


def parse_goal(goal, keys):
    """Validate an opt-in graph before the original Holder stores any item."""
    if not isinstance(goal, str) or not goal.lstrip().startswith('{'):
        return None
    try:
        value = json.loads(goal)
    except ValueError:
        return None
    if not isinstance(value, dict) or value.get('schema') != SCHEMA:
        return None
    if (set(value) != {'schema', 'goal', 'dependencies'}
            or not isinstance(value['goal'], str) or not value['goal'].strip()
            or not isinstance(value['dependencies'], dict)
            or not 1 <= len(keys) <= 3 or len(set(keys)) != len(keys)
            or set(value['dependencies']) != set(keys)):
        raise HostDenied('DAG_SCHEMA_OR_NODE_SET_INVALID')
    deps = value['dependencies']
    for key, parents in deps.items():
        if (not isinstance(parents, list) or any(not isinstance(p, str) for p in parents)
                or len(parents) != len(set(parents)) or key in parents
                or any(p not in deps for p in parents)):
            raise HostDenied('DAG_DEPENDENCY_INVALID')
    order, done = [], set()
    while len(done) < len(keys):
        ready = sorted(k for k in keys if k not in done and set(deps[k]) <= done)
        if not ready:
            raise HostDenied('DAG_CYCLE')
        order.extend(ready)
        done.update(ready)
    return dict(schema=SCHEMA, dependencies=deps, order=order)


async def snapshot(host, repository, batch_id):
    from sqlalchemy import select
    from deerflow.persistence.subagent_batches.model import SubagentBatchRow, SubagentBatchItemRow
    host.check('execute')
    binding, owner = host.binding('execute'), host.config['owner']
    async with repository._sf() as session:
        row = await session.get(SubagentBatchRow, batch_id)
        if row is None or row.user_id != owner:
            raise HostDenied('DAG_BATCH_NOT_BOUND')
        saved = row.execution_spec.get('native_control', {}).get('binding', {})
        if {k: saved.get(k) for k in binding} != binding:
            raise HostDenied('DAG_SOURCE_CHANGED')
        control = repository._native_control(row, user_id=owner, binding=saved)
        plan = row.execution_spec.get('task_decomposition')
        if not isinstance(plan, dict) or 'dag' not in plan:
            return None
        if row.execution_spec.get('fixed_agent_runtime') is not None:
            raise HostDenied('DAG_FIXED_ROLE_NOT_SUPPORTED')
        if hashlib.sha256(plan['goal'].encode()).hexdigest() != plan['goal_sha256']:
            raise HostDenied('DAG_PLAN_CHANGED')
        graph = parse_goal(plan['goal'], plan['item_keys'])
        if graph is None or graph != plan['dag']:
            raise HostDenied('DAG_GRAPH_CHANGED')
        rows = list((await session.execute(select(SubagentBatchItemRow).where(
            SubagentBatchItemRow.batch_id == batch_id).order_by(SubagentBatchItemRow.position))).scalars())
        if [item.item_key for item in rows] != plan['item_keys']:
            raise HostDenied('DAG_ITEM_SET_CHANGED')
        # Original Holder preserves submission position and immutable item keys.
        nodes = {key: dict(id=item.id, status=item.status, attempt=item.attempt,
            result=item.result, cancelled=item.cancel_requested_at is not None,
            receipt=control['items'].get(item.id))
            for key, item in zip(plan['item_keys'], rows, strict=True)}
        if row.status in ('cancelled', 'failed', 'paused'):
            raise HostDenied('DAG_BATCH_STOPPED')
        host.check('execute')
        return dict(graph=graph, nodes=nodes, batch_id=batch_id)


def verified_result(node):
    receipt = node['receipt']
    if (node['status'] != 'succeeded' or node['attempt'] != 1 or node['cancelled']
            or not isinstance(node['result'], str) or not node['result'].strip()
            or not isinstance(receipt, dict) or receipt.get('phase') != 'acknowledged'
            or not isinstance(receipt.get('ackEvidenceSHA256'), str)
            or len(receipt['ackEvidenceSHA256']) != 64
            or not isinstance(receipt.get('childCanonical'), str)
            or not receipt['childCanonical'].startswith('codex-thread:')):
        raise HostDenied('DAG_DEPENDENCY_NOT_VERIFIED_QUERY_ONLY')
    return dict(item_id=node['id'], result=node['result'],
        result_sha256=hashlib.sha256(node['result'].encode()).hexdigest(),
        child_canonical=receipt['childCanonical'], ack_sha256=receipt['ackEvidenceSHA256'])


async def dependency_inputs(host, repository, batch_id, item_id):
    """Guard every single/batch path; pending dependencies may never be bypassed."""
    view = await snapshot(host, repository, batch_id)
    if view is None:
        return None
    keys = [k for k, n in view['nodes'].items() if n['id'] == item_id]
    if len(keys) != 1:
        raise HostDenied('DAG_ITEM_NOT_BOUND')
    key = keys[0]
    return {parent: verified_result(view['nodes'][parent])
            for parent in view['graph']['dependencies'][key]}


async def execute_dag(host, repository, request, initial):
    from platform_codex_executor import Execute, execute_item
    from platform_codex_batch import child_key, validate_pending
    if set(request.item_ids) != {node['id'] for node in initial['nodes'].values()}:
        raise HostDenied('DAG_FULL_NODE_SET_REQUIRED')
    identity = hashlib.sha256(request.batch_id.encode()).hexdigest()
    fd = os.open(host.root / ('codex-batch-' + identity + '.lock'),
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise HostDenied('DAG_COORDINATOR_BUSY_QUERY_ONLY') from None
        await validate_pending(host, repository, request, request.item_ids)
        audit = host.root / ('codex-dag-run-' + hashlib.sha256(canonical(
            [request.parent_id, request.operation_key])).hexdigest())
        audit.mkdir(mode=0o700)
        exclusive_write(audit / 'plan.json', canonical(dict(request=request.model_dump(), graph=initial['graph'])))
        deadline, completed, reason, attempted = time.monotonic() + 300, [], None, set()
        for key in initial['graph']['order']:
            item_id = initial['nodes'][key]['id']
            try:
                host.check('execute')
                view = await snapshot(host, repository, request.batch_id)
                if view is None or view['graph'] != initial['graph']:
                    raise HostDenied('DAG_GRAPH_CHANGED')
                inputs = await dependency_inputs(host, repository, request.batch_id, item_id)
                await validate_pending(host, repository, request, [item_id])
                remaining = int(deadline - time.monotonic())
                if remaining < 10:
                    raise HostDenied('DAG_TIME_BUDGET_EXHAUSTED')
                child = Execute(parent_id=request.parent_id, operation_key=child_key(request, item_id),
                    execution_mode='codex_once', batch_id=request.batch_id, item_id=item_id,
                    timeout_seconds=min(request.timeout_seconds, remaining))
                attempted.add(item_id)
                result = await execute_item(host, repository, child)
                completed.append(dict(key=key, item_id=item_id, operation_key=child.operation_key,
                    dependency_results=inputs, result=result))
                exclusive_write(audit / ('child-' + str(len(completed)) + '.json'), canonical(completed[-1]))
                if (result.get('original_result_saved') is not True
                        or result.get('observed_execution_verified') is not True):
                    raise HostDenied('DAG_CHILD_NOT_SUCCESSFUL_QUERY_ONLY')
                verified_result((await snapshot(host, repository, request.batch_id))['nodes'][key])
            except asyncio.CancelledError:
                raise
            except Exception as error:
                reason = str(error) if isinstance(error, HostDenied) else 'DAG_CHILD_UNKNOWN_QUERY_ONLY'
                break
        outcome = dict(status='CODEX_DAG_FINISHED' if reason is None else 'CODEX_DAG_HALTED_QUERY_ONLY',
            batch_id=request.batch_id, children=completed, reason=reason,
            unstarted_item_ids=[n['id'] for n in initial['nodes'].values()
                if n['id'] not in attempted],
            unverified_item_ids=sorted(attempted - {c['item_id'] for c in completed}),
            coordinator='original-worker-bounded-dag-v1', host_dag_enforced=True,
            automatic_dispatch=False, native_work_dispatch=False, retry_performed=False)
        exclusive_write(audit / 'receipt.json', canonical(outcome))
        return outcome
    finally:
        os.close(fd)
