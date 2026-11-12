# Same-service Source RPC transport candidate

Offline-tested integration files, not a claim of live deployment or end-to-end
Work continuation. Installs on the existing FastMCP service and original mailbox.
It creates no additional service, Redis instance or event store.

## Tests

Use Python 3.11 or newer. Install requirements-dev.txt into an isolated environment,
then run from this directory:

python -m unittest discover -s tests -v

Offline tests use actual FastMCP schema generation, FakeMCP and fakeredis/Lua. No external Redis or live native
host is required. Golden UTF-8 vectors and the hash-pinned public Lua baseline
are bundled; no private source directory or host configuration is imported.

## Existing MCP integration

After original install_formal_transport returns formal:

from mcp_installer import install_rpc_tools
install_rpc_tools(mcp, mailbox, formal, principal, oauth_meta, host_config=host_rpc_config)

Pass the existing instance, original mailbox factory, formal event dispatcher
and original OAuth principal. This registers request/read/respond/query tools.
Only gateway_internal_v1 is accepted. No original Inbox.run/send code is replaced;
inbox_rpc.lua is used solely by the new request submission path on the same key.
Original mailbox Lua subscription status/presence fixes are retained.

Host configuration defaults to disabled:
- budgets_verified: false until live budget evidence is reviewed.
- max_wait_seconds: proposed 30, inactive without verified budgets.
- function_budget_seconds: verified deployed function bound; no default.
- connector_budget_seconds: conservative observed/verified client bound; no default.
- diagnostic_enabled: false.
- diagnostic_max_wait_seconds: 5 by default, at most 180.

XJ_SOURCE_RPC_HOST_PUBLIC_KEY must contain the fixed host public Ed25519 key as
64 lowercase hex characters. Do not publish or configure a private key here.
The existing VERCEL_ENV and XJ_PREVIEW_REDIS_URL/REDIS_URL are reused with rediss
TLS certificate/hostname checks. No environment settings are written by these
files. Missing key/budgets disables only new RPC tools, preserving old tools.

Optional diagnostic on the same MCP instance:

from rpc_diagnostics import install_rpc_diagnostic
install_rpc_diagnostic(mcp, principal, oauth_meta, host_config=host_rpc_config)

Requires explicit diagnostic_enabled. It performs only a bounded async await,
returns elapsed time/public-key fingerprint/configured status, and does not
access Source/Redis, send events or write state. A later 45-second diagnostic
can support an observed minimum client lifetime when enabled up to 180 seconds.
It cannot establish maximum timeout, host responsiveness, or future reliability,
and never changes budgets_verified. No such long live probe was run here.

## Request and response contract

Request JSON is exact canonical UTF-8: ensure_ascii=False, sorted keys, compact
separators and no NaN/Infinity. The complete request has v/id/source/method/params;
id is a 32-byte random nonce represented by 64 lowercase hex characters. Exact
canonical bytes are limited to 65536 bytes. A native request declaration must
bind this exact nonce and SHA256 before the host performs Source actions.

The original event stores full immutable request, digest, nonce, deadline and
reserved fence atomically. Pub/Sub subscription is acknowledged before submit.
Only a newly created event dispatches a notification. Duplicate/unknown requests
never redispatch or authorize Source effects. The query tool can reconcile by
original request ID if the original event ID was not returned.

Registration optionally accepts `self_report_json` through the same existing
request tool, mutually exclusive with `target_locator_json`. Its exact canonical
shape is `{v:1,kind:"work-runtime-hints",observations:{CODEX_THREAD_ID,...}}`, with
only optional `CODEX_ENVIRONMENT_ID`. `self_report.py` validates the complete
2048-byte report and 256-byte identifiers. These observations are lookup hints,
not a route, host identity, consent or grant. The event stores exact report bytes;
same-nonce sideband changes conflict without dispatch. The existing read returns
`selfReport` with `authority: false`, and query remains read-only. See
NEW-WORK-INSTRUCTIONS.md for the explicit one-call producer helper and remaining
protected host-resolution/verification requirements.

The response is signed by a fixed host-only private key, with domain, owner,
mailbox, operation, event, nonce, request SHA and expiry bound. OAuth owner alone
cannot forge a response or grant Source authority. Pub/Sub is only a wake signal;
results are verified from the original event. Unknown sends or readback failures
retain query-only state; no polling, automatic resend, claim or ACK is added.

## Remaining live integration

Original native host must validate fresh native identity, explicit task consent,
Source mapping/CAS and original finalizer fences. Protect the signing key outside
ordinary Work access. Verify deployed function/client budgets before activation.
Original Receiver's completed/idle Work gate remains unchanged: synchronous MCP
completion while Work is active does not itself provide a later completion wake.
Do not label an enqueue receipt as successful automatic continuation.

Host integration timing note: a measured real functions V8 registration child took 26.9 seconds before discovery and semantic review. A 30-second full first-registration budget is not established. Longer read-only diagnostic measurements must pass before configuring a larger RPC budget. The default RPC wait stays 30 and Source RPC remains disabled until host verification.
