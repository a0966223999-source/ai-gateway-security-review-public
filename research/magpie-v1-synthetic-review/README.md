# Magpie Adapter V1 — synthetic, DENY-only BFF candidate

Product parent: `d4f681abf20ea3530fe82225bf79cfcc5188b2c1`.
Magpie `v0.1.1285`, tag resolves to `d783df46cbac9021e26a6bd71ca0b55544552eb2`.
Node used: `v24.19.0`; no npm dependencies added or installed.

## Files and allowed scope

Everything is under `research/magpie-adapter-v1/`: adapter, loopback-only HTTP transport, tests, historical fixtures, a Next.js route candidate, Python lifecycle harness and sanitized evidence. No existing product files, dependency manifests, lockfiles, CI, Hooks or settings change.

## Boundary

Follow PR #50/#51: Vercel BFF → existing trusted Gate/Facade → Credential Broker → Adapter → Magpie → Provider. No new identity authority. Production entrypoints deny all requests. The offline WeakMaps are test fixtures, not identity verification, durable authorization, IPC security or billing proof. PR #52 Task Inbox is not a trusted Work executor and is not wired here.

`next/app/api/model/route.ts` is a standalone candidate, **not installed in product `src/app`**. POST returns 403 before reading headers/body; GET returns 405. A forged verified flag cannot reach a provider. Node runs the Web Request/Response handler using built-in TypeScript stripping. Full Next.js build, deployed Vercel BFF, real trusted Gate integration, allowed tenant request and authorized BFF SSE streaming are **NOT RUN**. Successful synthetic HTTP/SSE tests exercise the separate loopback transport only.

## Security changes

- Reject Node Proxy inputs before reflection, including revoked proxies; reject getters without invoking them.
- Copy validated request/receipt/usage primitives before use; mutations after attestation do not change the snapshot.
- Public legacy `executeMock` stays DENY-only; caller `verified` and `usageVerified` never authorize or certify billing.
- Missing, noninteger, negative, unsafe, infinite or nonnumeric token usage remains UNKNOWN.
- Even valid synthetic usage is observed only: `usageVerified=false` / `usage_verified=false`.
- Exceptions are represented by fixed codes; production route has no logging and never echoes bodies/headers/tenant identifiers.
- HTTP transport has a fixed loopback host and mock model aliases, fixed synthetic payload, no credential input, no tools, no redirects and no retry. Duplicate test intents are consumed before sending, including UNKNOWN. This in-memory set is **not durable production idempotency**.

## Retry governance

Fixed source `internal/gateway/fallback.go:1327–1368`: `lastRetries=2`, `rateRetries=3`, initial `retryPause=1s`, exponential waits; upstream `Retry-After` can replace the wait, capped at 8s. Thus tested 429 has 4 attempts (1+3), 500 has 3 (1+2). `patience=-1` controls group replan, not direct-provider retries. No supported universal runtime disable was found; the constants are not configuration. Do not falsify errors or headers to suppress retries.

Group replan, account repair and `upstream_transport.go` POST replay introduce other retry paths. **Four is not a universal Magpie cost ceiling.** This candidate is limited to fixed direct synthetic providers and denies paid/tool/side-effect options. Before any paid connection, an existing Gate/Ledger must reserve worst-case cost for an enforceable attempt policy and receive authenticated per-attempt events; otherwise fail closed. BFF/Adapter never add another retry layer. Do not refund UNKNOWN attempts. Mock records one independent attempt/cost-UNKNOWN event per actual upstream request; adapter HTTP attempt IDs are distinct from provider attempt IDs. No production ledger is implemented.

## Reproduce locally

Use the official Linux amd64 CLI, SHA256:
`6e6ef07a8faa1fb2ece151a38a276468c114a0d3e94476d7ac2fac0c37f65ce3`.
Official asset: https://github.com/yetone/magpie/releases/download/v0.1.1285/magpie-cli-linux-amd64
Do not use credentials. Confirm the binary hash and Node 24 before running:

```sh
node --test fixtures/original/adapter.test.mjs
node --test fixtures/security/test.mjs
node --test security-regression.test.mjs adapter.test.mjs http-safety.test.mjs bff.test.mjs
python3 run-http.py /absolute/path/to/pinned/magpie /absolute/path/to/node24
```

Run inside this directory. No automatic download, install, CI or remote service creation. The parent starts one process group with a 90s wall-clock deadline; kills the group on timeout and removes the private temporary root. Gateway/Mock bind loopback only. HTTP deadline 12s, worker Node deadline 45s. Reports are overwritten for the new run. Application proxy denies background external HTTP, but is **not kernel egress isolation**. The Work runtime and same-UID Node WeakMaps are **not adversarial multi-tenant isolation**.

## Actual results

Node24: original PR53 13/13; original security ZIP 16/16; same 16 tests against V1 16/16; additional V1 security 33/33; HTTP input safety 6/6; BFF candidate 6/6; actual CLI HTTP integration 13/13. Exit codes all 0. These suites overlap and must not be presented as an independent aggregate certification.

Real HTTP: models, Chat, Responses→Chat conversion, Messages→Chat conversion, incremental SSE, 401/429/500, cancellation, replay blocking, paid/tool blocking, unknown network result/redaction. Provider attempts 401=1 / 429=4 / 500=3; Adapter calls per error=1. Cancellation returns UNKNOWN, not a claim of remote billing cessation.

Latest HTTP run: 13.181s including stop/cleanup. Gateway expected SIGTERM exit -15; worker exit0; process group gone; Gateway/Mock ports closed; temporary root removed. See evidence JSON/logs. No real model calls, keys, paid resources, Runner, Codespace or deployment. No new billable cloud resource was requested; no billing API proof is claimed.

## Publication safety / GO-NO-GO

Both fixed product tree and current main recursive tree are untruncated and contain no Workflow/CI trigger files. However GitHub commit status for PR53 head `9d6509dfe521d2d804dd700318c648920b6b4729` says Vercel **Deployment has completed**. Vercel project config read returned 403; no Vercel CLI exists. Therefore publishing a new branch or Draft PR can auto-deploy, forbidden in this work package. **Branch publication and Draft PR: NOT RUN / NO-GO.** Do not mistake a Draft PR for a no-deploy barrier.

Preserve this candidate as a Git object without creating/updating a branch ref (no push/PR event), with readback and a portable evidence bundle. Minimum next authorization: allow a branch-only no-auto-deploy safeguard (for example official `git.deploymentEnabled` restricted to the proposed research branch), verify its behavior, then attach the reviewed commit to a new branch and create exactly one Draft PR. Do not change main, product baseline or project-wide settings without authorization.

Synthetic candidate review: GO. Formal Vercel integration / paid model / multi-tenant trust / final billing / automated Work dispatch: NO-GO. Remaining integration gates are the trusted Gate/Broker service boundary and enforceable retry/accounting control; no deployment approval is inferred.
