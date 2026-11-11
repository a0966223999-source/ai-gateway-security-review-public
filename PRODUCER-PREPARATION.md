# Work self-report and producer preparation update

This additive client release follows explicit cloud/native routing v3. It does not change the original host identity/authorization checks, RPC schema, Source, credentials, or access permissions.

## Included

- `collect_work_self_report.py` reads only `CODEX_THREAD_ID` and `CODEX_ENVIRONMENT_ID` in the reporting Work's execution environment.
- `prepareWorkSelfReport` preserves allowlisted observations and the original canonical register request. A failed report cannot be forwarded as a valid partial report.
- `prepareTargetedRegistration` reports all independent input errors before effects, validates exact canonical request bytes, and checks that the reported thread agrees with the separately supplied official target.
- Both preparation functions handle null/non-object options with structured errors. They perform zero tool calls and return no authorization or readiness verdict.
- The existing submission, typed locator constructors, and UNKNOWN query-only behavior remain unchanged.

## Authority boundary and remaining gap

A Work reports hints; the original host verifies identity and current genuine USER authority; only then may continuation registration proceed. Runtime IDs do not grant authority or select a route. The successful preparation state is INPUTS_PREPARED_NOT_AUTHORIZED. Missing official reference, request, or tool stops before submission.

For Work actually created by the common host, the host can retain the real create result and pass that official context automatically. For arbitrary existing Work, a supported original-host/platform route provider is still required. This release does not implement that generic resolver or self-report transport. An existing native read denial remains terminal unless materially new access evidence permits the exact denied operation. No alternate-route retry, account scan, replacement Work, or event is used to discover identity.

The original host retains its complete native evidence review, exact task/registration/activation/self-wake authorization checks, Source CAS, immutable request/nonce/target binding, original deadlines, and once fences. A previous privileged-path success is not proof that arbitrary Work admission works.

## Verification

Independent local review and rerun: Node syntax, 21 Node tests and 3 collector Python tests passed. Two review findings were corrected and independently rechecked. The new tests cover malformed composition, invalid options, canonical request bytes, mismatched targets, no effects, and compatibility.

Run from this repository:

```sh
node --check functions_producer_v2.mjs
node --test tests/test_functions_producer_v2.mjs tests/test_producer_preparation.mjs
PYTHONPATH=. python3 -B -m unittest discover -s tests -p 'test_work_self_report.py' -v
```

No final live business test was run for this update. Publication, a previous successful privileged test, offline tests, and an active subscription do not establish automatic arbitrary-Work continuation.
