# Trusted context and instance namespace V0 — synthetic research only

This candidate is based on PR37 Head 322aafbd70863ba116ae0d7fcb9c314a93fd3499. It adds an **independent** offline mock and 16 synthetic tests, leaving PR37 unchanged.

The existing Consumer Gate V0 and Memory Access Facade V0 are DENY-only parsers, **not** authenticators. This candidate's private-looking Python context issuer is explicitly exposed through test_issuer() and **is not a trusted security boundary**. An attacker with direct Python object access can call it. It demonstrates contract shape only; cannot be deployed or described as preventing a malicious caller from minting identity.

The namespace must be globally unique and assigned by a trusted coordinator. Same namespace copied into two instances with identical token generation collides; 128-bit random values reduce probability but do not provide coordination. A namespace is not a grant, login proof or tenant authorization.

Mapping is per-process memory only; no durable registry, trusted identity issuer, revocation receipts, replay protection across instances, audit, credential store, real API or upstream access. References never reveal source_id in their returned data; internal mapping still holds it.

Run `python3 -I -B automation/superteam/tests/test_namespaced_resolver_mock_v0.py` in a verified fixed-SHA offline environment. 16 test definitions are not a PASS claim. Explicitly expect same-namespace clone collision as a negative demonstration; independent adversarial review must assess issuer exposure, scope forgery, TTL, tenant separation, replay and namespace cloning.

Production design must reuse a **verified** Gate principal from an actually trusted login system (OIDC or equivalent), restrict context construction to server-side trusted runtime, enforce per-object authorization and persist revocation/namespace uniqueness. All are UNKNOWN/not implemented. No real service integration authorized.
