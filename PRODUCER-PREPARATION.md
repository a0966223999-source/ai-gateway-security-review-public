# Work self-report transport and preparation

This local candidate extends the original Source RPC tool with optional
`self_report_json`. It does not create a service, resolver, route, host identity,
grant, Source mapping, or new credentials. Publication and live acceptance are
separate work; this candidate alone establishes neither.

## Public API

- `collect_work_self_report.py` reads only `CODEX_THREAD_ID` and `CODEX_ENVIRONMENT_ID` in the reporting Work's own environment.
- `prepareWorkSelfReport({runtimeIdentifiers, requestJson})` prepares allowlisted observations and unchanged original canonical register request bytes without tool calls.
- `canonicalWorkSelfReport(selfReport)` strictly validates the report object and serializes its canonical transport bytes.
- `submitSelfReportedRegistration({tools, requestJson, selfReport})` explicitly makes at most one existing `gateway_source_rpc_request` call with `mailbox_id`, unchanged `request_json`, and `self_report_json`. It does not retry or convert UNKNOWN into success.
- The existing `prepareTargetedRegistration` and `submitTargetedRegistration` path remains available when a separate verified official reference is already supplied.
- Python `self_report.validate_self_report(value)` and `self_report.parse_self_report(raw, method='register')` implement the exact schema. `parse_registration_sidebands` enforces mutual exclusion.

## Exact sideband contract

The sole report shape is `{v:1,kind:"work-runtime-hints",observations:{CODEX_THREAD_ID,optional CODEX_ENVIRONMENT_ID}}`.
No route, grant, identity, authority, or other keys are permitted. The report must
be exact sorted compact UTF-8 JSON, at most 2048 bytes, without duplicate fields
or nonfinite values. Identifiers are nonblank strings, at most 256 UTF-8 bytes,
without control characters or invalid Unicode.

`target_locator_json` and `self_report_json` are mutually exclusive and both are
register-only. Empty optional strings preserve the old omitted-field behavior.
Stored empty reports are invalid. Python and the original atomic mailbox Lua
validate the new report before storage. No request bytes or request SHA256 are
changed to include a sideband.

The existing event retains exact `rpc_self_report` bytes alongside its original
request, nonce, deadline, and reserved query-only state. Any changed, removed,
added, or replaced sideband on the same nonce conflicts. Duplicate requests do
not extend the original deadline or redispatch. The old no-sideband and v2/v3
targeted-locator representations remain unchanged.

The original RPC read exposes `selfReport` beside `request`, `context`, and
`authority: false`. Query is only retained signed-outcome lookup and never
dispatches, resolves, extends, or grants anything. The existing bounded RPC maps
uncertain post-submit outcomes to `UNKNOWN_QUERY_ONLY`; callers retain the
original nonce for query and do not retry under a replacement nonce.

## Host boundary and remaining work

The protected original host must resolve these hints using supported official
context, read the exact target through the authorized route, and enforce the
existing complete native evidence review, genuine original USER consent,
registration/activation/self-wake scope, Source CAS, and global-once fences.
Reports cannot construct identity or substitute for that review. This public
candidate contains no made-up generic resolver or configuration mutation.
Denied access remains terminal without materially new relevant access evidence.
No alternate route, replacement Work, account scan, or discovery event is added.

Callable tool presence and offline schema generation do not prove the deployed
schema is refreshed or the protected host can resolve a particular Work. The
user personally performs the final live business trial after integration.

## Offline verification

Run from this directory with the project's existing test dependencies:

```sh
node --check functions_producer_v2.mjs
node --test tests/*.mjs
python3 -B -m unittest discover -s tests -v
```

Tests cover exact request/hash preservation, strict report keys, size, Unicode,
duplicate/noncanonical JSON, injected authority/route, direct Lua validation,
same-nonce conflicts, lost-reply replay, original-owner isolation, query-only
behavior, old request/locator compatibility, and actual local FastMCP schema.
They use synthetic fixtures, isolated fakeredis/Lua and temporary signing keys.
No live RPC, business event, Source, subscription, target read, deployment, or
final user trial is performed by these tests.
