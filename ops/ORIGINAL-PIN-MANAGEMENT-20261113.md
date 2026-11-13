# DR management over the original host pin

This supersedes the earlier candidate instructions to add an `agent` action or
replace host configuration/pins. Do not apply those earlier configuration edits.

Management uses original `read` and `execute` transport actions, with strict
`original-deer-agent-management-v1` mode and hashed, kind-bound operation keys.
The original transport enrollment alone grants no registry management access.
An independent, original authenticated Gateway session must issue a live,
name-scoped consent. Missing, expired or revoked consent fails closed.

Keep host.json, config.sha256, generation, parent, signing/ingress keys and
XJ_FRESH_BATCH_HOST_CONFIG unchanged. The existing branch-scoped MCP environment
only needs XJ_AGENT_MANAGEMENT_ENABLED=true to mount the gated tools.

The original static review page is deployed at the existing Gateway, using
the original session/admin/thread/CSRF checks for its approval POST. Anonymous
auth/me remains 401. The page itself does not issue consent on load.

Pending-host installer selects the future consent hash from an original
checkpoint identity record and independently verifies live SQL ownership. The
expired checkpoint record is an identity lookup hint, never management authority.
The installer creates no grant. It hands the original supervisor lock back
before launching the existing manager and retains original worker arguments,
environment and original config pin.

Review scope: qa-dr-mcp-20261112; list/read/create; 600 seconds; no tool groups,
skills, subagents, model calls, versioned update or fixed-Agent execution/stop.
Native batch cancellation/Stop and checkpoint forwarding remain separate and
preserved. A saved definition is not proof that a resident Agent executed.

Validation: 73 management/issuer/installer/lifecycle tests, 17 original transport
tests and 14 checkpoint tests pass in local isolation. These are engineering
checks, not independent QA or proof of live create/readback acceptance.

Live consent-page installation receipt: evidence/dr-consent-page-live-20261113.json.
Management consumer installation and MCP deployment are separate steps; verify
fresh signed heartbeat after worker restart and READY deployment before asking
for the short interactive consent. Never reuse the closed host-only trial grant.
