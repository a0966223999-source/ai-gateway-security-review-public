"""Same-event-loop rendezvous for the original gated Codex executor.

All callbacks are held host capabilities. This module provides no transport,
authorization, durable retry, SQL writer, or replacement process registry.
"""
import asyncio
import copy
import hashlib
import json


class AttemptQueryOnly(RuntimeError):
    pass


def _digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(raw).hexdigest()


class OwnedCodexAttempt:
    def __init__(self, *, expected, execute_owned, check_current,
                 read_original, stop_owned):
        if not all(callable(c) for c in
                   (execute_owned, check_current, read_original, stop_owned)):
            raise TypeError('ORIGINAL_HELD_CALLBACKS_REQUIRED')
        fields = {'batch_id', 'item_id', 'binding', 'prompt_sha256',
                  'dependency_input_sha256'}
        if set(expected) != fields:
            raise ValueError('EXACT_NATIVE_MAPPING_REQUIRED')
        binding = expected['binding']
        keys = {'taskRef', 'version', 'operationRef', 'ownerRef',
                'sourceRevision', 'sourceDigest', 'payloadDigest'}
        if not isinstance(binding, dict) or set(binding) != keys:
            raise ValueError('FULL_NATIVE_BINDING_REQUIRED')
        if any(not isinstance(v, str) or not v for v in binding.values()):
            raise ValueError('INVALID_NATIVE_BINDING')
        for value in (binding['sourceDigest'], binding['payloadDigest'],
                      expected['prompt_sha256'], expected['dependency_input_sha256']):
            if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
                raise ValueError('INVALID_NATIVE_DIGEST')
        if any(not isinstance(expected[k], str) or not expected[k] for k in ('batch_id', 'item_id')):
            raise ValueError('INVALID_NATIVE_IDS')
        self._expected = copy.deepcopy(expected)
        self._execute = execute_owned
        self._check = check_current
        self._read = read_original
        self._stop = stop_owned
        self._task = None
        self._ready = None
        self._decision = None
        self._context = None
        self._release_attempted = False
        self._closed = False
        self._stop_task = None

    async def _current(self, action):
        if await self._check(action, copy.deepcopy(self._expected)) is not True:
            raise AttemptQueryOnly('SOURCE_OR_ASSIGNMENT_NOT_CURRENT')

    async def _gate(self, context):
        if self._context is not None or self._closed:
            raise AttemptQueryOnly('GATE_REENTRY_QUERY_ONLY')
        if set(context) != set(self._expected) | {'intent_id'}:
            raise AttemptQueryOnly('NATIVE_CONTEXT_CHANGED')
        if ({k: context[k] for k in self._expected} != self._expected
                or not isinstance(context['intent_id'], str)
                or not context['intent_id']):
            raise AttemptQueryOnly('NATIVE_MAPPING_CHANGED')
        await self._current('reserved')
        if self._closed:
            raise AttemptQueryOnly('STOPPED_BEFORE_START')
        self._context = copy.deepcopy(context)
        self._ready.set_result(copy.deepcopy(context))
        accepted = await self._decision
        if accepted is not True or self._closed:
            raise AttemptQueryOnly('START_NOT_RELEASED')
        await self._current('before-launch')
        if self._closed:
            raise AttemptQueryOnly('STOPPED_BEFORE_START')
        return True

    async def _run(self):
        try:
            await self._current('reserve')
            return await self._execute(self._gate)
        finally:
            # Wake reserve() on early errors without leaking task exceptions.
            if not self._ready.done():
                self._ready.set_result(None)

    async def reserve(self):
        if self._closed:
            raise AttemptQueryOnly('CLOSED_QUERY_ONLY')
        if self._task is None:
            loop = asyncio.get_running_loop()
            self._ready = loop.create_future()
            self._decision = loop.create_future()
            self._task = asyncio.create_task(self._run())
            self._task.add_done_callback(lambda task: None if task.cancelled() else task.exception())
        context = await asyncio.shield(self._ready)
        if context is None:
            # Re-raise the original error; never start a replacement task.
            await asyncio.shield(self._task)
            raise AttemptQueryOnly('NO_NATIVE_RESERVATION_OBSERVED')
        if self._task.done() or self._closed:
            raise AttemptQueryOnly('NATIVE_TASK_ENDED_QUERY_ONLY')
        return dict(status='reserved', context=copy.deepcopy(context),
                    context_digest=_digest(context), native_started=False)

    async def release(self, context_digest):
        if (self._closed or self._task is None or self._task.done()
                or self._context is None or context_digest != _digest(self._context)):
            raise AttemptQueryOnly('EXACT_RESERVED_TASK_REQUIRED')
        if self._release_attempted:
            return dict(status='query-only', native_started=False)
        # Set before awaiting authority: concurrent releases cannot launch twice.
        self._release_attempted = True
        try:
            await self._current('release')
            if self._closed or self._task.done() or self._decision.done():
                raise AttemptQueryOnly('NATIVE_TASK_ENDED_QUERY_ONLY')
            self._decision.set_result(True)
        except BaseException:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            raise
        return dict(status='released-await-original-ack', native_started=False)

    async def query(self):
        await self._current('query')
        observation = await self._read(copy.deepcopy(self._expected))
        await self._current('query-result')
        return observation

    async def wait(self):
        if self._task is None:
            raise AttemptQueryOnly('NOT_RESERVED')
        return await asyncio.shield(self._task)

    async def stop(self):
        # Closure/cancellation is cleanup of this owned task, not a new grant.
        self._closed = True
        if self._stop_task is None:
            self._stop_task = asyncio.create_task(self._stop_once())
        return await asyncio.shield(self._stop_task)

    async def _stop_once(self):
        if self._task is None:
            return dict(status='not-started', native_stopped=False,
                        stopped_by_this_request=False)
        if self._release_attempted and not self._task.done():
            try:
                # Captured original stop_item path and request, same process.
                return await self._stop(copy.deepcopy(self._expected))
            finally:
                # Covers the release/Popen race, UNKNOWN, and revoked Stop.
                # The original executor's finally kills its own process group.
                if not self._task.done():
                    self._task.cancel()
                    await asyncio.gather(self._task, return_exceptions=True)
        if not self._task.done():
            self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        return dict(status='owned-task-ended-query-original', native_stopped=False,
                    original_sql_terminal_state_verified=False,
                    stopped_by_this_request=False)
