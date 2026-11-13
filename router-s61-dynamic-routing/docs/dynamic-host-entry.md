# Dynamic routing host entry

`createDynamicRoutingPlugin(dynamic, coldIdle?)` returns a Cordis plugin object
with the original `name`, `inject` and `Config`. Its `apply` calls the same
`applyWithDynamicRouting` / Runtime path as the direct trusted entry.

The owned host must resolve its existing `dsh-superagent` plugin entry to this
stable object **instead of** the ordinary module. Retain the object across
Fiber restarts. Do not register it alongside the ordinary entry, register a
second Runtime, choose a replacement state directory, or serialize capabilities
into the profile. Existing account-change restart and Runtime disposal remain
owned by the original Fiber.

For programmatic registration, the trusted loader can use
`registerDynamicRoutingPlugin(originalContext, originalConfig, dynamic, coldIdle)`.
It rejects an already registered ordinary S61 callback or any live plugin
runtime named `dsh-superagent` in that same Registry, then delegates to the
original `ctx.plugin`. It returns the native Fiber/thenable, so the loader must
await startup and retain normal Cordis error/disposal handling. It does not
unload an existing entry, substitute a Store, or suppress startup failures.
An already running S61 requires the original owner's controlled unload window;
the helper never performs that hot replacement itself.

The capability is assembled from live original providers, Store/Effects,
current task Source/Holder, authority checks, native once fence and result
reader. This factory validates shape only; it creates none of those authorities.
Missing capabilities fail before Runtime construction. Missing Cordis services
leave the entry pending through the original injection gate.

`node --test host-tests/dynamic-entry.test.mjs` exercises the built entry with
the installed real Cordis package. It checks missing capability rejection,
original metadata preservation and dependency-blocked registration/cleanup.
It provides no runtime services or Source fixtures and performs no dispatch.
It does not prove deployment, host ownership or end-to-end native execution.

`node --test host-tests/dynamic-registration.test.mjs` additionally checks real
Registry duplicate rejection for ordinary/dynamic entries, native Fiber restart
and disposal, and missing-capability rejection before Fiber allocation. Its
context deliberately has no Runtime services, task Source or execution grant.

Current deployment gap: the staged source is not the registered original
Runtime. Its owned loader, original state directory and live native providers
must be located and bound before enabling execution. Keep automatic dispatch
off while that binding is absent.

## Async native SQL reservation

External consumers whose original native repository reserves asynchronously
must await `routing.claimStartAsync(ticket, assignment)` after the original
routing transaction commits. `QueueProvider.claimStartAsync` must invoke that
original repository fence, never another queue or an in-memory permission flag.
The existing original DSH synchronous fence path remains available.

Before awaiting the native fence, the selector commits an observation intent in
the same original route record. It grants no authority and makes no native
reservation. Re-entry after a lost reply/crash only invokes the captured original
`recover` reader and returns `query-only`; it never calls the fence a second
time. Switching back to the synchronous claim method cannot bypass that intent.
After a known native result the selector rechecks Source/policy/provider/Stop
before returning `send`. Revocation during the await refuses launch. A caller
must not infer a native start or completion from a reservation decision.

The production Store/Effects tests use a temporary database, original Engine
and original durable effect queue. Identity, Source and native fence callbacks
are explicitly synthetic. They do not supply a live Codex adapter, signed
ingress, S61-to-batch identity mapping or trusted completion reader. Those
bindings and formal live acceptance remain required before enabling dispatch.
