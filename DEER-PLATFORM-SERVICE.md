# Deer account platform service

Uses the existing OAuth MCP, existing Redis transport and original Deer SQLite
repository. No new task database, model gateway or Work identity is created.
The existing owner explicitly authorized the shared account service in this
thread. A new private enrollment was created, rather than extending old pins.

## Entry

Discover `batch_host_status` each time. `expires_at=null` is valid only for the
explicit `owner-platform-service-v1` lifecycle and owner service authority.
The signed heartbeat must be current. Other accounts and stale parents fail.
Ordinary `batch_task` goal/items requests still only record pending work.

For explicit execution, call `batch_execute`, or the existing `batch_task`
with `execution_mode=codex_once`, parent_id, new operation_key, saved batch_id,
saved item_id and timeout_seconds (10..90). No prompt or executable is accepted
in execution mode. The original full payloadDigest binding is recovered from
original SQL and revalidated by original reserve/ACK/finalize transactions.
Use `batch_receipt(operation_kind=execute)` with the same key for UNKNOWN.
Then independently read `batch_status(include_result=true,item_offset=0)`.

The outer native_execution_verified=false is a management boundary; the signed
execution result independently reports observed_execution_verified and
original_result_saved. CLI completion does not supply a QA verdict.

## Original host operation

Source: `/home/codespace/deer-platform-service-20261112`.
Private state: `/home/codespace/.deer-platform-service-20261112` (0700/0600).
Original backend: `/home/codespace/gateway-r12-restore-yfl4xryg/source/backend`.
Original database: backend `.deer-flow/data/deerflow.db`.

Trusted `ops/platform_service_bootstrap.py prepare` is one-time only and
refuses an existing root. Its transfer is X25519/HKDF/AES-GCM encrypted to the
operator's ephemeral public key. Never print ingress or signing keys.
`start --endpoint <verified pinned original deployment control URL>` starts the
manager with the original backend Python. Verify the signed heartbeat after
starting; a PID alone does not prove readiness. Manager permits one worker and
at most three process restarts; it never replays popped requests or SQL attempts.

To permanently revoke this enrollment, the trusted operator uses
`fresh_batch_host.py stop --root <private state>` under the original Python.
STOP is permanent for this enrollment: do not delete it to resume. Disable the
existing Vercel host flag too, then verify heartbeat becomes unavailable and
owned child processes stop. Do not delete attempts, SQL rows, keys or logs.
Maintenance reload uses SIGTERM only on the observed, verified owned worker PID;
the manager starts current code without re-enqueueing prior UNKNOWN requests.

## Limits

This service runs only while the original Codespace is running. No claim of
24-hour availability or automatic restart after Codespace suspension is made.
Codex uses existing login, ephemeral/read-only CLI profile and an empty working
directory, one item at a time. This is not the AIO container sandbox. It does
not support file writing through this profile, dependency scheduling, automatic
fan-out or native Work wake-up. cancel_batch only cancels unstarted items.
STOP and revocation terminate the executor's owned process group. Failures keep
their original attempt fences and are query-only; do not retry with new keys.

## Verification

Local lifecycle, original control and batch boundary suites; original-backend
temporary SQL plus explicitly synthetic CLI process verifies full binding,
one successful save, repeat rejection and STOP killing its owned process.
Live owner MCP execution is recorded separately in evidence. Old failed
request remains UNKNOWN, its unstarted batch was cancelled with attempt=0;
the subsequent successful acceptance is a different batch and different nonce.
Gate1 remains DENY; D2, old Source RPC authority and old enrollments are unchanged.
