# Fresh Deer batch host

The owner requested a newly constructed host after the original private host
state could not be located. This mode creates a new batch-only generation and
a new service parent. It does not recover or impersonate an old Work, grant,
Source, signer, native caller or acknowledgement.

The existing OAuth deployment configuration supplies the allowed account.
Only that account and the enrolled new parent can call this generation. The
parent is a service task ID, **not** a verified ChatGPT Work/thread identity.
The host has bounded expiry and a durable STOP file. The default MCP service
does not advertise these tools without explicit configuration.

## Data and execution path

Existing authenticated MCP -> signed batch RPC -> existing Redis/mailbox
namespace -> one fresh batch worker -> original `bind_batch_tools` -> original
`HolderBoundBatchSubmitter` -> existing Gateway `SubagentBatchRepository`.
Host replies use a new Ed25519 signer pinned at the frontend. The original
Source RPC signer, subscription and historical UNKNOWN requests are untouched.
Auxiliary Redis request/reply keys are transport records, not batch state.

The tools record pending items, read saved progress and cancel unstarted work.
They do not dispatch native agents, wake Works, grant further delegation or
prove in-flight stop. Every response retains `automatic_dispatch=false` and
`native_execution_verified=false`. There is no model/API charge in this path.

`batch_receipt` queries an original submit/cancel operation when a response is
uncertain. It never enqueues. Requests and local mutation-attempt fences are
retained without automatic expiry/replay. Same-key different payloads conflict.
A popped request whose worker crashed remains unknown; no automatic requeue or
retry occurs. Recovery of that state is a separate read-only reconciliation.

## Bootstrap and operation

1. Install the pinned code alongside the existing Gateway environment. Verify
   the existing database has the original batch tables; do not create another
   database or run migrations through this worker.
2. Derive the owner from the **existing** authenticated OAuth issuer/allowed
   subject using the same hash as `mcp_bridge.principal`.
3. Run `fresh_batch_host.py initialize --root <private persistent directory>
   --owner <derived owner> --ttl-seconds 3600`. Initialization refuses any
   existing directory. It creates real new keys, host config and attempt storage.
4. Deliver the existing Redis connection to `redis.url` in that directory with
   mode 0600. Do not place credentials or private keys in Git, reports or chats.
5. Configure the existing MCP project with `XJ_FRESH_BATCH_HOST_CONFIG` equal to
   the non-secret host.json, `XJ_FRESH_BATCH_INGRESS_KEY` equal to ingress.key hex,
   and `XJ_FRESH_BATCH_HOST_ENABLED=true` for this generation. Keep private
   signing.key on the host. Do not enable both local and remote batch modes.
6. Run `fresh_batch_worker.py --root <host> --gateway-backend <existing backend>
   --database <existing Gateway sqlite path>` using the existing Python runtime.
7. Verify actual OAuth tools/list, submit/receipt/status/cancel against the same
   new generation. A Preview build or local test is not that live verification.

Stop with `fresh_batch_host.py stop --root <host>`. The worker checks STOP before
each call and the original Holder checks during calls. A previously committed
operation is not rolled back by STOP. Disable this generation's frontend
configuration and read back for service-wide shutdown. Stopping a Codespace
also stops its worker; it does not make it a 24-hour production host.

## Validation boundary

The local suite includes real file permissions, Ed25519 signing, restart
fences, STOP, expiry, modified config, wrong owner/parent/generation, altered MAC,
and exclusive worker lock tests. Original-tool boundary tests still use disclosed
fixtures. These results do not establish live deployment or native dispatch.
