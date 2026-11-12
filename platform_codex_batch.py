"""Bounded opt-in coordination on the original worker and native SQL fences.

No queue scanning, restart replay, dependency scheduling or native Work routing.
The original worker owns this coroutine after the submitting MCP call returns.
"""
import asyncio
import fcntl
import hashlib
import os
import time
from typing import Annotated, Literal

from pydantic import Field, model_validator
from batch_mcp_entry import RequestBase, Text
from fresh_batch_host import HostDenied, canonical, exclusive_write
from platform_codex_executor import Execute, execute_item


class ExecuteBatch(RequestBase):
    execution_mode: Literal['codex_batch_once']
    batch_id: Text
    item_ids: Annotated[list[Text], Field(min_length=1, max_length=3)]
    timeout_seconds: Annotated[int, Field(strict=True, ge=10, le=90)] = 90

    @model_validator(mode='after')
    def unique_items(self):
        if len(set(self.item_ids)) != len(self.item_ids):
            raise ValueError('DUPLICATE_EXECUTION_ITEMS')
        return self


def child_key(request, item_id):
    return 'codex-batch:' + hashlib.sha256(canonical(
        [request.operation_key, request.batch_id, item_id])).hexdigest()


async def validate_pending(host, repository, request, item_ids):
    """Check original owner/full binding and all selected rows before effects.

    This read does not reserve anything. The original executor revalidates and
    acquires the durable native writer fence separately immediately before spawn.
    """
    from deerflow.persistence.subagent_batches.model import SubagentBatchRow, SubagentBatchItemRow
    owner, binding = host.config['owner'], host.binding('execute')
    async with repository._sf() as session:
        row = await session.get(SubagentBatchRow, request.batch_id)
        host.check('execute')
        if row is None or row.user_id != owner:
            raise HostDenied('CODEX_BATCH_NOT_BOUND')
        saved = row.execution_spec.get('native_control', {}).get('binding', {})
        if {key: saved.get(key) for key in binding} != binding:
            raise HostDenied('CODEX_SOURCE_CHANGED')
        control = repository._native_control(row, user_id=owner, binding=saved)
        for item_id in item_ids:
            item = await session.get(SubagentBatchItemRow, item_id)
            if (item is None or item.batch_id != request.batch_id or item.status != 'pending'
                    or item.attempt != 0 or item.lease_owner is not None
                    or item.lease_expires_at is not None or item.started_at is not None
                    or item.cancel_requested_at is not None or item_id in control['items']):
                raise HostDenied('CODEX_BATCH_UNTOUCHED_PENDING_ITEMS_REQUIRED')
        host.check('execute')


async def execute_batch(host, repository, request):
    host.check('execute')
    # Same original private host directory, audit/lock only, not a task database.
    identity = hashlib.sha256(request.batch_id.encode()).hexdigest()
    fd = os.open(host.root / ('codex-batch-' + identity + '.lock'),
                 os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise HostDenied('CODEX_BATCH_COORDINATOR_BUSY_QUERY_ONLY') from None
        await validate_pending(host, repository, request, request.item_ids)
        audit_id = hashlib.sha256(canonical([request.parent_id, request.operation_key])).hexdigest()
        audit = host.root / ('codex-batch-run-' + audit_id)
        audit.mkdir(mode=0o700)
        exclusive_write(audit / 'plan.json', canonical(request.model_dump()))
        completed = []
        # Max three 90-second children, sequential; cleanup stays owned by executor.
        deadline = time.monotonic() + 300
        reason = None
        for index, item_id in enumerate(request.item_ids):
            key = child_key(request, item_id)
            try:
                host.check('execute')
                await validate_pending(host, repository, request, [item_id])
                remaining = int(deadline - time.monotonic())
                if remaining < 10:
                    raise HostDenied('CODEX_BATCH_TIME_BUDGET_EXHAUSTED_QUERY_ONLY')
                child = Execute(parent_id=request.parent_id, operation_key=key,
                    execution_mode='codex_once', batch_id=request.batch_id, item_id=item_id,
                    timeout_seconds=min(request.timeout_seconds, remaining))
                result = await execute_item(host, repository, child)
                completed.append(dict(item_id=item_id, operation_key=key, result=result))
                exclusive_write(audit / f'child-{index}.json', canonical(completed[-1]))
                if (result.get('original_result_saved') is not True
                        or result.get('observed_execution_verified') is not True):
                    reason = 'CODEX_BATCH_CHILD_NOT_SUCCESSFUL_QUERY_ONLY'
                    break
            except asyncio.CancelledError:
                # Original executor finishes owned-process cleanup before returning.
                # Retain attempt/plan; restart never replays this coordinator.
                raise
            except Exception as error:
                reason = str(error) if isinstance(error, HostDenied) else 'CODEX_BATCH_CHILD_UNKNOWN_QUERY_ONLY'
                completed.append(dict(item_id=item_id, operation_key=key, status='UNKNOWN_QUERY_ONLY'))
                break
        outcome = dict(status='CODEX_BATCH_FINISHED' if reason is None else 'CODEX_BATCH_HALTED_QUERY_ONLY',
            batch_id=request.batch_id, children=completed,
            unstarted_item_ids=request.item_ids[len(completed):], reason=reason,
            coordinator='original-worker-bounded-sequential-v1', automatic_dispatch=False,
            native_work_dispatch=False, retry_performed=False)
        exclusive_write(audit / 'receipt.json', canonical(outcome))
        return outcome
    finally:
        os.close(fd)
