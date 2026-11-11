# Explicit cloud/native target routing

These files alone do not establish a live deployment. Verify the final release commit, matching host pins, and the refreshed tool schema before use. The same official mailbox tool accepts optional string `target_locator_json`, separate from untouched canonical `request_json`. No private runtime installation is needed in ordinary Work.

## Select a route from an official reference

- A task created by `thread_management.create_thread`: use its exact official `threadId` and explicit `cloud-task` route. Official `read_thread` must omit `environmentId` on every page.
- A user-created native chat: use `threadId` and the connected computer's `environmentId` from its official chat reference, with explicit `native-chat` route. Keep that same environment on every page.
- Existing v2 locators retain their original native-chat semantics and required environment. They do not turn into cloud locators.

A runtime `environment_context` ID is not a registered-computer reference. A `hostId` is a desktop navigation label. Missing environment, a cloud computer, copied text, or `source_thread_id` does not select or prove the route. If the official route/reference is unavailable, stop before submitting; do not guess.

The two v3 schemas are exactly:

```json
{"kind":"ordinary-work","route":"cloud-task","threadId":"<official task ID>","v":3}
{"environmentId":"<official computer reference>","kind":"ordinary-work","route":"native-chat","threadId":"<official chat ID>","v":3}
```

Cloud locators reject `environmentId`, including null or an empty string. Native locators require it. IDs are nonblank strings, at most 256 UTF-8 bytes and without control characters. Unknown route, version, or extra fields are rejected.

## Producer usage

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

FORBIDDEN, CONFLICT, missing target, timeout, UNKNOWN, or any other read error stops. Never retry by stripping the environment, changing route, using another tool, changing nonce, or extending the deadline. The entire locator is immutable with the original request/nonce/event. Return `UNKNOWN_QUERY_ONLY` unchanged and use only the existing official query path for uncertain outcomes.

The official send tool retains `threadId`/`prompt` arguments, without `environmentId`. Its preceding fresh read uses the exact pinned route; a successful read does not promise permission to send.

The user personally performs the final live business trial. Offline fixtures and release readback do not count as that acceptance test.
