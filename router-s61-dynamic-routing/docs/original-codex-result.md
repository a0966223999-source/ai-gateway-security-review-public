# Original Codex result mapping

`bindOriginalCodexResult` holds the original S61 Store and trusted original
`checkCurrent` and `readOriginal` callbacks. Binding fixes one current S61
assignment to an already authorized original batch/item plus all seven native
binding fields. It neither submits a batch nor creates a Source or grant.
Rebinding the same assignment to different native identities is refused.

The original host reader independently reads saved SQL, original native receipt
and exact raw ACK JSON, projecting them into `OriginalCodexObservation`. It must
enforce original account, Source, lifetime and read permissions itself; a model
or tool response cannot supply this callback or pass observed data directly.
`native_context` uses the original finalize metadata fields `binding`,
`intentId`, `childCanonical`, `ackEvidenceSHA256`, `reportSHA256`.

Observation requires succeeded/attempt=1, matching full binding and original
batch/item, verified/saved receipt, exit=0, result SHA, exact raw ACK SHA,
intent and native thread identity. Source and latest S61 assignment are checked
again after the async read and inside the original Store transaction. Captured
evidence is immutable and stays in the original KV namespace; no extra database,
queue, grant or native call is created. Pending/failed/cancelled reads stay
query-only. Different later evidence cannot replace a captured result.

Evidence capture returns `engineCompleted=false`. It never invokes Engine.report,
acceptance, person ACK or QA, and never closes a ticket. The original consumer
still needs its trusted ACK/start, delivered-input and result lifecycle mapping,
as well as the concrete original host reader and execution adapter. Formal
dispatch remains disabled until those parts and exact-task authority are bound.

The seven new tests use the original production Store in temporary SQLite.
Native SQL projection/ACK/Source/identity are explicit fixtures. They establish
mapping and evidence refusal behavior, not a real Codex or independent QA pass.
