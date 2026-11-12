"""Explicit one-item Codex execution, using original durable native fences.

This is a CLI read-only permission profile, not a container sandbox provider.
The public caller selects a saved item, never a command, path, or executable.
"""
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import time
from typing import Annotated, Literal

from pydantic import Field
from batch_mcp_entry import RequestBase, Text
from fresh_batch_host import canonical, exclusive_write, HostDenied
from platform_codex_stop import ActiveRun, registry, terminate_owned_group, save_stopped_item, track_owned_descendants


class Execute(RequestBase):
    execution_mode: Literal['codex_once']
    batch_id: Text
    item_id: Text
    timeout_seconds: Annotated[int, Field(strict=True, ge=10, le=90)] = 90


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def file_sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def successful_output(raw):
    """Require real CLI event structure; invalid or incomplete output stays unknown."""
    events = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    starts = [e for e in events if e.get('type') == 'thread.started']
    messages = [e['item']['text'] for e in events if e.get('type') == 'item.completed'
                and e.get('item', {}).get('type') == 'agent_message']
    if (len(starts) != 1 or not isinstance(starts[0].get('thread_id'), str)
            or not starts[0]['thread_id'] or not messages
            or not any(e.get('type') == 'turn.completed' for e in events)
            or any(e.get('type') in ('turn.failed', 'error') for e in events)):
        raise HostDenied('CODEX_COMPLETION_NOT_VERIFIED_QUERY_ONLY')
    report = messages[-1]
    if not isinstance(report, str) or not 0 < len(report.encode()) <= 32768:
        raise HostDenied('CODEX_RESULT_SIZE_QUERY_ONLY')
    return starts[0], report


async def execute_item(host, repository, request):
    # Reserve a process-local slot before the first await. Original SQL capacity
    # and durable once fences still apply independently to each batch/item.
    count = getattr(host, '_codex_execution_count', 0)
    if count >= 2:
        raise HostDenied('CODEX_HOST_CAPACITY_BUSY_QUERY_ONLY')
    host._codex_execution_count = count + 1
    try:
        return await _execute_item(host, repository, request)
    finally:
        host._codex_execution_count -= 1


