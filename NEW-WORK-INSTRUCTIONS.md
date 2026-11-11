# Explicit cloud/native target routing

These files alone do not establish a live deployment. Verify the final release commit, matching host pins, and the refreshed tool schema before use. The same official mailbox tool accepts optional strings `target_locator_json` or `self_report_json`, never both, separate from untouched canonical `request_json`. They are allowed only for registration. No private runtime installation is needed in ordinary Work.

## Select a route from an official reference

- A task created by `thread_management.create_thread`: use its exact official `threadId` and explicit `cloud-task` route. Official `read_thread` must omit `environmentId` on every page.
- A user-created native chat: use `threadId` and the connected computer's `environmentId` from its official chat reference, with explicit `native-chat` route. Keep that same environment on every page.
- Existing v2 locators retain their original native-chat semantics and required environment. They do not turn into cloud locators.

A runtime `environment_context` ID is not a registered-computer reference. A `hostId` is a desktop navigation label. Missing environment, a cloud computer, copied text, or `source_thread_id` does not select or prove the route. The targeted-locator path requires an official route/reference before submission; do not guess. The separate self-report input below sends only observations for the original host to resolve and verify.

The two v3 schemas are exactly:

```json
{"kind":"ordinary-work","route":"cloud-task","threadId":"<official task ID>","v":3}
{"environmentId":"<official computer reference>","kind":"ordinary-work","route":"native-chat","threadId":"<official chat ID>","v":3}
```

Cloud locators reject `environmentId`, including null or an empty string. Native locators require it. IDs are nonblank strings, at most 256 UTF-8 bytes and without control characters. Unknown route, version, or extra fields are rejected.

## Producer usage

### Prepare the Work's self-report without submitting

At Work admission, collect only `CODEX_THREAD_ID` and `CODEX_ENVIRONMENT_ID` with
`collect_work_self_report.py` in that Work's own execution environment. Never dump
the environment or copy secrets. These are runtime observations, not an official
computer reference, a route, a turn admission receipt, or permission.

The same producer module exports `prepareWorkSelfReport`,
`prepareTargetedRegistration`, and explicit `submitSelfReportedRegistration`.
Preparation is local and makes zero tool calls.
Keep the Work's original, never-submitted canonical registration request bytes.
Do not substitute an empty request, invent an authorized Source, mint another
nonce for an unknown request, or treat the report as a grant.

```js
const report = prepareWorkSelfReport({runtimeIdentifiers, requestJson});
if (report.status !== 'SELF_REPORT_PREPARED_UNVERIFIED') {
  // Return the structured issues to the caller. Do not continue or submit.
  return report;
}
// route remains UNRESOLVED; identity and authorization remain NOT_VERIFIED.

// Explicit submission, only when requested. Uses the original RPC exactly once.
const outcome = await submitSelfReportedRegistration({
  tools, requestJson: report.requestJson, selfReport: report.selfReport,
});
// UNKNOWN_QUERY_ONLY stays unchanged; query the original nonce, never resubmit.
```

This input uses the original `gateway_source_rpc_request` and original mailbox,
not a second service. The exact canonical `self_report_json` is stored alongside
the unchanged canonical request on the same event. It contains only `v: 1`,
`kind: "work-runtime-hints"`, and `observations` with required `CODEX_THREAD_ID`
and optional `CODEX_ENVIRONMENT_ID`. Reports are at most 2048 UTF-8 bytes; each
identifier is nonblank, at most 256 bytes, and has no control characters.
Unknown fields, duplicate keys, noncanonical JSON, simultaneous locator/report,
and sidebands on read/save requests are rejected. The original RPC read returns
`selfReport` and `authority: false`. No tool listing or preparation result proves
that the deployed host supports this path or can resolve the reported Work.

The privileged host must obtain the official route and reference through its
supported protected context, then use its existing native read, consent, Source,
and admission checks. The report supplies no route, native identity, or grant.
Unresolved or denied official access remains blocked. This public transport
contains no resolver and does not change the host's configuration.

