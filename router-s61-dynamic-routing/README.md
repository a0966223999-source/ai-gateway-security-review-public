# S61 dynamic routing and lifecycle overlay — STAGED, NOT ENABLED

This candidate extends the recovered ORIGINAL S61 Executor port and lifecycle.
It creates no Store, queue, Source, grant, runtime service, database or MCP server.
The current live Gateway, Codex background worker, DR line and v1.0.15 plugin
are unchanged. Gate1 remains DENY; formal D2 is untouched.

## Implemented

- Match current original Source requirements against same-Store registered
  executors by capabilities, availability, host priority and stable ID. An
  explicit provider requirement never falls back. Selection and the original
  executor enqueue share the caller's original Store transaction.
- Persist the selected provider per original assignment in existing KV. Reentry
  and SQL reopen are query-only. Delivery and Stop retain that provider; Stop
  still cleans up owned work after revocation and can address the previous
  assignment when the original Engine has already created its replacement.
- Add optional applyWithDynamicRouting and original Runtime constructor hooks.
  Ordinary apply/applyWithRecovery retain their original behavior. Dynamic
  capabilities are live host functions, never configuration or request JSON.
- Guard original DshExecutor enqueue, effect consumer, native prompt callbacks,
  reports, event handlers, background injections, restart and shutdown. Check
  Source again after awaits. Restart observes the saved original attempt and
  does not cancel/requeue/restart it. A native UNKNOWN is observation-only;
  dynamic launch/turn failures do not trigger the legacy retry-model path.
- Call the host's durable native once fence immediately before the original
  prompt, after capacity/preparation. Query-only never sends. Preserve the
  original Sessions/AgentDelivery request-ID deduplication for input delivery.
- Guard original Sessions worker lookup, creation, prompt delivery, model-prompt
  binding, inherited and registered tools, and worker subagent decoration.
  A stale session name never grants the new provider's tools or Source rights.
- On shutdown only the latest assignment can identify the original DSH session;
  an old owned assignment cannot cause cancellation of a foreign replacement.

## Original-host capability contract

The optional fifth Runtime argument supplies originalProviderId, providers,
readRequirements and checkCurrent. Providers receive the SAME original Store,
originalExecutor and Runtime AbortSignal. Every provider must supply an original
native claimStart fence and query-only recover port. External providers also
must attachLifecycle before Runtime starts; they must enforce those callbacks
at their own consumers, native boundaries and shutdown. Sharing the signal
alone does not prove native cancellation.

The original DSH provider's executor must be the exact originalExecutor object.
The original Runtime supplies its lifecycle dynamically, fails closed while
binding, gives the Engine the selector, and keeps DSH prepare/setup/lifecycle
methods on the original object. Initialization calls selector recovery before
starting the original passes/effect loops. Binding rejects active legacy
assignments without an existing, valid saved route; there is no implicit
migration, adoption or renewal of their authority.

recover is a synchronous query/observation port into the existing host. It must
not resend, clear a once record, manufacture an ACK, or create a new assignment.
Only the original native result may advance original Engine state. Stop queued
and completionVerified=false are not proof that a native task stopped or ran.
No permissive adapter implementation is provided as an example grant.

## Validation

From this directory on Node 24:

    node --experimental-transform-types --experimental-vm-modules --test tests/*.test.mjs

48 engineering cases PASS, zero skipped. The 29 selector/composition cases use
exact copied original Engine/MemoryRepositories with a real temporary SQLite
KV/queue CONTRACT fixture. The 14 DSH and 5 Sessions cases execute the patched
original class methods through Node's TypeScript transform/VM modules, with
explicit synthetic Cordis/session/Source/background dependency adapters.
No live grant, native model, production Store/Effects/Runtime, full dependency
link, full project typecheck or independent QA is claimed. Four TS overlays
also parse after transform; that is syntax validation only.

The first expanded run (35/40) is retained. It caught an out-of-scope keyFor
reference in Runtime binding, now moved to module scope; four DSH test fixtures
also lacked the original required origin.triggerIds field. Corrected fixtures
and implementation pass, including the real original Engine retry flow.
SOURCE-PINS.json retains original upstream hashes and MIT license.

## Installation boundary / next concrete host work

This overlay has NOT been installed or enabled. The original production S61
Store/Effects/Registry/Fiber capability, exact-task current Source and revocation
mapping, native once fence, executor adapters and original ACK/result mapping
still need their actual owned host instances. Current Python batch success and
checkpoint permissions do not supply those instances. The current Work has no
native Work read/send/interrupt tool; no substitute was fabricated.

A trusted host loader must verify the pinned original files, stage this overlay
into that SAME S61 checkout, bind those ports, run its full typecheck, then
validate original Store/Effects restart, lost-response, no-duplicate, revocation,
Stop and result readback before enabling this shared capability. External
adapters must separately prove lifecycle/shutdown handling. Do not replace the
live Python/MCP branch with this isolated candidate. Do not rerun the completed
three-item background acceptance batch. Without the host capabilities, remain
DYNAMIC_LIFECYCLE_PATCHED_NOT_RUNTIME_BOUND.

## Rollback

No production deployment occurred. Restore the pinned original index/runtime/
DSH/Sessions files and remove the two new dynamic executor files when removing
this staged overlay. Never discard an existing native once/route record to
force a resend; an uncertain original attempt remains query-only.
