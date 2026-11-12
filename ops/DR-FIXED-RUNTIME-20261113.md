# Fixed Agent runtime candidate — 2026-11-13 Taipei

This candidate binds a saved role definition to the existing Deer Holder/SQL
and the original bounded Codex CLI executor. It requires no Grok model API.
It is not a native ChatGPT Work controller, Router deployment or all-Agent
installation. Bot Designer remains separate.

## Behavior
- `fixed_agent_task` prepares, explicitly executes, reads or stops one role task.
  Prepare saves an immutable definition/prompt digest in original execution_spec.
  It returns the original batch ID; a new status key obtains its original item ID.
- Execution requires separate original interactive Gateway runtime consent pinned
  to one role SHA256, at most 600 seconds and two preparations, each run <=90s.
  A management grant or checkpoint link cannot authorize runtime.
- The existing Codex login/default model and read-only CLI permission profile are
  used. This can consume the original account quota. Empty role capabilities do
  not claim the CLI's built-in read operations are technically disabled.
- Expiry, revocation, lost live policy or definition change prevents dependent
  execution/result reads and terminates the owned running process. Original
  attempt fences remain; no automatic replay or invented terminal success.
- Explicit stop uses retained original process ownership and saves original SQL
  cancelled/attempt=1/stopped_by_request with null result. Stop may clean up an
  expired runtime grant, still requiring the same original user/source/policy.
- Ordinary single-item and bounded background executors cannot bypass the role
  lease. Existing batch/stop/checkpoint tools and background coordinator remain.
- `fixed_agent_receipt` is audit only, recovers identifiers but never role/task
  contents or renewed authority. UNKNOWN queries the same key, never resubmits.

## Current live boundary
The original service contains 3 pending/attempt=0 items in batch
native-batch-605006c8346943798f96895f3d46be59, title
"Bounded background Codex live acceptance". They belong to the other execution
workstream and are preserved. No cancel, model, service restart, auth change,
configuration-pin change, new runtime grant or new registry definition occurred
in this work. Source candidate staging is separate from installation.

The current MCP test branch was independently updated to 5424d7bb70710886f05696229a32615b0935d169.
This candidate merges those 8 changed files, preserving the background work.
The private common plugin was observed as 1.0.13; it was not modified here.
Do not deploy old 411272e wholesale or republish this snapshot's older plugin.

## Controlled installation and remaining acceptance
Run ops/install_fixed_runtime.py only from this exact verified checkout. The
manifest requires the observed Gateway/manager start identity, source hashes,
and original SQL terminal-only state before any formal write or process stop.
It preserves auth/session/origin/config environment in memory; it does not log
credentials or change host.json/config.sha256/JWT. It adds only a static public
review page; runtime APIs keep original session/admin/thread/CSRF protection.
Changed preimages/processes stop; regenerate the manifest from real observations,
never bypass the guard. The script has not passed the live gate in this work.

After source installation, independently confirm the new signed worker heartbeat,
health 200, anonymous auth/me 401, and runtime review page 200. Deploy the merged
candidate to the original protected MCP test branch using a fresh expected-head
lease, preserving all current env/keys/pins; opt in XJ_FIXED_AGENT_RUNTIME_ENABLED.
Do not create another endpoint/account/service. Refresh original App tools.

The retained qa-dr-mcp-20261112 management-test role explicitly prohibits models;
do not run it. Use the reviewed management page's named-role field and a fresh
management consent to create qa-dr-runtime-20261113 through original agent_manage,
then independently read its exact definition. No direct registry/database insert.
Suggested arithmetic-only definition: description "Bounded fixed-role runtime
acceptance"; soul "Perform only the supplied arithmetic task and return the
requested JSON. Do not use tools, files, network or delegation." All capability
lists empty. This is a prompt boundary, not a new OS/container sandbox claim.

Only after these preparations, obtain action-time confirmation for the original
Gateway runtime button: named test role, exact definition hash, 10 minutes,
maximum two 90-second tasks, original Codex account quota, no delegation. Select
that exact issued grant via --agent-runtime-grant-id in the original manager;
no automatic discovery/adoption of grants. The management grant may then be
revoked independently. Test one arithmetic completion plus one bounded live stop,
independently query original SQL through tools, then revoke runtime and confirm a
fresh execution/read is denied. Real model/stop acceptance and independent QA
remain NOT_RUN. Do not classify isolation CLI fixtures as real model execution.

No D2/Gate1 changes. Earlier source candidates remain historical and must not
replace these original-pin instructions.