async def _execute_item(host, repository, request):
    # Operator-owned pin, not executable lookup supplied by the GPT tool.
    host.check('execute')
    executable = shutil.which('codex')
    if not executable:
        raise HostDenied('CODEX_EXECUTABLE_NOT_FOUND')
    binding, owner = host.binding('execute'), host.config['owner']
    batch = await repository.get_bound_native_batch(request.batch_id, user_id=owner, binding=binding)
    if batch is None:
        raise HostDenied('CODEX_BATCH_NOT_BOUND')
    from deerflow.persistence.subagent_batches.model import SubagentBatchItemRow, SubagentBatchRow
    async with repository._sf() as session:
        row = await session.get(SubagentBatchRow, request.batch_id)
        if row is None or row.user_id != owner:
            raise HostDenied('CODEX_BATCH_NOT_BOUND')
        saved_binding = row.execution_spec.get('native_control', {}).get('binding', {})
        if {key: saved_binding.get(key) for key in binding} != binding:
            raise HostDenied('CODEX_SOURCE_CHANGED')
        # Dispatch requires the ORIGINAL full seven-field binding, including
        # the Holder's payloadDigest. The six-field read scope is insufficient.
        repository._native_control(row, user_id=owner, binding=saved_binding)
        binding = json.loads(canonical(saved_binding))
        item = await session.get(SubagentBatchItemRow, request.item_id)
        if item is None or item.batch_id != request.batch_id or item.status != 'pending':
            raise HostDenied('CODEX_PENDING_ITEM_REQUIRED')
        prompt = item.prompt
    if not isinstance(prompt, str) or not 0 < len(prompt.encode()) <= 48000:
        raise HostDenied('CODEX_PROMPT_SIZE')
    run = host.root / ('codex-' + secrets.token_hex(16))
    run.mkdir(mode=0o700)
    work = run / 'work'
    work.mkdir(mode=0o700)
    intent = 'codex-' + secrets.token_hex(16)
    now = lambda: datetime.now(timezone.utc)
    exclusive_write(run / 'intent.json', canonical(dict(batch_id=request.batch_id,
        item_id=request.item_id, operation_key=request.operation_key, intent_id=intent,
        prompt_sha256=sha(prompt.encode()), timeout_seconds=request.timeout_seconds)))
    host.check('execute')
    reserved = await repository.reserve_native_attempt(request.batch_id, request.item_id,
        user_id=owner, binding=binding, intent_id=intent, now=now())
    exclusive_write(run / 'reservation.json', canonical(reserved))
    if reserved.get('sendAllowed') is not True:
        raise HostDenied('CODEX_ATTEMPT_QUERY_ONLY')
    host.check('execute')
    cmd = [executable, 'exec', '--ignore-user-config', '--skip-git-repo-check',
           '--ephemeral', '--sandbox', 'read-only', '--json', '-C', str(work), '-']
    exclusive_write(run / 'invocation.json', canonical(dict(argv=cmd, started_at=time.time())))
    proc = None
    active = None
    feeding = None
    outcome = {'status':'STOP_OUTCOME_UNKNOWN_QUERY_ONLY', 'batch_id':request.batch_id,
               'item_id':request.item_id}
    stopped = None
    try:
        with open(run / 'events.jsonl', 'xb') as out, open(run / 'stderr.log', 'xb') as err:
            os.chmod(run / 'events.jsonl', 0o600)
            os.chmod(run / 'stderr.log', 0o600)
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=out, stderr=err, start_new_session=True)
            active = ActiveRun(proc=proc, intent=intent, binding=binding)
            track_owned_descendants(proc,active.tracked)
            registry(host)[(request.batch_id,request.item_id)] = active
            def feed():
                try:
                    proc.stdin.write(prompt.encode())
                finally:
                    proc.stdin.close()
            feeding = asyncio.create_task(asyncio.to_thread(feed))
            deadline = time.monotonic() + request.timeout_seconds
            while proc.poll() is None:
                host.check('execute')
                track_owned_descendants(proc,active.tracked)
                if active.stop.is_set():
                    observed = await terminate_owned_group(proc,active.tracked)
                    if observed['signals_sent']:
                        out.flush()
                        err.flush()
                        evidence = dict(observed, batch_id=request.batch_id, item_id=request.item_id,
                            stopped_at=time.time(),
                            intent_id=intent, stop_operation_key=active.stop_operation_key,
                            execution_operation_key=request.operation_key,
                            events_sha256=file_sha(run/'events.jsonl'),
                            stderr_sha256=file_sha(run/'stderr.log'))
                        exclusive_write(run/'stop-evidence.json', canonical(evidence))
                        await save_stopped_item(host, repository, request, active, evidence)
                        outcome = dict(evidence, status='STOPPED', original_terminal_state_saved=True,
                                       observed_execution_verified=True, stopped_by_this_request=True)
                        exclusive_write(run/'stop-receipt.json', canonical(outcome))
                        return outcome
                    break  # Completion won the race; finish through the normal result path.
                if time.monotonic() >= deadline:
                    stopped = 'CODEX_TIMEOUT_QUERY_ONLY'
                    raise HostDenied(stopped)
                if max((run / 'events.jsonl').stat().st_size, (run / 'stderr.log').stat().st_size) > 1048576:
                    stopped = 'CODEX_OUTPUT_LIMIT_QUERY_ONLY'
                    raise HostDenied(stopped)
                await asyncio.sleep(0.25)
        if proc.returncode != 0:
            raise HostDenied('CODEX_EXIT_FAILURE_QUERY_ONLY')
        await feeding
        raw = (run / 'events.jsonl').read_bytes()
        if len(raw) > 1048576:
            raise HostDenied('CODEX_OUTPUT_LIMIT_QUERY_ONLY')
        start, report = successful_output(raw)
        child = 'codex-thread:' + start['thread_id']
        ack = canonical(dict(event=start, intent_id=intent, batch_id=request.batch_id, item_id=request.item_id))
        exclusive_write(run / 'ack.json', ack)
        host.check('execute')
        await repository.record_native_ack(request.batch_id, request.item_id, user_id=owner,
            binding=binding, intent_id=intent, child_canonical=child, evidence_sha256=sha(ack), now=now())
        host.check('execute')
        saved = await repository.finalize_item(request.item_id, lease_owner=owner, succeeded=True,
            result=report, result_preview=report[:1000], result_truncated=False, error=None,
            stop_reason='completed', token_usage=None, model_name='codex-cli-default', completed_at=now(),
            acceptance_verdict=None, native_context=dict(binding=binding, intentId=intent,
                childCanonical=child, ackEvidenceSHA256=sha(ack), reportSHA256=sha(report.encode())))
        if saved is not True:
            raise HostDenied('CODEX_RESULT_SAVE_QUERY_ONLY')
        receipt = dict(batch_id=request.batch_id, item_id=request.item_id, child_canonical=child,
            exit_code=proc.returncode, original_result_saved=True, observed_execution_verified=True,
            events_sha256=sha(raw), result_sha256=sha(report.encode()))
        exclusive_write(run / 'receipt.json', canonical(receipt))
        outcome = receipt
        return receipt
    finally:
        # Every exceptional path, including STOP, task cancellation and lost authority,
        # ends its owned process group. Never leaves a child running after revocation.
        try:
            if proc is not None:
                await terminate_owned_group(proc,active.tracked if active is not None else None)
        finally:
            if feeding is not None:
                await asyncio.gather(feeding,return_exceptions=True)
            if active is not None:
                registry(host).pop((request.batch_id,request.item_id),None)
                if not active.done.done():
                    active.done.set_result(outcome)
