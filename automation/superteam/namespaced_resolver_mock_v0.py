"""Offline-only identity context and namespaced task resolver research.
No network, credentials, production authentication, or durable state.
"""
from dataclasses import dataclass
import secrets
import re

_NS = re.compile(r"[a-z][a-z0-9_-]{1,31}\Z", re.ASCII)
_TOKEN = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)

@dataclass(frozen=True)
class _Identity:
    tenant: str
    scopes: frozenset

class _TrustedContextIssuer:
    """Local test issuer, deliberately not exported as a real authenticator."""
    def __init__(self):
        self._issued = {}
    def issue_for_test(self, *, tenant, scopes):
        if type(tenant) is not str or not tenant or type(scopes) is not frozenset or not scopes:
            raise ValueError("invalid_context")
        if not all(type(x) is str and x for x in scopes):
            raise ValueError("invalid_scopes")
        handle = object()
        self._issued[handle] = _Identity(tenant, scopes)
        return handle
    def resolve(self, handle):
        try:
            return self._issued.get(handle)
        except TypeError:
            return None

class NamespacedResolver:
    def __init__(self, namespace, *, token_factory=None):
        if type(namespace) is not str or _NS.fullmatch(namespace) is None:
            raise ValueError("invalid_namespace")
        self._namespace = namespace
        self._token = token_factory or (lambda: secrets.token_hex(16))
        self._entries = {}
        self._issuer = _TrustedContextIssuer()

    def test_issuer(self):
        """Only for synthetic offline tests. Not an auth endpoint."""
        return self._issuer

    def issue(self, context, *, platform, source_id, expires_at):
        identity = self._issuer.resolve(context)
        if identity is None or "read" not in identity.scopes:
            return {"decision": "DENY"}
        if type(platform) is not str or not platform or type(source_id) is not str or not source_id:
            return {"decision": "DENY"}
        if type(expires_at) is not int or expires_at <= 0:
            return {"decision": "DENY"}
        for _ in range(3):
            token = self._token()
            if type(token) is not str or _TOKEN.fullmatch(token) is None:
                return {"decision": "DENY"}
            ref = "task_" + self._namespace + "_" + token
            if ref not in self._entries:
                self._entries[ref] = (identity.tenant, platform, source_id, expires_at, False)
                return {"decision": "ISSUED", "ref": ref}
        return {"decision": "DENY"}

    def resolve(self, context, *, ref, now):
        identity = self._issuer.resolve(context)
        if identity is None or "read" not in identity.scopes or type(ref) is not str or type(now) is not int:
            return {"decision": "DENY"}
        entry = self._entries.get(ref)
        if entry is None:
            return {"decision": "DENY"}
        tenant, _platform, _source_id, expiry, revoked = entry
        if tenant != identity.tenant or revoked or now >= expiry:
            return {"decision": "DENY"}
        return {"decision": "READ_ONLY", "ref": ref}

    def revoke(self, context, *, ref):
        identity = self._issuer.resolve(context)
        if identity is None or "revoke" not in identity.scopes or type(ref) is not str:
            return {"decision": "DENY"}
        entry = self._entries.get(ref)
        if entry is None or entry[0] != identity.tenant:
            return {"decision": "DENY"}
        self._entries[ref] = (*entry[:4], True)
        return {"decision": "REVOKED"}
