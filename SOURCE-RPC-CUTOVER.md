# Source RPC signer cutover preparation

This opt-in addition belongs to the existing X Redis Test backend, on its
existing FastMCP instance, Redis owner namespace and `gateway_internal_v1`.
It creates no new tool, service, database, key, Source, grant or subscription.
The default single-key behavior remains unchanged when no cutover policy is set.

## Why this change is needed

The original service has one pinned host public key. Replacing that key would
make old saved responses unverifiable. Simply trusting both keys would also
permit the old signer to answer new work. The replacement-host staging tool
alone cannot solve this public-backend trust problem.

The new verifier has one active public key and a bounded, exact allowlist of
already saved historical response envelopes. The active public key still comes
from `XJ_SOURCE_RPC_HOST_PUBLIC_KEY`. The optional, public-only deployment
setting `XJ_SOURCE_RPC_CUTOVER_POLICY` supplies this exact JSON schema:

```text
schema: "source-rpc-cutover-v1"
mailbox_id: "gateway_internal_v1"
cutover_epoch: positive integer Unix second
legacy_public_key: previous Ed25519 public key as 64 lowercase hex characters
historical_replies: zero to 100 records with event_id and envelope_sha256
```

`envelope_sha256` hashes UTF-8 `transport.canonical(saved_envelope)` bytes.
Freeze the actual already saved envelopes from the original authenticated
mailbox before cutover, and verify their signatures and request bindings. Do not
add UNKNOWNs or absent replies to this list. The policy input is trusted
deployment configuration, never event payload, model hints or self-reported
Work IDs. This patch does not retrieve or assert provenance of that inventory.
An empty list is valid and authorizes no historical response verification.

## Behavior

* Events before the cutover second: only the original query tool can verify an
  exact frozen response using the old key. It returns
  `verified_historical_host_response`, `historical_only=true`, `authority=false`.
  This result must never be treated as a new grant or current continuation.
* Missing old response: `UNKNOWN_QUERY_ONLY`; no send, extension, claim or ACK.
* Old events cannot be read as live host input or committed to, even with the
  new private signer. New-host migration must not adopt old requests.
* Events at or after cutover: only the active key verifies. The old key is never
  tried as fallback, including query. Routing uses the original Redis event's
  `created_at` metadata, not a timestamp inside its message payload.
* Commit repeats the event-time boundary inside the original atomic Lua
  transition, before SET/PUBLISH. The original request/deadline/nonce guards
  remain in place. Missing, fractional or future event times are refused.
* Policy/key changes revoke installed sessions. Invalid or future policies
  disable only RPC installation; existing notification tools remain installed.

## Operational boundary

This is code preparation, **not** a deployed replacement host or approved
cutover. No production policy values are provided by the tests. The earlier
staging candidate remains STOPped/expired and does not generate private keys.

Before any actual cutover, verify the replacement's existing persistent runtime,
identity, original Source/finalizer and lawful provider connections. Prepare
the reviewed new public key, frozen original history inventory, exact source
and configuration pins, and stop/revocation readback. Stop admission and the old
host's new effects first. Account for Redis TIME versus host clock and requests
created in the boundary second; preserve uncertainty as query-only. Update the
new public key and cutover policy together during the stopped maintenance
window. Confirm old live capability is rejected before enabling new admission.
Do not activate overlapping authorities. Generation-control/Holder/native
Caller cutover requires its own original evidence; this patch grants none.

On failure remain stopped. Do not remove the policy or restore old live trust as
an automatic rollback, and do not disable persistent notification subscriptions.

## Validation

New tests use actual ephemeral Ed25519 signatures and the original MCP tool
functions with a disclosed read-only record fixture. Atomic predicate tests
execute the production COMMIT Lua in system Lua 5.4 with disclosed Redis/cjson
fixtures. Neither is live Redis or native Work continuation evidence.
The existing full Redis integration suite requires its original
`requirements-dev.txt` dependencies. Run it in the existing authorized test
environment before merge/deployment; it was unavailable here because redis and
fakeredis were not installed. No dependency constraints were changed.
