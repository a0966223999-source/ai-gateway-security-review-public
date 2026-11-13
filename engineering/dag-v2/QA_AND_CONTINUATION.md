# DAG remaining work — independent QA and continuation

Status: BLOCKED_EXECUTION_ENVIRONMENT_UNAVAILABLE
Owner request: 完成剩餘的全新 Work DAG驗收、獨立QA、大型/跨批次DAG及重啟恢復。
Live version remains v1.0.17; no v2 source installed or deployed in this round.

## Evidence and current live scope
Runtime commit: 19de9dbea37885642e7b2e2b6dcce60939d060f0.
Original host source/raw tests/live arithmetic DAG receipt:
f55a47289c8bdb8add3cb184ffbce73ab5226c67 / evidence/original-host-dag-20261113.json.
The existing original host reports a verified heartbeat and execution_available=true.
The unavailable environment is this Work's build/host-access executor, not proof that the original platform service stopped.

## Independent QA
Reviewer: /root/dag_independent_qa, independent AI engineering reviewer; not a human ACK.
Verdict for the selected v1 implementation: FAIL, repair and re-review required.
Reviewed code blobs and original evidence blob were matched to the fixed commits.
No live model, deployment mutation, or QA impersonation occurred during review.

1. P2: execute_dag appends a child's returned receipt to completed before validating original_result_saved, observed_execution_verified, and SQL completion. STOP or SQL verification failures can therefore be omitted from unverified_item_ids. Reproduction: return STOPPED with observed_execution_verified=true and original_terminal_state_saved=true but no original_result_saved.
2. P2: verified_result checks a succeeded row and superficial ACK fields, without verifying the original completion record's submittedResultDigest. ACK-present but missing-completion or modified-result rows can be accepted; a nonhex ACK digest or empty codex-thread suffix was also accepted. This is an integrity gap, not proof that public callers can modify SQL.
3. P3: attempted is recorded before the executor's SQL reserve. A capacity rejection can incorrectly classify an untouched pending item as unknown. Recovery must use original SQL/native fences rather than this derived list.
4. The existing untouched-pending coordinator preflight intentionally prevents replay but does not implement partial-success restart recovery.

## Original SQL contract
_native_control requires exactly schema/binding/items. Do not insert DAG runtime into that strict object.
SubagentBatchRow has execution_spec JSON but no metadata_ field; a separately named runtime field in execution_spec can be considered under the original writer lock.
The completion submittedResultDigest is SHA256 of UTF-8 canonical JSON with sort_keys=true, separators=(',', ':'), ensure_ascii=false, allow_nan=false, containing:
succeeded=true, result, result_preview, result_truncated, error, stop_reason, token_usage, model_name, acceptance_verdict.
Also verify completion.itemStatus=succeeded; do not confuse this digest with the pure result string SHA.

Offline backend reference found by QA (origin commit not yet established):
model.py SHA256 581ca36f39d43be5ace8e985d8f4c2041dc2dc5d06f4d8918f32b9e74c452715
sql.py SHA256 1871084e8df3a5f8b8f0e44ef80e9805793cf9ff0acbbdff0a9fa86fc9ea7a5f
Before calling it the exact live backend, independently pin against the original host. Use temporary SQL only for engineering tests.

## Candidate scope drafted, NOT verified
Local draft directory: /workspace/scratch/e88b2b6f1c12/host-dag-v2.
Some local draft edits were acknowledged before interruption. The final platform_dag_runtime.py write/compile call lost its execution environment; write/compile outcome UNKNOWN. The waiting orchestration cell was terminated; it did not target live service files. On resume, inspect files and syntax before using any draft. No v2 tests or independent v2 review passed.

Draft approach:
- Repair full completion-payload digest and nonhex/empty ACK validation.
- Classify verified, pristine/unstarted, and uncertain nodes from original SQL/native control.
- Keep v1 compatibility, add deer-codex-dag-v2 for 1..100 saved nodes.
- External references identify an existing same-owner, same-host-bound batch/item and its original payload digest; expose only that nonsecret digest in authorized include_plan.
- Persist original explicit execute intent outside native_control in the SAME original SQL row under _lock_native_writer. Pin original request ID/digest, host config/generation, graph digest and a bounded deadline to the retained private original attempt record.
- New codex_dag_once tool contract must be registered and published; do not send fields/modes that the current tool schema does not support.
- Worker startup may continue only already admitted graphs after revalidating current authority. Reuse verified succeeded results; launch only untouched pending nodes. Running/UNKNOWN without retained proof stays query-only; never reset native once or replay a model send.
- Preserve original worker/process ownership, STOP/revocation behavior, existing DR/Router/memory lines, and Gate1 DENY/D2 boundaries.

## Next required actions, in order
1. Restore a usable execution environment for this Work; reacquire the original Codespace read-only endpoint. Do not replace the original host or install a second service.
2. Read current live code/pins, in-flight tasks and graph states; reconcile draft against current source. Do not overwrite changes made by other work lines.
3. Finish the integrity fixes, worker recovery hook, authenticated execute schema, cross-batch validation, and compatibility tests. The draft alone is not a completed implementation.
4. Add meaningful tests: ACK present but completion/digest false, DAG STOP/SQL postvalidation failure, pre-reserve denial classification, known-success restart, UNKNOWN never resent, expired/revoked recovery, cross-batch owner/payload mismatch, queue/capacity and same-node competing callers.
5. Independent AI QA re-review the exact changed commit. Retain the initial FAIL. Do not relabel it PASS based on test counts.
6. Only after original-host engineering tests and current idle/pin checks pass, perform the minimal guarded deployment through the original manager, then separate new live DAG acceptance. Never replay the completed arithmetic DAG.
7. A true fresh ChatGPT Work acceptance must run in a genuinely separate Work and retain its own original tool receipts. A collaboration subagent or Codex child does not establish that.
8. Publish revised limits and verified scope to the same existing plugin, independently read back, save evidence and update Hub.

Outstanding: all items above; no claim of a complete v2 release, cross-Work DAG PASS, restart PASS or independent QA PASS.
