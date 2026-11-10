# Public contract fixtures

## Original RPC UTF-8 canonical vectors

Upstream baseline: b1d12dad965ddb12156076c8794fe78404913c1a
Source module: shared_source_rpc.py
Source SHA256:
57636c9d06692063d5cc9ac9544c0138ec97c55a73afdf682b9f89290cf9ecee

The bundled tests/fixtures/utf8-golden.json was calculated by invoking the original
module's canonical and digest functions on synthetic Chinese checkpoint/request
and response values. It was not generated with this candidate's canonical helper.
The local original-module comparison was retained separately by the integrator;
public tests require only fixed golden bytes and digests, not that source module.

Request canonical SHA256:
430fdb172e9451b43470dd477ed2cd845fc5f623e92b9c0536bd7f82f12a0a81

Reply canonical SHA256:
de7e660b493e58453b0f86d24bbe1a21dbf982cf1a2e45321d1d65d0239e3cbd

Tests compare exact UTF-8 bytes as hex and SHA256, roundtrip the request through
same-event storage, and verify a synthetic Ed25519 signature over the reply body.
No private task, source document, host identity or live signing key is included.

## Public live Lua differential baseline

Upstream public baseline: 9b800a
Fixture: tests/fixtures/inbox-9b800a.lua
SHA256:
cd975b23255d9cc51f21d0c2f5dba1b4c0efe0b90d879a4a46a120f801f81707

The integrating parent retrieved this public baseline through the official Git
connector and verified it against an independently verified backup. The fixture
is unmodified, and its hash is asserted in tests. Candidate inbox_rpc.lua adds
only the bounded rpc_submit path/shared send fields. Differential tests preserve
ordinary send/dedup/conflict/list/claim/ACK and live subscription_get found/status
contracts, including missing/present/unsubscribed and wrong-event cases.

## Limits of evidence

42 tests passed offline with real MCP ToolAnnotations, fake MCP registration,
fakeredis Lua/PubSub, synthetic fixtures and temporary signing keys. A mocked
45-second diagnostic verifies API behavior without actually waiting 45 seconds.
These are transport/storage contract tests, not live native authority or Source
finalization verification. No deployment, event, subscription or runtime setting
was changed by preparation of this package.
