"""One operator-approved original-host definition test, NOT an MCP login.

The caller must have the user's explicit approval for this exact role and
600-second limit. A saved checkpoint link supplies an identity association,
never management authority. This file neither upgrades that link nor switches
on the global management HTTP API. It uses the original named-owner Store.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
from types import SimpleNamespace

from deer_agent_management import AgentManagementDenied, _canonical
from deer_management_journal import OriginalManagementJournal
from deer_management_test_scope import TEST_AGENT_NAME


class OriginalHostTrial:
    def __init__(self, *, backend, gateway_pid, link_id, approval_reference,
                 operation_key="dr-approved-host-trial-20261112-01"):
        self.backend = Path(backend).resolve(strict=True)
        self.pid = int(gateway_pid)
        if not re.fullmatch(r"[a-f0-9]{64}", link_id):
            raise AgentManagementDenied("ORIGINAL_LINK_ID_REQUIRED")
        if approval_reference != "owner-approved-qa-dr-readback-20261112-600s":
            raise AgentManagementDenied("EXACT_OPERATOR_APPROVAL_REQUIRED")
        self.approval_reference = approval_reference
        self.operation_key = operation_key
        self.proc = Path("/proc") / str(self.pid)
        if Path(os.readlink(self.proc / "cwd")).resolve() != self.backend:
            raise AgentManagementDenied("ORIGINAL_GATEWAY_CWD_MISMATCH")
        self.process_identity = (self.proc / "stat").read_text().split()[21]
        command = (self.proc / "cmdline").read_bytes().split(b"\0")
        if not any(b"app.gateway.app:app" == arg for arg in command):
            raise AgentManagementDenied("ORIGINAL_GATEWAY_PROCESS_REQUIRED")
        allowed_env = {key.encode() for key in ("DEER_FLOW_HOME", "DEER_FLOW_CONFIG_PATH",
            "DEER_FLOW_SOURCE_ACCOUNT_CONFIG", "DEER_FLOW_SOURCE_ACCOUNT_CONFIG_SHA256")}
        process_env = {key: value for item in (self.proc / "environ").read_bytes().split(b"\0")
                       if b"=" in item for key, value in [item.split(b"=", 1)] if key in allowed_env}
        # No credential values are exported or copied. Only original config
        # location/pin variables are needed to select the SAME runtime data.
        for key in ("DEER_FLOW_HOME", "DEER_FLOW_CONFIG_PATH",
                    "DEER_FLOW_SOURCE_ACCOUNT_CONFIG", "DEER_FLOW_SOURCE_ACCOUNT_CONFIG_SHA256"):
            if key.encode() in process_env:
                os.environ[key] = process_env[key.encode()].decode()
            else:
                os.environ.pop(key, None)
        del process_env
        os.chdir(self.backend)
        sys.path[:0] = [str(self.backend), str(self.backend / "packages/harness"),
                       str(self.backend / "packages/extension-api")]
        from app.gateway.platform_source_http import default_platform_source_runtime
        from app.gateway import platform_task_source as source_io
        from deerflow.config.app_config import get_app_config
        from deerflow.persistence.agents import get_agent_store
        runtime = default_platform_source_runtime()
        if runtime.account is None or runtime.account.current() is not True:
            raise AgentManagementDenied("ORIGINAL_PINNED_ACCOUNT_NOT_BOUND")
        self.account, self.root, self.io = runtime.account, runtime.root, source_io
        self.config = get_app_config()
        if self.config.database.backend != "sqlite":
            raise AgentManagementDenied("THIS_TRIAL_REQUIRES_ORIGINAL_SQLITE")
        self.database = Path(self.config.database.sqlite_path).resolve(strict=True)
        self.store = get_agent_store()
        fd = source_io.private_directory(self.root)
        try:
            self.link_raw = source_io.read_at(fd, "account-link-" + link_id + ".json")
            try:
                os.stat("account-link-" + link_id + ".revoked", dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise AgentManagementDenied("ORIGINAL_IDENTITY_LINK_REVOKED")
        finally:
            os.close(fd)
        self.link = json.loads(self.link_raw)
        self.link_id = link_id
        if (self.link.get("schema") != "platform-checkpoint-account-link-v1"
                or self.link.get("mcp_owner") != self.account.config["owner"]
                or self.link.get("generation") != self.account.config["generation"]
                or self.link.get("scope") != "platform-checkpoint-only"
                or self.link.get("native_work_authority") is not False):
            raise AgentManagementDenied("ORIGINAL_IDENTITY_ASSOCIATION_MISMATCH")
        self.user_id = self.link["gateway_user_id"]
        self.started = None
        self.expires_at = None
        self.closed = False

    async def readback_only(self):
        scope_id = hashlib.sha256(_canonical([self.user_id, self.operation_key])).hexdigest()
        fd = self.io.private_directory(self.root)
        try:
            scope = json.loads(self.io.read_at(fd, "agent-operator-trial-" + scope_id + ".json"))
            try:
                os.stat("agent-operator-trial-" + scope_id + ".closed.json", dir_fd=fd,
                        follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise AgentManagementDenied("OPERATOR_TRIAL_ALREADY_CLOSED")
        finally:
            os.close(fd)
        if (scope.get("schema") != "operator-approved-deer-definition-test-v1"
                or scope.get("approval_reference") != self.approval_reference
                or scope.get("gateway_user_id") != self.user_id
                or scope.get("mcp_owner") != self.account.config["owner"]
                or scope.get("generation") != self.account.config["generation"]
                or scope.get("token_version") != self.link["token_version"]
                or scope.get("test_agent_name") != TEST_AGENT_NAME
                or scope.get("identity_link_used_as_management_grant") is not False
                or scope.get("mcp_management_authority") is not False
                or scope.get("actions") != ["create-definition", "read-definition"]):
            raise AgentManagementDenied("OPERATOR_TRIAL_READ_SCOPE_MISMATCH")
        self.expires_at = scope["expires_at"]
        if await self.authorize() is not True:
            raise AgentManagementDenied("OPERATOR_TRIAL_READ_DENIED")
        readback = await asyncio.to_thread(self.store.get, TEST_AGENT_NAME, user_id=self.user_id)
        soul = await asyncio.to_thread(self.store.get_soul, TEST_AGENT_NAME, user_id=self.user_id)
        if (readback.name != TEST_AGENT_NAME or readback.tool_groups != []
                or readback.skills != [] or readback.allowed_subagents != []):
            raise AgentManagementDenied("ORIGINAL_STORE_READBACK_MISMATCH")
        return {"status": "INDEPENDENT_ORIGINAL_STORE_READBACK_PASS", "name": readback.name,
                "soul_sha256": hashlib.sha256((soul or "").encode()).hexdigest(),
                "store_type": type(self.store).__name__}

    async def _independent_readback(self):
        # A fresh interpreter resolves the original config/Store again. No
        # process context, in-memory registry, model client or cookie is shared.
        argv = [sys.executable, str(Path(__file__).resolve()), "--backend", str(self.backend),
                "--gateway-pid", str(self.pid), "--link-id", self.link_id,
                "--approval-reference", self.approval_reference, "--readback-only"]
        child = await asyncio.to_thread(subprocess.run, argv, capture_output=True,
                                        text=True, timeout=45)
        if child.returncode != 0:
            raise AgentManagementDenied("INDEPENDENT_STORE_READER_FAILED_QUERY_ONLY")
        try:
            result = json.loads(child.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError) as error:
            raise AgentManagementDenied("INDEPENDENT_READER_RESULT_INVALID") from error
        return result

    def current(self):
        try:
            return (not self.closed and self.account.current() is True
                    and (self.proc / "stat").read_text().split()[21] == self.process_identity
                    and Path(os.readlink(self.proc / "cwd")).resolve() == self.backend)
        except (OSError, ValueError):
            return False

    def live_user(self):
        # Read only identity/policy fields, never email, password hash, tokens,
        # OAuth secrets, session cookies or another user's record.
        with sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT id,system_role,token_version,needs_setup,oauth_provider,oauth_id "
                             "FROM users WHERE id=?", (self.user_id,)).fetchone()
            thread = db.execute("SELECT user_id FROM threads_meta WHERE thread_id=?",
                                (self.link["thread_id"],)).fetchone()
        if (row is None or row["system_role"] != "admin" or row["needs_setup"]
                or row["token_version"] != self.link["token_version"]
                or thread is None or thread["user_id"] != self.user_id):
            raise AgentManagementDenied("ORIGINAL_LIVE_OWNER_OR_TOKEN_VERSION_DENIED")
        return SimpleNamespace(**dict(row))

    async def authorize(self):
        if not self.current() or self.expires_at is None or time.time() >= self.expires_at:
            return False
        fd = self.io.private_directory(self.root)
        try:
            if self.io.read_at(fd, "account-link-" + self.link_id + ".json") != self.link_raw:
                return False
            try:
                os.stat("account-link-" + self.link_id + ".revoked", dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                return False
        finally:
            os.close(fd)
        from app.gateway.authz import resolve_route_permissions
        user = await asyncio.to_thread(self.live_user)
        permissions = await resolve_route_permissions(user, is_internal=False)
        return {"threads:write", "runs:create"} <= set(permissions)

    async def run(self):
        self.started, self.expires_at = time.time(), time.time() + 600
        if await self.authorize() is not True:
            raise AgentManagementDenied("ORIGINAL_OWNER_POLICY_DENIED")
        if await asyncio.to_thread(self.store.exists, TEST_AGENT_NAME, user_id=self.user_id):
            raise AgentManagementDenied("TEST_AGENT_EXISTS_QUERY_ONLY_NO_OVERWRITE")
        scope = {"schema": "operator-approved-deer-definition-test-v1",
                 "approval_reference": self.approval_reference,
                 "mcp_owner": self.account.config["owner"], "gateway_user_id": self.user_id,
                 "generation": self.account.config["generation"],
                 "token_version": self.link["token_version"],
                 "identity_link_sha256": hashlib.sha256(self.link_raw).hexdigest(),
                 "identity_link_expired": self.link["expires_at"] <= self.started,
                 "identity_link_used_as_management_grant": False,
                 "actions": ["create-definition", "read-definition"],
                 "test_agent_name": TEST_AGENT_NAME, "expires_at": self.expires_at,
                 "mcp_management_authority": False, "model_execution": False}
        scope_id = hashlib.sha256(_canonical([self.user_id, self.operation_key])).hexdigest()
        fd = self.io.private_directory(self.root)
        try:
            self.io.publish(fd, "agent-operator-trial-" + scope_id + ".json", self.io.encode(scope))
        finally:
            os.close(fd)
        journal = OriginalManagementJournal(self.root, binding={
            "mcp_owner": self.account.config["owner"], "gateway_user_id": self.user_id,
            "grant_id": scope_id}, current=self.current, authorize=self.authorize, source_io=self.io)
        config = {"name": TEST_AGENT_NAME, "description": "DR Bot definition readback test; do not run",
                  "tool_groups": [], "skills": [], "allowed_subagents": []}
        soul = "Nonexecuting verification definition. Do not run models, tools or delegated work."
        payload_sha = hashlib.sha256(_canonical({"config": config, "soul": soul})).hexdigest()

        async def create():
            if await self.authorize() is not True:
                raise AgentManagementDenied("TRIAL_AUTHORITY_REVOKED")
            await asyncio.to_thread(self.store.create, TEST_AGENT_NAME, config, soul, user_id=self.user_id)
            return {"name": TEST_AGENT_NAME, "definition_saved": True, "model_executed": False}

        result = None
        try:
            await journal("create", self.operation_key, payload_sha, create)
            if await self.authorize() is not True:
                raise AgentManagementDenied("TRIAL_READ_AUTHORITY_CHANGED")
            readback = await asyncio.to_thread(self.store.get, TEST_AGENT_NAME, user_id=self.user_id)
            read_soul = await asyncio.to_thread(self.store.get_soul, TEST_AGENT_NAME, user_id=self.user_id)
            if (readback.name != TEST_AGENT_NAME or read_soul != soul
                    or readback.tool_groups != [] or readback.skills != []
                    or readback.allowed_subagents != []):
                raise AgentManagementDenied("ORIGINAL_STORE_READBACK_MISMATCH")
            if hasattr(self, "backend"):
                independent = await self._independent_readback()
                if (independent.get("status") != "INDEPENDENT_ORIGINAL_STORE_READBACK_PASS"
                        or independent.get("name") != TEST_AGENT_NAME
                        or independent.get("soul_sha256") != hashlib.sha256(soul.encode()).hexdigest()):
                    raise AgentManagementDenied("INDEPENDENT_READER_CONTENT_MISMATCH")
            receipt = await journal.query("create", self.operation_key, payload_sha)
            if receipt["status"] != "CONFIRMED":
                raise AgentManagementDenied("TRIAL_RECEIPT_UNCONFIRMED")
            result = {"status": "ORIGINAL_HOST_DEFINITION_CREATE_READBACK_PASS",
                      "agent_name": TEST_AGENT_NAME, "store_type": type(self.store).__name__,
                      "definition_saved": True, "independent_readback": True,
                      "fresh_process_readback": hasattr(self, "backend"),
                      "receipt_status": receipt["status"], "operation_id": receipt["operation_id"],
                      "payload_sha256": payload_sha,
                      "identity_link_expired": scope["identity_link_expired"],
                      "identity_link_used_as_management_grant": False,
                      "mcp_management_bound": False, "mcp_login_proven": False,
                      "model_executed": False, "delegation": False,
                      "global_management_api_changed": False,
                      "test_window_seconds": 600, "started_at": self.started,
                      "ended_at": time.time(), "scope_closed": True}
            return result
        finally:
            self.closed = True
            fd = self.io.private_directory(self.root)
            try:
                self.io.publish(fd, "agent-operator-trial-" + scope_id + ".closed.json",
                    self.io.encode(result or {"status": "UNKNOWN_QUERY_ONLY", "scope_closed": True}))
            finally:
                os.close(fd)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", required=True)
    parser.add_argument("--gateway-pid", required=True, type=int)
    parser.add_argument("--link-id", required=True)
    parser.add_argument("--approval-reference", required=True)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--readback-only", action="store_true")
    args = parser.parse_args()
    try:
        trial = OriginalHostTrial(backend=args.backend, gateway_pid=args.gateway_pid,
            link_id=args.link_id, approval_reference=args.approval_reference)
        if args.preflight:
            trial.live_user()
            print(json.dumps({"status": "ORIGINAL_HOST_TRIAL_PREFLIGHT_PASS",
                "store_type": type(trial.store).__name__, "live_named_admin_verified": True,
                "link_is_identity_history_only": True,
                "identity_link_expired": trial.link["expires_at"] <= time.time(),
                "agent_already_exists": trial.store.exists(TEST_AGENT_NAME, user_id=trial.user_id),
                "scope_issued": False, "definition_created": False}, sort_keys=True))
        else:
            operation = trial.readback_only() if args.readback_only else trial.run()
            print(json.dumps(asyncio.run(operation), sort_keys=True))
    except Exception as error:
        print(json.dumps({"status": "STOPPED_OR_UNKNOWN_QUERY_ONLY",
                          "error_type": type(error).__name__, "error": str(error)}))
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
