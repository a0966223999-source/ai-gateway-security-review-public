# Big goal to original Deer child tasks

This extends the existing authenticated `batch_task` and `batch_status` tools.
The current GPT/Work is the planner, as in the original neural-graph design.
There is no second model API, rule-based pretend planner, task store or executor.

1. Call `batch_task` with the enrolled parent, a new operation key and `goal`.
   With no `items`, the API returns `AWAITING_CURRENT_GPT_DECOMPOSITION` plus a
   planning contract. It has not saved, queued or dispatched anything.
2. In the same GPT turn, split the goal into independent, self-contained items.
   Keep dependency chains together; this batch is not a DAG scheduler.
3. Call `batch_task` once with the same original goal/parent/key, title and items.
   A narrow submitter adapter adds a versioned goal snapshot to the original
   request's execution spec. The ORIGINAL Holder hashes and saves goal metadata
   and original items in the same original SQL creation transaction.
4. On an unknown outcome, use `batch_receipt` with the same submit key. Never
   invent a new key or repeat an unknown creation.
5. Use a fresh read key with `batch_status(include_plan=true, item_offset=0)`.
   The host verifies original owner/source binding, reads only the saved plan
   field and one original item page with prompts, and returns the parent goal,
   children and saved state. Pages contain at most 25 children.

Original item-only submissions continue to work. Goal snapshots never grant
permissions or turn on execution. `automatic_dispatch=false` stays unchanged.
The optional legacy callback adapter explicitly rejects goal persistence until
its host supplies the corresponding original-store adapter; it never silently
drops a goal. The fresh host path implements persistence and plan readback.

The earlier enrollment expired. Do not edit its pin, extend old permissions,
clear attempt fences, replay old tasks or claim that deployment alone makes a
new enrollment live. A new bounded owner-scoped enrollment can be used for
fresh management tests, retaining original history and unknown outcomes.

Tests: contract preparation does not touch Redis; injected runtime, missing
input, duplicate keys and other parents are rejected. The original backend
integration test uses the ORIGINAL Holder and SQLite repository in a new
temporary DB: one goal, three children, restart/fence, saved readback and
unstarted cancellation. Set XJ_DEER_BACKEND to the existing backend directory
to run it; no model, worker or external dispatch is launched by those tests.
