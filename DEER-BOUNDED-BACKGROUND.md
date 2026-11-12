# Bounded Codex batch execution

This increment extends the original FreshBatchHost execute action and existing
worker. The caller explicitly selects 1–3 saved independent pending item IDs
from one batch. The worker executes them sequentially using the original
codex_once executor. The MCP response can return pending while that same original
worker continues. No new queue, scheduler, store, service or authority is added.

Request: execution_mode=codex_batch_once, parent_id, operation_key, batch_id,
item_ids and optional timeout_seconds (10–90, default90). No prompt, command,
path, credentials, owner, execution profile or retry parameter is accepted.
batch_execute_batch and the compatible batch_task union use the same transport.
Existing batch_execute, batch_stop, checkpoint and DR management are preserved.

Before any child launches, validate all selected original rows, owner and full
seven-field native binding including payloadDigest. Immediately before each
launch, revalidate that item. The original executor and original SQL writer
fence remain the authority for once reservation, subprocess ownership, ACK and
result saving. A private batch lock prevents concurrent batch coordinators.
It is only an audit/coordination lock, not another task database.

At most three children run, one at a time, within a 300-second launch budget.
Any stop, denial, uncertain result or unsuccessful child halts remaining starts.
Stopping the active child uses the existing batch_stop. Pending cancellation
retains existing SQL semantics. Worker shutdown cleans up its owned processes.
Crash recovery is query-only; it never automatically replays a batch or child.

Read the final signed coordinator receipt with batch_receipt(kind execute) and
the original operation key. Read each saved result independently with the
existing batch_status(include_result=true,item_offset=0,item_limit=25).
CODEX_BATCH_FINISHED requires original_result_saved and
observed_execution_verified for every selected child. It does not imply QA.

This is opt-in bounded background coordination, not global automatic routing,
native Work dispatch, dependency scheduling, container isolation or S61 Engine
installation. Host automatic_dispatch remains false because ordinary submit
and unselected pending tasks never launch automatically. Codespace suspension
still makes the host unavailable.

Validation separates synthetic executor tests from original temporary SQL and
real process-group tests, and both from live Codex acceptance. Deploy only after
the SQL/process tests pass, exact installed host source/config are checked,
the original host is idle, and the original worker can be rolled back. Preserve
current DR/checkpoint wiring and running service arguments.
