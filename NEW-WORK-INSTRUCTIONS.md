# New Work: targeted registration v2

## Prerequisite

These files alone do not establish a live deployment. The official mailbox MCP server must have the matching version installed and the connector tool schema refreshed. Before the user runs the one final trial, verify the discovered schema for `mcp__codex_apps__x_redis_test_gateway_source_rpc_request` includes optional string `target_locator_json`. An old schema cannot transport the target. Do not fall back to a different tool, a direct Redis write, or adding target fields to `request_json`.

## Inputs

Use the original exact canonical registration `request_json` and its original request ID. Obtain the target `threadId` and `environmentId` from the verified official chat reference supplied to the producer context. Do not guess IDs, derive an environment from a thread ID, copy an unrelated default environment, or substitute the current host environment. If no reference is available, stop with `LOCATOR_REQUIRED`; if the environment reference is absent, stop with `ENVIRONMENT_REFERENCE_REQUIRED`. Neither case invokes MCP.

The locator schema is exactly:

```json
{"environmentId":"<verified official environment ID>","kind":"ordinary-work","threadId":"<verified official thread ID>","v":2}
```

Both IDs must be nonblank strings, contain no control characters, and fit within 256 UTF-8 bytes. Only `register` accepts a target locator. The locator is metadata separate from the canonical request and is not proof of the caller's origin or runtime authority.

## Producer usage

`functions_producer_v2.mjs` exports the helper and has no side effects on import. In a JavaScript module producer with the official `tools` binding:

```js
import {targetLocatorFromOfficialReference, submitTargetedRegistration}
  from './functions_producer_v2.mjs';

// These two values must already come from the authorized producer context.
// originalRequestJson is not reconstructed or augmented by the helper.
const targetLocator = targetLocatorFromOfficialReference(verifiedOfficialChatReference);
const result = await submitTargetedRegistration({
  tools,
  requestJson: originalRequestJson,
  targetLocator,
});
```

For a `functions.exec` invocation without local-module imports, paste the helper's declarations into the execution cell with `export` removed, then append the same usage with the already verified inputs. Do not run the final call while preparing the cell. The user alone runs the final trial.

The helper calls only the official request tool, once, with `mailbox_id`, untouched `request_json`, and canonical `target_locator_json`. A missing official tool is a blocker, not permission for a workaround.

## Outcome handling

Return `UNKNOWN_QUERY_ONLY` unchanged. Do not repeat registration, generate a new nonce, alter the target, or extend the request deadline. An exception after invocation also does not authorize retry. The parent can query the existing official request outcome using the original request ID. Same-operation locator conflicts are rejected atomically; the unchanged legacy wait transport reports them as query-only outcomes.

## Offline validation

Run `node --test tests/test_functions_producer_v2.mjs` from this directory. Six tests cover missing-input no-call behavior, preservation of reference and exact request bytes, strict locator validation, query-only no retry, and uncertain-failure no retry. No live MCP invocation is part of these tests.

Use this path only for a current registration request that has never been submitted. Do not resubmit any prior UNKNOWN request or event, and never create a new nonce to bypass an uncertain outcome. The user personally performs the final live business trial; operators run only offline checks and authorized release validation.
