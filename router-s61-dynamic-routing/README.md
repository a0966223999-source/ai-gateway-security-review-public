# S61 dynamic executor selection — staged, NOT ENABLED

Implements a trusted-host adapter at the ORIGINAL S61 Executor port. No live
Runtime, Store, queue, Source, credential, database or MCP server is created.
No existing Gateway, Codex worker, DR runtime or published plugin is changed.

The original Engine still owns admission, mandates, tickets and assignments.
The adapter reads only the current original assignment, original policy and
host-resolved Source requirements. It matches required capabilities against
already registered executor adapters, excludes unavailable adapters, then uses
host-configured priority and stable ID order. An explicit provider requirement
never falls back to another provider. Health and priority do not grant authority.

The selection record and ORIGINAL effect enqueue must share the original Store
transaction. The adapter uses the existing KV namespace only; it does not open
a second store or invent another task authority. Same assignment is query-only
after selection; delivery and Stop retain that provider. Stop can still queue
owned cleanup after policy revocation. Every returned state says queued or
query-only, never that execution or stopping completed.

## Validation

Run with Node24:

    node --experimental-transform-types --test tests/dynamic_executor.test.mjs

19 engineering cases pass. Tests use exact copied original S61 Engine and
MemoryRepositories, with real temporary SQLite KV/effect CONTRACT fixtures.
Identity, Source permissions and executor adapters are synthetic. Tests do NOT
instantiate the production Store/Effects/Runtime, dispatch a Work, run a model,
or prove independent QA. The copied core is test-only; its MIT license and
source pins are retained. No full TypeScript project typecheck has run.

## Required original-host integration — not supplied or enabled here

1. Bind the same live original S61 Store/Effects/Registry/Fiber and current
   ticket/assignment. The current Python batch worker and checkpoint-only
   Source are not these capabilities and cannot be relabeled as them.
2. Resolve requirements from the exact current authorized Source, preserving
   revision, digest, mandate, owner, revocation and original native once fence.
   The host's readRequirements/checkCurrent are constructor capabilities,
   never settings JSON, model output, an OAuth owner alone or lambda True.
3. Supply already owned, lifecycle-bound executor adapters that enqueue in
   that SAME Store and recheck authority/capacity/once at actual native launch.
   Codex batch success does not provide a S61-native ACK mapping. DR runtime
   consent and native Work read/send/interrupt remain separately required.
4. Before installing the optional Engine hook, integrate every ORIGINAL DSH
   lifecycle path. In particular src/executor/dsh.ts resume() currently scans
   all pending/running tickets and can call this.start directly; launchQueued,
   delivery, restore and shutdown must respect the persisted provider. A hook
   only around Engine.executor can therefore dispatch through the wrong
   executor after restart. Do not install such a partial hook.

An exploratory Engine/Runtime-only hook was withdrawn after this review. The
candidate intentionally contains no src/index.ts or src/app/runtime.ts overlay.
bindRuntimeDynamicExecutor is a tested composition HELPER, not an installed
Runtime entry or permission to enable one. Native Work callables were absent
from this Work's current tool inventory; that is not a claim about every host.

Ordinary composition without a routing capability returns the original
executor unchanged. Do not enable routing or publish it as live until the
same-Store production integration, source/revocation and lifecycle cases have
real receipts. The existing v1.0.15 platform functions stay in service.

## Restore / rollback

This is an isolated source candidate. No formal deployment to roll back.
Review source pins and restore the original S61 source before preparing its
integration. Never replace the live Python/MCP branch with this candidate
branch, renew an expired Source, clear once records, cancel other Work tasks,
or rerun the already completed three-item platform acceptance batch.
