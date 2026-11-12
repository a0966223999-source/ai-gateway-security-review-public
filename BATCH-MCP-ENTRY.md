# Deer batch tools on the existing MCP entry

This candidate adds an opt-in mount for the original `batch_task`, `batch_status`
and `cancel_batch` tools to the existing FastMCP service. It creates no new
service, Redis instance, task store, grant or native dispatch implementation.

## Verified source

- Repository: `a0966223999-source/ai-gateway-security-review-public`
- Base branch: `test/vercel-fastapi-protected-preview`
- Base commit: `5e0a589a31336ef9a5b619681c8ef5500d1d971d`
- Vercel project: `prj_NcyrYFsWwdbyhRc4MtmO0BlgI24W`
- Deployment inspected: `dpl_AXm7XwVxoeyck6PvRDzaRmEGNqCW`

The earlier 403 from a different project does not establish a permission blocker
for this service. The original deployment and its pinned source were readable.

## Integration contract and remaining work

`mcp_bridge.install` accepts an optional trusted `batch_scope_resolver` callback.
The existing application does not pass one, so the default tool catalog remains
unchanged. This change alone does **not** enable batch tools in ChatGPT.

The original host must supply an async resolver with arguments
`(ctx, parent_id, operation_key, action)` and return `OriginalBatchScope`:

- Authenticate the actual caller using the existing OAuth context. The wrapper
  checks the existing principal before and after resolution; an OAuth subject
  alone does not identify an individual Work or grant task authority.
- Resolve the current parent task, version and grant to its original Holder,
  original bound tool triple, runtime and original `BatchTaskItem` class.
- Resolve a stable original `tool_call_id` for the operation key. Enforce original
  ownership and deduplication rules, including batch ownership on reads/cancels.
- Supply a live `is_current` check for authority changes and revocation. The
  original Holder must enforce mutation authority atomically; wrapper checks
  before and after a call do not replace that enforcement.

No production implementation of that resolver has been verified or substituted
by this candidate. Test resolvers are fixtures only. Do not enable using a
constant authority check, model-supplied runtime, mailbox claim or synthetic ACK.
Unknown mutation outcomes return an error directing callers to query the original
key; the adapter does not retry them.

Once the original host contract is satisfied, inject its resolver into the
existing `install` call, deploy the existing service and refresh the existing
ChatGPT connection. Then verify all three advertised schemas and a bounded
authorized submit/status/cancel flow with real original receipts. These live
activation steps have not been performed.

## Validation

`PYTHONPATH=.deps:. python3 -m unittest discover -s tests -v`

16 tests passed: 10 adapter tests, 2 original HTTP/OAuth mounting tests, 3 original
mount regression tests and 1 original schema test. The tests use the real MCP
SDK and HTTP authentication middleware, with fixtures for original batch
authority and execution. They do not prove live Holder binding or native dispatch.

The first original-mount test attempt stopped because the local `redis` Python
package was missing. Installing the source-pinned `redis==6.4.0` into the local
`.deps` test directory allowed the final suite to pass. No real Redis endpoint,
credential, model request or native Work dispatch was used.
