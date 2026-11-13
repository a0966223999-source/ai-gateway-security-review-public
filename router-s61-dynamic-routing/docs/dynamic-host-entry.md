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

Current deployment gap: the staged source is not the registered original
Runtime. Its owned loader, original state directory and live native providers
must be located and bound before enabling execution. Keep automatic dispatch
off while that binding is absent.
