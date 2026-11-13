"""Separate explicit runtime consent (never a management-grant upgrade) in the existing private Gateway Source root.

Only the original interactive Gateway route issues consent. These files are
not credentials: consumption still requires the original verified MCP owner,
current host and live Gateway user policy. Checkpoint links are never used.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time

from deer_agent_management import AgentManagementDenied, _canonical, _name


class RuntimeConsentStore:
    def __init__(self, root, *, account, current, source_io=None, clock=time.time):
        if source_io is None:
            from app.gateway import platform_task_source as source_io
        if not callable(current) or not callable(getattr(account, "current", None)):
            raise AgentManagementDenied("RUNTIME_ACCOUNT_CAPABILITY_REQUIRED")
        self.root, self.account, self.current = Path(root), account, current
        self.io, self.clock = source_io, clock
        fd = self.io.private_directory(self.root)
        try:
            info = os.fstat(fd)
            self.identity = info.st_dev, info.st_ino
        finally:
            os.close(fd)

    def _open(self):
        if self.current() is not True or self.account.current() is not True:
            raise AgentManagementDenied("RUNTIME_CONSENT_HOST_STOPPED")
        fd = self.io.private_directory(self.root)
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) != self.identity:
            os.close(fd)
            raise AgentManagementDenied("RUNTIME_CONSENT_ROOT_CHANGED")
        try:
            os.stat("STOP", dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return fd
        os.close(fd)
        raise AgentManagementDenied("RUNTIME_CONSENT_SOURCE_STOPPED")

    def _key(self, user_id, thread_id, operation_key):
        if (not isinstance(user_id, str) or not user_id.strip()
                or user_id in {"default", "internal", "system"}
                or not isinstance(thread_id, str)
                or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", thread_id) is None
                or not isinstance(operation_key, str)
                or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", operation_key) is None):
            raise AgentManagementDenied("RUNTIME_CONSENT_IDENTITY_INVALID")
        return hashlib.sha256(_canonical([self.account.config["owner"],
            self.account.config["generation"], user_id, thread_id, operation_key])).hexdigest()

    def approve(self, *, user_id, token_version, thread_id, operation_key,
                names, actions, ttl_seconds, definition_sha256):
        """Called ONLY after original session, admin, CSRF and thread gates."""
        key = self._key(user_id, thread_id, operation_key)
        if (not isinstance(definition_sha256, str) or re.fullmatch(r"[a-f0-9]{64}", definition_sha256) is None):
            raise AgentManagementDenied("RUNTIME_DEFINITION_PIN_REQUIRED")
        if (type(token_version) is not int or token_version < 0
                or type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 600
                or type(names) is not list or len(names) != 1
                or len(set(names)) != len(names)
                or type(actions) is not list or not actions
                or len(set(actions)) != len(actions)
                or not set(actions) <= {"prepare", "read", "execute", "stop"}
                or "read" not in actions):
            raise AgentManagementDenied("RUNTIME_CONSENT_SCOPE_INVALID")
        names = sorted(_name(name) for name in names)
        created = self.clock()
        record = {"schema": "original-deer-runtime-consent-v1", "grant_id": key,
            "mcp_owner": self.account.config["owner"],
            "generation": self.account.config["generation"], "gateway_user_id": user_id,
            "token_version": token_version, "thread_id": thread_id,
            "operation_key": operation_key, "scope": "original-deer-fixed-runtime",
            "names": names, "actions": sorted(actions), "created_at": created,
            "expires_at": created + ttl_seconds, "model_execution": True,
            "definition_sha256": definition_sha256, "max_runs": 2,
            "execution_profile": "original-codex-cli-read-only",
            "tool_groups": [], "skills": [], "allowed_subagents": [],
            "issuer": "original-interactive-gateway-session"}
        fd = self._open()
        try:
            try:
                self.io.publish(fd, "agent-runtime-consent-" + key + ".json",
                                self.io.encode(record))
            except FileExistsError:
                raise AgentManagementDenied("RUNTIME_CONSENT_EXISTS_QUERY_ONLY") from None
        finally:
            os.close(fd)
        return self.read(key, verified_owner=self.account.config["owner"])

    def by_operation(self, *, user_id, thread_id, operation_key, verified_owner):
        return self.read(self._key(user_id, thread_id, operation_key),
                         verified_owner=verified_owner)

    def read(self, grant_id, *, verified_owner):
        if verified_owner != self.account.config["owner"]:
            raise AgentManagementDenied("RUNTIME_CONSENT_MCP_OWNER_MISMATCH")
        if not isinstance(grant_id, str) or re.fullmatch(r"[a-f0-9]{64}", grant_id) is None:
            raise AgentManagementDenied("RUNTIME_CONSENT_ID_INVALID")
        fd = self._open()
        try:
            record = json.loads(self.io.read_at(fd, "agent-runtime-consent-" + grant_id + ".json"))
            try:
                os.stat("agent-runtime-consent-" + grant_id + ".revoked", dir_fd=fd,
                        follow_symlinks=False)
                revoked = True
            except FileNotFoundError:
                revoked = False
        finally:
            os.close(fd)
        if (record.get("execution_profile") != "original-codex-cli-read-only"
                or record.get("max_runs") != 2
                or not isinstance(record.get("definition_sha256"), str)
                or re.fullmatch(r"[a-f0-9]{64}", record["definition_sha256"]) is None
                or record.get("schema") != "original-deer-runtime-consent-v1"
                or record.get("grant_id") != grant_id
                or record.get("mcp_owner") != verified_owner
                or record.get("generation") != self.account.config["generation"]
                or record.get("scope") != "original-deer-fixed-runtime"
                or record.get("issuer") != "original-interactive-gateway-session"
                or record.get("model_execution") is not True
                or any(record.get(k) != [] for k in ("tool_groups", "skills", "allowed_subagents"))
                or type(record.get("expires_at")) not in (int, float)
                or type(record.get("created_at")) not in (int, float)
                or not math.isfinite(record["expires_at"])
                or not math.isfinite(record["created_at"])
                or not 0 < record["expires_at"] - record["created_at"] <= 600):
            raise AgentManagementDenied("RUNTIME_CONSENT_RECORD_INVALID")
        # Revalidate the full original allowlist even if a record was replaced.
        if (type(record.get("names")) is not list or len(record["names"]) != 1
                or record["names"] != sorted(set(_name(n) for n in record["names"]))
                or type(record.get("actions")) is not list
                or not set(record["actions"]) <= {"prepare", "read", "execute", "stop"}
                or "read" not in record["actions"]
                or self._key(record.get("gateway_user_id"), record.get("thread_id"),
                             record.get("operation_key")) != grant_id
                or type(record.get("token_version")) is not int):
            raise AgentManagementDenied("RUNTIME_CONSENT_RECORD_INVALID")
        return {**record, "revoked": revoked,
                "expired": not record["created_at"] <= self.clock() < record["expires_at"]}

    def active(self, grant_id, *, verified_owner):
        try:
            record = self.read(grant_id, verified_owner=verified_owner)
        except FileNotFoundError:
            raise AgentManagementDenied("RUNTIME_CONSENT_NOT_ISSUED") from None
        if record["revoked"] or record["expired"]:
            raise AgentManagementDenied("RUNTIME_CONSENT_REVOKED_OR_EXPIRED")
        return record

    def revoke(self, grant_id, *, verified_owner, user_id, thread_id):
        record = self.read(grant_id, verified_owner=verified_owner)
        if record["gateway_user_id"] != user_id or record["thread_id"] != thread_id:
            raise AgentManagementDenied("RUNTIME_CONSENT_OWNER_MISMATCH")
        fd = self._open()
        try:
            try:
                self.io.publish(fd, "agent-runtime-consent-" + grant_id + ".revoked", b"revoked\n")
            except FileExistsError:
                pass
        finally:
            os.close(fd)
        return {"grant_id": grant_id, "revoked": True}
