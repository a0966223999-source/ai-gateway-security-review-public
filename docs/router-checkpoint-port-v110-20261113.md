# Router checkpoint continuation port v1.0.10 — 2026-11-13 Asia/Taipei

Private shared plugin updated in place; App/MCP/OAuth/audience/defaultPrompt unchanged.
Release: pluginrel_6af5eeb449748191aca02bf476fab218.
URL: https://chatgpt.com/plugins/plugin_asdk_app_6af05e97756c8191affb9700b02418e7
Original DR Bot references/license, functions_producer_v2 and Codex stop workflow retained.
All 8 packaged files independently read back; support content preserved.

## Implementation
router-s61-restored/router_checkpoint_port.mjs is a CURRENT-WORK client seam for
original gateway_checkpoint_read/save/receipt. It creates no Source, queue, service,
native caller, timer or grant. The current Work deliberately invokes it; loading
the plugin does not schedule or execute work. No new server tool or HTTP client.
It is not the S61 Engine/Registry/Fiber startup assembly or native-start/completion adapter.

- Capture original tool callbacks and exact session-approved link/thread scope.
- Read current full checkpoint through original Gateway checks.
- Reject wrong source/scope/revision, elevated native authority, expired snapshots,
  historical_only/authority=false and absent payload as usable current evidence.
- CAS-save exact result under original revision. Concurrent same-key save only once;
  transport failure/UNKNOWN only queries the original key. Durable fence stays server-side.
- Independently re-read full original result and revision before reporting checkpoint success.
- No ACK or dispatch capability in the port. Original Receiver still owns lease and
  event/result fence before original transport ACK. Turn IDs are not required for
  the accepted notification test; transport completion remains separate from business/QA ACK.
- A notice is only a locating hint. Saved task text is data, never system authority.
- Existing bindings/Source/grants are not renewed, transferred or adopted.

## Verification
Node24.22.1: 9 focused offline transport tests PASS. These use explicit synthetic
responses, not production authority. Cases cover historical/expiry/scope denial,
CAS, concurrent unknown one-send, query-only recovery, independent result readback,
no fabricated ACK and immutable callback capture.
Initial test expected undefined checkpoint to behave as an omitted JSON key; corrected
the fixture to omit the key, preserving fail-closed handling of malformed data.
Actual Work V8 first lacked structuredClone; replaced it with strict JSON cloning.
Final 9 tests rerun PASS.
Actual current Work constructed the client with REAL available read/save/receipt tool
functions: PASS, tool calls=0. This verifies callable wiring only, not live Source authority.

## Actual existing live prerequisites
Hub #6829314463 already proves original MCP checkpoint create/read/CAS for that
earlier test. Hub #6829898465 proves original notification result and transport ACK
plus independent completed read. Reuse those results; do not replay old events.

The last published account link expired at epoch1794500484.1489644 (00:21:24 Taipei).
During this work current time exceeded1794502258. No expiry extension or 403 re-probe.
New integration live write/read is NOT_RUN: it requires a newly session-approved
original 10-minute link, its exact thread, and a new bounded checkpoint.
Old Source cannot be adopted under a new link. Do not run or re-ACK old tests.
Gateway page: https://fantastic-space-system-vprp5995g5q43xgv4-8001.app.github.dev/api/v1/auth/continuation-link

No production Gateway/worker change or restart, no auto dispatch, no new notification,
claim or ACK this turn. S61 native Caller/dispatch assembly remains NOT_BOUND.
Gate1 DENY; D2 untouched.

## Next live check after new link
Use the original approved test thread and exact new link. Confirm current host/link;
create one NEW nonsensitive checkpoint with original MCP; use this port read/save/confirm;
preserve raw requests/receipts. UNKNOWN queries only. Only a new explicitly authorized
notification test would use the original Receiver/lease/ACK afterwards.
