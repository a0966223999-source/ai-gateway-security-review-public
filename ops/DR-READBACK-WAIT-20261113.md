# DR management readback wait — 2026-11-13 Taipei

Problem: a successful original role read can finish after the transport's 3-second wait. The receipt correctly exposes audit metadata only, so it cannot substitute for full definition readback.

Change: only a fresh management kind=read requests a bounded 12-second wait. Other operations retain 3 seconds. BatchRPC validates a caller-internal maximum of 15 seconds, waits through notification hints, and independently checks the original signed response after waits and at the deadline. It enqueues exactly once. No new public tool parameter, account, consent, service, host source, key, queue or role is created. Cached operations and agent_management_receipt remain historical_only and exclude contents.

Validation: 7 new deterministic signed-response tests, 17 original fresh-host/control/Stop tests, 10 checkpoint/background RPC tests PASS; py_compile PASS. Transport/identity are explicit fixtures; not live readback or independent QA. The late/lost notice tests exercise actual Ed25519 response verification.

Deployment: original PR3 and existing test/vercel-fastapi-protected-preview only. Preserve all branch-scoped settings and original MCP alias; inspect READY and exact commit before alias update. Rollback by assigning the existing MCP alias to previous READY dpl_Hp1cKTFtWKkvTgP7jnwYmVFZTnEA; no host restart needed.

Existing role qa-dr-runtime-20261113 was created once under dr-runtime-role-create-20261113-02. Do not create again. Management consent02 expires at1794512261.6843255; do not reuse an expired grant. A fresh active named consent and explicitly selected original manager grant are required before a fresh full read. Only after exact full readback and role definition SHA verification may the separately confirmed runtime consent be issued. Arithmetic/live-stop/revocation acceptance remain NOT_RUN.
