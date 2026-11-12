# Deer single-item in-flight stop — 2026-11-12

Published private shared entry: v1.0.9, release pluginrel_6af5d29b1e088191b65499cc74dbcfcc.
Runtime: 5a59f642e0e3d037b467de77e38d744c3cccaa5e.
Vercel: dpl_3vBkaBLT7unkn2KfQgvSDdanAzjU, READY. Original stable /mcp alias retained.
Original host generation f8a69fac5f1b2acd07918b0afe7348c5; signed heartbeat verified.
Original Source/Holder/SQL authorization and execute permission are retained; no new grants.
Gate1 remains DENY; D2 untouched.

## Callable behavior

Use batch_stop when exposed; compatibility path through existing batch_task:
execution_mode="stop_codex_once", parent_id, batch_id, item_id, fresh operation_key.
Query batch_receipt using operation_kind="execute" and that stop key.
No caller PID, arbitrary command, path or identity is accepted.
STOPPED requires owned process tree stopped AND original SQL terminal state saved.
Successful stop: cancelled, attempt=1, stop_reason=stopped_by_request, result=null.
Pending-only cancellation remains separate and preserves attempt=0.
Already terminal/exited/unowned targets are query-only; old PIDs are never recovered.
Duplicate same stop key returns original receipt, without signalling again.

Single original worker now handles concurrent requests; live Codex cap=2,
original batch/SQL capacity and once fences retained. Only retained process ownership
permits stop. /proc start identity and pidfd protect against PID reuse. Observed
detached descendants are tracked; SIGTERM escalates to SIGKILL after 3 seconds.
Worker shutdown cleans its owned runs. No host STOP marker is used to stop one item.

## Real original-host Codex acceptance

Round 1: native-batch-c834bb01c41e4027a51939bc77e3ebd7.
Target STOPPED/cancelled attempt=1; sibling succeeded with sum=1023.
Sibling completed before target stop, so this alone does not prove overlap.
Pending sentinel cancelled independently with attempt=0. No leftover pending items.

Round 2: native-batch-47f036ff01604dccbc8dd8d2dd9bd498.
Target batch-item-f07c271593db486da4c19afd82b39500:
started 14:49:22.444697 UTC; stopped/cancelled 14:49:39.280810 UTC.
Sibling batch-item-656710cb1c594ed6baf45428ea260219:
started 14:49:28.095369 UTC; independently read as running after target stop;
succeeded 14:50:37.590209 UTC, with original saved result:
{"nonce":"item-stop-overlap-1794494887384-sibling","sum":903}.
Both attempts remained 1. Target result is null. Stop tracked 9 processes;
process_group_stopped, observed_descendants_stopped and original_terminal_state_saved=true.
Stop receipt request_id b661d35c7f3927bf309212f64e8b9b9465f17b2b4e9c06883ef0c980ea481320.
Final independent read request_id 1f9c586916c394237c092c4b835ebb18148716bd17ad98d753e0b1b2dae1586b.
Same stop key and original execute receipt both return the original STOPPED outcome.
Post-replay independent read still shows cancelled=1/succeeded=1, attempts=1.

## Construction verification

Final original remote backend venv: test_platform*.py 16 PASS (14.572s);
test_fresh_batch*.py 17 PASS (0.452s). These include synthetic SQL/authority/CLI fixtures;
they are distinct from the real Codex acceptance above.
Terminal-observed summaries only: raw remote log bytes were not downloaded.
Intermediate stop16.log failed one assertion: signals receipt omitted pidfd SIGKILL,
although termination worked. Corrected in final commit; final rerun passed.
Remote logs retained at /tmp/stop16-fixed.log and /tmp/stop17.log.

## Publication preservation and scope

Readback v1.0.9 retains App ID, MCP endpoint, presentation, audience and defaultPrompt.
v1.0.8 DR Bot references, license and producer helper are unchanged except publisher-added
trailing newline. Legacy manifest JSON is semantically equal to candidate (format normalization).
This Work's real runtime acceptance is not an independent QA ACK or cross-account proof.
This release does not assert Router S61, recursive decomposition, container sandbox, or
full subsystem availability. AIO true sandbox remains NOT_DEPLOYED.
Original supervisor reload budget must not be reset or casually consumed again.

Raw tool envelopes and request bodies: evidence/deer-single-item-stop-live-20261112.json.
Restore source from the pinned runtime commit; this archive is a delta, not host credentials
or a complete original Gateway backend/SQL environment.
