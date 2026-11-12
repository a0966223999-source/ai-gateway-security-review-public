"""Stop only a current, in-process owned Codex child; preserve SQL once fences."""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import signal
import time
from typing import Literal

from batch_mcp_entry import RequestBase, Text
from fresh_batch_host import HostDenied, canonical, exclusive_write


class Stop(RequestBase):
    execution_mode: Literal['stop_codex_once']
    batch_id: Text
    item_id: Text


@dataclass
class ActiveRun:
    proc: object
    intent: str
    binding: dict
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    done: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())
    stop_operation_key: str | None = None
    tracked: dict = field(default_factory=dict)


def registry(host):
    if not hasattr(host, '_active_codex_runs'):
        host._active_codex_runs = {}
    return host._active_codex_runs


def live_group_members(group):
    """Read only process state/group IDs; no command lines, environments or secrets."""
    members = []
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            fields = (path/'stat').read_text().rsplit(') ', 1)[1].split()
            if int(fields[2]) == group and fields[0] not in ('Z', 'X'):
                members.append(int(path.name))
        except (OSError, ValueError, IndexError):
            continue
    return members


def track_owned_descendants(proc, tracked):
    snapshots = {}
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():continue
        try:
            fields = (path/'stat').read_text().rsplit(') ',1)[1].split()
            snapshots[int(path.name)] = (int(fields[1]),fields[19],fields[0])
        except (OSError,ValueError,IndexError):continue
    if proc.pid in snapshots and proc.pid not in tracked and proc.returncode is None:
        tracked[proc.pid] = snapshots[proc.pid][1]
    changed = True
    while changed:
        changed = False
        for pid,(parent,start,state) in snapshots.items():
            if (pid not in tracked and parent in tracked and parent in snapshots
                    and snapshots[parent][1] == tracked[parent]):
                tracked[pid] = start
                changed = True
    return [pid for pid,start in tracked.items() if pid in snapshots
            and snapshots[pid][1] == start and snapshots[pid][2] not in ('Z','X')]


def signal_tracked(tracked, sig):
    for pid,start in list(tracked.items()):
        fd = None
        try:
            # pidfd pins this observed process instance; a reused PID is never a target.
            fd = os.pidfd_open(pid)
            fields = Path('/proc',str(pid),'stat').read_text().rsplit(') ',1)[1].split()
            if fields[19] == start and fields[0] not in ('Z','X'):
                signal.pidfd_send_signal(fd,sig)
        except (ProcessLookupError,FileNotFoundError):pass
        finally:
            if fd is not None:os.close(fd)


async def terminate_owned_group(proc, tracked=None):
    """Use the retained Popen group, never a caller-supplied PID or recovered PID."""
    sent = []
    tracked = {} if tracked is None else tracked
    for sig in (signal.SIGTERM, signal.SIGKILL):
        alive = track_owned_descendants(proc,tracked)
        if proc.poll() is not None and not live_group_members(proc.pid) and not alive:
            break
        try:
            os.killpg(proc.pid, sig)
            sent.append(sig.name)
        except ProcessLookupError:
            pass
        signal_tracked(tracked,sig)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            alive = track_owned_descendants(proc,tracked)
            if proc.poll() is not None and not live_group_members(proc.pid) and not alive:
                break
            await asyncio.sleep(.05)
    if proc.poll() is None or live_group_members(proc.pid) or track_owned_descendants(proc,tracked):
        raise HostDenied('CODEX_PROCESS_GROUP_STOP_NOT_VERIFIED_QUERY_ONLY')
    proc.wait()
    return {'process_group_stopped': True, 'observed_descendants_stopped':True,
            'tracked_process_count':len(tracked), 'signals_sent': sent, 'exit_code': proc.returncode}


