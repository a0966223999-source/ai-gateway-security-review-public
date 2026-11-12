# Original-mailbox control notifications

This extension mounts four tools on the existing FastMCP service:

- gateway_cycle_begin: admit an authenticated delivery into one control round.
- gateway_cycle_observe: accept a fresh original-host-signed observation after 80 seconds.
- gateway_cycle_publish: send at most one successor to the existing subscription.
- gateway_cycle_query: read the saved outcome without retrying a mutation.

When explicitly enabled, service discovery is 25 tools (10 read, 15 write),
preserving all original 21 tool definitions. These counts were verified with
local FastAPI/FastMCP discovery, not a claim of live deployment.

State stays in the original owner's Redis mailbox JSON key. Business messages,
operation IDs, payloads, leases and original RPC data remain unchanged. Control
writes preserve TTL. Only naturally expired control records whose original
messages are also expired/missing are reclaimed. Valid and unknown operations
are not deleted to permit retry.

## Host protocol and control flow

Every mutation requires a fresh fixed-host Ed25519 proof in domain
`gateway-cycle-control-v1`, binding action, owner, mailbox, logical event,
generation, current Source/base/task scope, exact native watch and authority.
Ordinary OAuth or event text alone cannot create that proof. The original
SourceRPC signature domain and signed 80-second deadline are unchanged.

Each separate control round waits 80 seconds, then reads the exact bound native
turn. Active arms one next generation and publishes it once before returning.
It does not read task results on the active branch or start another Work.
After a legitimate original admission, observation uses the Source's exact
stored started platform receipt to watch the resumed turn, without re-sending,
claiming, or resetting the original once fence.

Only an ended native turn allows verification of the original same-mailbox
rpc_request/rpc_response and original AgentResult. COMPLETED with no next step
can stop control notifications as verified agent-reported completion. FAILED
requires review. IN_PROGRESS requires the original authorized continuation
path. Missing/conflicting/unverified results stop safely as unknown. Native
ended, webhook 2xx, transport ACK and person ACK are never task completion.
Business acceptance and parent-close permission remain false.

The original AgentResult fields remain status, next_step, context_data and
final_output. The extension does not create a replacement result-message format.
A host-signed attestation and the existing signed RPC pair are both required;
Source-only status cannot substitute for the submitted message.

## Notification identity and concurrency

The optional v2 discovery schema separates transport notification_id/eventId
from the immutable logical_event_id. The legacy schema and equality guard stay
unchanged. Outbound cursor is null; verified inbound delivery may omit cursor
or supply null, but no other value or unexpected fields is accepted.

A trusted platform delivery, verified and signed by the fixed host, can admit
its exact next generation while the one callback attempt is still sending.
Late callback completion updates only historical delivery state, never a newer
active round. Initial admission likewise requires an existing delivered or
currently locked sending original delivery. Pending/armed/unknown records are
not fabricated into received notifications. A native scheduler completion
barrier is not claimed: a new notification may arrive before the old native
turn returns. Redis CAS ensures only one logical owner and successor.

All uncertain callback or mutation outcomes are query-only, with no automatic
retry. A new nonce, event, job or Source save cannot be used to escape an unknown
outcome. No scheduler, cron, extra Redis database or subscription is created.

## Deployment-owned configuration

Disabled by default. The existing fixed public host key is reused, never a
private signing key. These settings must be supplied through deployment setup,
not a request, event or source-code constant:

- XJ_GENERATION_ENABLED=true
- XJ_GENERATION_SUBSCRIPTION_ID: the verified existing subscription.
- XJ_GENERATION_ROUND_BUDGET_SECONDS: explicit integer 85..180.
- XJ_GENERATION_MAX_GENERATIONS: explicit integer 2..100 successors.
- XJ_GENERATION_MIN_INTERVAL_SECONDS: explicit integer 80..3600.

The matching private original-host policy must agree exactly. Missing or
invalid configuration leaves the original service enabled and this extension
disabled. The original XJ_SOURCE_RPC_HOST_PUBLIC_KEY is pinned at setup.

All rounds and any separate original Receiver phase remain clamped by original
Source/base/event/authority/subscription expiry and STOP. The control wait is
not part of or an extension to a SourceRPC request. Cost or time limits produce
a safety pause, not task completion. No delayed wake is invented if a configured
minimum interval is longer than the elapsed 80 seconds.

## Verification boundary

Local tests cover Lua CAS, single-flight generations, delivery races,
post-started read-only watch, same-event uncertainty, expiry, capacity cleanup,
original result verification and real local tool discovery. Temporary test
keys, generated subscription IDs, fake callbacks and native observations are
used. No live event, Work, Redis mutation or subscription update is a test
prerequisite. Live platform acceptance and actual new-turn behavior must be
verified separately before claiming end-to-end operation.

Run the public control subset with the existing project dependencies plus
fakeredis[lua]:

`python -m unittest discover -s tests/generation_control -v`