If the producer already has a separate official reference, it can instead use
the existing targeted preparation and submission path:

```js

const preparation = prepareTargetedRegistration({
  tools, requestJson: report.requestJson, selfReport: report.selfReport,
  referenceKind, officialReference,
});
```

The common entry must obtain `referenceKind` and `officialReference` from a
supported platform context or an existing authoritative mapping for that exact
Work. For tasks it actually creates, the creating host can retain its official
`create_thread` result and pass that result automatically. This is not a way to
reclassify or replace a user-created Work. There is no implemented generic
current-Work resolver in this candidate. The runtime self-report cannot fill that
gap by asserting its own route or computer mapping.

Missing reference and request are reported together in `issues`. The sole
successful preparation status is `INPUTS_PREPARED_NOT_AUTHORIZED`, never READY or
PASS. It checks shapes and exact input bytes only. The real tool schema,
reference provenance, target accessibility, original current USER consent,
native plan/request declarations and Source fences are not established by it.
The original host must verify these under its existing current authority before
registering or saving a continuation checkpoint. If its official read was
denied, self-report is not new access permission and cannot cause another-route
retry. No preparation result is final live acceptance.

Manual chat mention can be a supported one-off way to supply a reference, but is
not the automated common-entry solution and is not required by this design.
If supported host resolution cannot obtain official context for a Work, retain
the exact missing capability as blocked. Do not send a separate business event,
scan other accounts, or probe alternative routes to discover a target.

### Submit only after preparation and existing authorization conditions

Use the existing `functions_producer_v2.mjs` compatibility module, which now exports explicit typed constructors. In a JavaScript module with the real official `tools` binding:

```js
import {targetLocatorFromCloudTaskReference, targetLocatorFromNativeChatReference,
  submitTargetedRegistration} from './functions_producer_v2.mjs';

// Choose exactly one constructor using an actual official reference.
const targetLocator = targetLocatorFromCloudTaskReference(officialCreateThreadResult);
// For an official user-created native-chat reference instead:
// const targetLocator = targetLocatorFromNativeChatReference(officialChatReference);

const outcome = await submitTargetedRegistration({
  tools, requestJson: originalCanonicalRequestJson, targetLocator,
});
```

For `functions.exec`, include the published helper declarations with `export` removed, then the selected usage. Do not execute submission while preparing the cell. A missing route/reference or missing official request tool is a blocker, not permission for a workaround.

Use only a current registration request that has never been submitted. Keep its original canonical bytes and nonce. The host preserves the original signed 80-second RPC deadline and 20-second native preparation bound.

## Authority and uncertain outcomes

A locator is a search address, never proof of invocation origin, task creation lineage, original user authorship, consent, or Source authority. The exact successful official read establishes target accessibility only. The original host still requires the current complete turn, exact `SOURCE_RPC_PLAN` / `SOURCE_RPC_REQUEST` declarations, genuine original USER authorization for the precise task and registration/activation/self-wake scope, unchanged USER evidence, and the existing Source/global-once fences. Copied/delegated prompts and USER role labels alone are not consent.

FORBIDDEN, CONFLICT, missing target, timeout, UNKNOWN, or any other read error stops. Never retry by stripping the environment, changing route, using another tool, changing nonce, or extending the deadline. The entire locator or self-report is immutable with the original request/nonce/event. Changing, removing, adding, or replacing the sideband on that nonce conflicts without dispatch. Return `UNKNOWN_QUERY_ONLY` unchanged and use only the existing official query path for uncertain outcomes. Query only reads retained state; it cannot dispatch or resolve a new target.

The official send tool retains `threadId`/`prompt` arguments, without `environmentId`. Its preceding fresh read uses the exact pinned route; a successful read does not promise permission to send.

The user personally performs the final live business trial. Offline fixtures and release readback do not count as that acceptance test.