async def save_stopped_item(host, repository, request, active, evidence):
    """Original SQL transaction, full binding, matching intent and observed stop proof."""
    from deerflow.persistence.subagent_batches.model import SubagentBatchRow, SubagentBatchItemRow
    if evidence.get('process_group_stopped') is not True or not evidence.get('signals_sent'):
        raise HostDenied('OBSERVED_CODEX_STOP_REQUIRED')
    owner = host.config['owner']
    host.check('execute')
    async with repository._sf() as session:
        await repository._lock_native_writer(session, request.batch_id, owner)
        batch = await session.get(SubagentBatchRow, request.batch_id, populate_existing=True)
        item = await session.get(SubagentBatchItemRow, request.item_id, populate_existing=True)
        host.check('execute')
        if batch is None or item is None or item.batch_id != batch.id:
            raise HostDenied('STOP_ITEM_NOT_BOUND')
        control = repository._native_control(batch, user_id=owner, binding=active.binding)
        attempt = control['items'].get(item.id)
        if (not isinstance(attempt, dict) or attempt.get('intentId') != active.intent
                or attempt.get('phase') not in ('attempted_unknown', 'acknowledged')
                or item.status != 'running' or item.attempt != 1 or item.lease_owner != owner):
            raise HostDenied('STOP_ATTEMPT_CHANGED_QUERY_ONLY')
        now = datetime.now(timezone.utc)
        control['items'][item.id] = {**attempt, 'phase': 'stopped',
            'stopEvidenceSHA256': hashlib.sha256(canonical(evidence)).hexdigest(),
            'stopOperationKey': active.stop_operation_key}
        batch.execution_spec = {**batch.execution_spec, 'native_control': control}
        item.status = 'cancelled'
        item.stop_reason = 'stopped_by_request'
        item.error = 'Stopped by authorized request; process group termination verified'
        item.cancel_requested_at = now
        item.completed_at = now
        item.updated_at = now
        item.lease_owner = None
        item.lease_expires_at = None
        await session.flush()
        await repository._refresh_batch_status(session, batch, now=now)
        host.check('execute')
        await session.commit()
    host.check('execute')
    return True


async def stop_item(host, repository, request):
    host.check('execute')
    owner, binding = host.config['owner'], host.binding('execute')
    from deerflow.persistence.subagent_batches.model import SubagentBatchRow, SubagentBatchItemRow
    async with repository._sf() as session:
        batch = await session.get(SubagentBatchRow, request.batch_id)
        item = await session.get(SubagentBatchItemRow, request.item_id)
        if batch is None or item is None or item.batch_id != batch.id or batch.user_id != owner:
            raise HostDenied('STOP_ITEM_NOT_BOUND')
        saved = batch.execution_spec.get('native_control', {}).get('binding', {})
        if {k:saved.get(k) for k in binding} != binding:
            raise HostDenied('STOP_SOURCE_CHANGED')
        control = repository._native_control(batch, user_id=owner, binding=saved)
        host.check('execute')
        if item.status in ('succeeded', 'failed', 'cancelled'):
            return {'status':'ALREADY_TERMINAL_NOT_STOPPED', 'batch_id':batch.id,
                    'item_id':item.id, 'item_status':item.status, 'stopped_by_this_request':False}
        active = registry(host).get((batch.id, item.id))
        if active is None:
            return {'status':'STOP_NOT_OWNED_QUERY_ONLY', 'batch_id':batch.id, 'item_id':item.id,
                    'stopped_by_this_request':False}
        attempt = control['items'].get(item.id)
        if (not isinstance(attempt,dict) or attempt.get('intentId') != active.intent
                or active.binding != saved or item.status != 'running' or item.attempt != 1):
            raise HostDenied('STOP_ATTEMPT_CHANGED_QUERY_ONLY')
        if active.proc.poll() is not None:
            return {'status':'ALREADY_EXITED_QUERY_RESULT', 'batch_id':batch.id, 'item_id':item.id,
                    'stopped_by_this_request':False}
        if active.stop_operation_key is not None:
            return {'status':'STOP_ALREADY_REQUESTED_QUERY_ONLY', 'batch_id':batch.id,
                    'item_id':item.id, 'stop_operation_key':active.stop_operation_key}
        host.check('execute')
        active.stop_operation_key = request.operation_key
        active.stop.set()
    result = await asyncio.wait_for(asyncio.shield(active.done), timeout=15)
    host.check('execute')
    return result
