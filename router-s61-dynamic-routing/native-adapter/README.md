# Original Codex reservation-to-start gate candidate

This pinned staging script copies the current original executor into the isolated engineering directory and adds an optional internal async callback after the original durable SQL reservation, before invocation/Popen. No live executor is replaced. The callback sees the original full binding, batch/item, native intent, exact assembled prompt digest and dependency input digest. It must return strict true within the bounded request timeout. Original host and fixed-role checks run again after the wait; original DAG revalidation, process-local capacity, ActiveRun registry, Stop and result persistence remain in the original function.

The public Execute schema is unchanged. Ordinary platform callers omit this internal callback. The future S61 adapter must supply the held callback; this optional seam alone does not force existing platform operations through Router. The callback is not a new grant, SQL reservation or transport.

A refusal, timeout, cancellation or lost authority after reservation leaves the original durable once record in place. Recovery must query that same batch/item/intent; never clear the fence or issue a replacement launch. This patch does not yet add pre-launch Stop ownership while waiting; cancellation must be owned by the eventual adapter task, and native Stop remains the existing ActiveRun path after Popen.

## Observed checks

Original Codespace: compile exit 0; seven async boundary tests PASS. Tests execute AST nodes taken from the generated actual executor boundary and the actual outer capacity wrapper. SQL, Source/role authority, CLI and host are fixtures, so this is neither full native execution nor formal S61 acceptance. The full executor remains unchanged on the live service. Generated candidate SHA is recorded in onhost-start-gate.json.

## Remaining installation requirements

Bind the S61 owned Runtime/assignment to the same-process Python executor task and the original Source/Holder with a real authenticated existing host bridge. Connect the callback to original S61 claimStartAsync and final current-assignment/Stop checks, then map original input delivery, native ACK and result through the saved result verifier. No such held runtime/bridge is currently verified. Do not enable dynamic dispatch until this wiring and same-window original loaded-version/writer checks pass. No D2 change is required for this seam; it does not establish that other D2-dependent policies are ready.
