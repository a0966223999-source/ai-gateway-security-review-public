# Deer Library original host native provision | fail-closed step

## Exact problem

PR #7 installed five MCP contracts but the public Vercel `app.py` had no
`library_catalog_scope_resolver` or `library_write_scope_resolver` from
the original Source/Reader/Writer. "READY" and an Auth0 OAuth login are
not proof of file access. The previously signed BatchRPC and summary modes
also cannot be turned into a Library write authority.

## This increment

- Add `OriginalLibraryHostBinding`: a frozen *in-process* selector that
  accepts only an exact original-source Reader/Writer resolver and current
  original host lifecycle.
- Vercel `app.py` checks only `app.state.original_library_host_binding`,
  installed by trusted startup code. A user prompt, the OAuth `azp`, an
  environment variable, fake status envelope or arbitrary web request cannot
  create this binding. Absent/malformed = no Library read/write tools.
- Enforce current host lifecycle together with source-provided, client-specific
  ACLs on each call, before and after I/O.
- Authenticated `library_bridge_status` returns no content or identities.
  `NOT_BOUND` is the expected default. `RESOLVER_REGISTERED_NOT_LIVE_VERIFIED`
  does **not** claim real-file E2E.
- No new Issuer/Store/token, no G0/Gate1, no Worker restart, no SQL/D2
  changes, and no Alias reassignment.

## Critical host/source boundary

The trusted original Gateway runs separately from the Vercel MCP.
A Python object in the original Codespace does NOT automatically appear
inside Vercel's `app.state`. This code implements a verified *selection
seam*, **not** cross-host delivery.

To make actual data available, a trusted source producer must be connected
to the original host and return a source- and client-scoped capability.

- For native ChatGPT Library, ordinary accounts have no confirmed public
  external service API for server-side whole-Library browse/read/write.
  ChatGPT's authorized Files tools work **inside ChatGPT**. A possible
  user-mediated plugin UI file picker can authorize individual selections,
  but not silent unrestricted enumeration or in-place CAS writes.
- For Drive/Dropbox, use **each provider's authorized source connector** with
  independent credentials and scopes. The Grok-side already connected
  backup token is NOT a credential that Deer may borrow.
- For the original source-to-MCP hop, add a separate, host-verified
  Library RPC mode and signed reply using existing trusted channel ownership;
  no library mode is currently registered on BatchRPC and none is enabled
  by this PR.
- Each `update` must require true source-native expected-version CAS and
  same-operation-key readback, or a verified original serialized Writer.
  If absent, DENY. Create only under exact original parent grant.
- Each provider must prove current owner, OAuth client, operation, ref,
  source version and revocation. A signed transport ACK is not proof of
  Library rights.

## Acceptance gates

1. Offline tests: invalid port, absent port, wrong source, stale host,
   cross-client read denial, status no secret leakage, original MCP intact.
2. Source-specific independent QA with real non-sensitive canary: list,
   read, create, native version-pinned update, receipt, revoke and second read.
3. Verify no true file content copied into public GitHub.
4. Separately approve deployment and alias cutover; verify with same exact
   commit SHA, current source binding and rollback receipt.

Until step 2 is confirmed, report `LIBRARY_E2E=NOT_BOUND` even if code
merged and a fresh Vercel deployment is READY.
