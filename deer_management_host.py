"""Original registry operations after interactive consent and signed ingress.

This is a separate explicit delegated management gate, NOT the original public
/api/agents HTTP gate. It creates no session or AuthContext and never calls a
model. The public agents API remains unchanged and may stay disabled.
"""
import asyncio
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from types import SimpleNamespace

from deer_agent_management import AgentManagementDenied, _canonical
from deer_management_consent import ManagementConsentStore
from deer_management_journal import OriginalManagementJournal
from deer_management_rpc import ManagementRequest, create_payload, MODE


class ManagementDefinitionService:
    def __init__(self, *, consent_store, grant_id, agent_store, live_policy, current):
        if (type(consent_store) is not ManagementConsentStore or not callable(live_policy)
                or not callable(current) or not isinstance(grant_id, str)
                or re.fullmatch(r"[a-f0-9]{64}", grant_id) is None):
            raise AgentManagementDenied("ORIGINAL_MANAGEMENT_COMPOSITION_REQUIRED")
        self.consents, self.grant_id = consent_store, grant_id
        self.store, self.live_policy, self.current = agent_store, live_policy, current

    async def execute(self, body):
        # The ORIGINAL host already verified the exact signed ingress. This
        # method is only injected into that host, never called from public JSON.
        body = json.loads(_canonical(body))
        request = ManagementRequest.model_validate(dict(body["payload"],
                    operation_key=body["operation_key"])).scoped()
        if len(_canonical(body)) > 65536:
            raise AgentManagementDenied("MANAGEMENT_REQUEST_TOO_LARGE")
        record = await asyncio.to_thread(self.consents.active, self.grant_id,
                                        verified_owner=body["owner"])
        action = "read" if request.kind == "receipt" else request.kind
        if action not in record["actions"] or (request.name and request.name not in record["names"]):
            raise AgentManagementDenied("MANAGEMENT_CONSENT_ACTION_OR_NAME_DENIED")
        original_record = _canonical(record)

        async def authorize():
            if self.current() is not True:
                return False
            active = await asyncio.to_thread(self.consents.active, self.grant_id,
                                            verified_owner=body["owner"])
            if _canonical(active) != original_record or await self.live_policy(active) is not True:
                return False
            # Policy resolution may await external providers. Recheck durable
            # consent after that await, including expiry and revocation.
            active = await asyncio.to_thread(self.consents.active, self.grant_id,
                                            verified_owner=body["owner"])
            return (_canonical(active) == original_record and self.current() is True
                    and self.consents.clock() < active["expires_at"])

        async def check():
            if await authorize() is not True:
                raise AgentManagementDenied("MANAGEMENT_LIVE_OWNER_POLICY_DENIED")

        await check()
        user_id = record["gateway_user_id"]
        journal = OriginalManagementJournal(self.consents.root,
            binding={"mcp_owner": body["owner"], "gateway_user_id": user_id,
                     "grant_id": self.grant_id}, current=self.current,
            authorize=authorize, source_io=self.consents.io)

        async def read(name):
            await check()
            try:
                config = await asyncio.to_thread(self.store.get, name, user_id=user_id)
            except FileNotFoundError:
                raise AgentManagementDenied("ORIGINAL_AGENT_NOT_FOUND") from None
            soul = await asyncio.to_thread(self.store.get_soul, name, user_id=user_id)
            await check()
            return {"name": config.name, "description": config.description, "soul": soul,
                    "tool_groups": config.tool_groups, "skills": config.skills,
                    "allowed_subagents": config.allowed_subagents}

        if request.kind == "list":
            agents = []
            for name in record["names"]:
                if await asyncio.to_thread(self.store.exists, name, user_id=user_id):
                    agents.append(await read(name))
            result = {"status": "SCOPED_REGISTRY_READ", "agents": agents}
        elif request.kind == "read":
            result = {"status": "REGISTRY_READBACK", "agent": await read(request.name)}
        elif request.kind == "receipt":
            result = await journal.query("create", request.create_operation_key,
                                          request.payload_sha256)
        else:
            payload, payload_sha = create_payload(request)
            config = payload["config"]

            async def create():
                await check()
                # Original Store enforces name conflict, per-user location and
                # persistence. No update/upsert/delete or memory cleanup path.
                from deerflow.persistence.agents import AgentExistsError
                try:
                    await asyncio.to_thread(self.store.create, request.name, config,
                                            request.soul, user_id=user_id)
                except AgentExistsError:
                    raise AgentManagementDenied("ORIGINAL_AGENT_NAME_EXISTS_QUERY_ONLY") from None
                return {"status": "REGISTRY_SAVED_READBACK_REQUIRED", "name": request.name,
                        "model_executed": False, "delegation": False}

            result = await journal("create", request.operation_key, payload_sha, create)
            result = {**result, "payload_sha256": payload_sha,
                      "create_operation_key": request.operation_key}
        await check()
        if len(_canonical(result)) > 65536:
            raise AgentManagementDenied("MANAGEMENT_RESPONSE_TOO_LARGE_QUERY_ONLY")
        return {**result, "kind": MODE, "execution_started": False, "fixed_agent_stop_bound": False}


def project_management_user(database, record):
    """Original SQLite identity/policy projection; never reads credentials."""
    with sqlite3.connect(Path(database).as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT id,system_role,token_version,needs_setup,oauth_provider,oauth_id "
            "FROM users WHERE id=?", (record["gateway_user_id"],)).fetchone()
        thread = db.execute("SELECT user_id FROM threads_meta WHERE thread_id=?",
                            (record["thread_id"],)).fetchone()
    if (row is None or row["id"] in {"default", "internal", "system"}
            or row["system_role"] != "admin" or row["needs_setup"]
            or row["token_version"] != record["token_version"]
            or thread is None or thread["user_id"] != row["id"]):
        raise AgentManagementDenied("ORIGINAL_MANAGEMENT_LIVE_USER_DENIED")
    return SimpleNamespace(**dict(row))


def original_gateway_management(host, *, backend, gateway_pid, grant_id):
    """Trusted worker startup, bound to the CURRENT original Gateway process.

    No credentials are retained. Only original config locations/pins are read
    from its environment. Original users/thread metadata are projected read-only
    for each operation and the real original policy resolver is called.
    """
    backend = Path(backend).resolve(strict=True)
    proc = Path("/proc") / str(int(gateway_pid))
    if (Path(os.readlink(proc / "cwd")).resolve() != backend
            or b"app.gateway.app:app" not in (proc / "cmdline").read_bytes().split(b"\0")):
        raise AgentManagementDenied("ORIGINAL_MANAGEMENT_GATEWAY_PROCESS_REQUIRED")
    process_identity = (proc / "stat").read_text().split()[21]
    allowed = {k.encode() for k in ("DEER_FLOW_HOME", "DEER_FLOW_CONFIG_PATH",
                "DEER_FLOW_SOURCE_ACCOUNT_CONFIG", "DEER_FLOW_SOURCE_ACCOUNT_CONFIG_SHA256")}
    selected = {k: v for item in (proc / "environ").read_bytes().split(b"\0")
                if b"=" in item for k, v in [item.split(b"=", 1)] if k in allowed}
    for key in allowed:
        if key in selected:
            os.environ[key.decode()] = selected[key].decode()
        else:
            os.environ.pop(key.decode(), None)
    del selected
    os.chdir(backend)
    sys.path[:0] = [str(backend), str(backend / "packages/harness"), str(backend / "packages/extension-api")]
    from app.gateway.platform_source_http import default_platform_source_runtime
    from app.gateway.authz import resolve_route_permissions
    from deerflow.config.app_config import get_app_config
    from deerflow.persistence.agents import get_agent_store
    runtime = default_platform_source_runtime()
    account = runtime.account
    if (account is None or account.current() is not True
            or account.config != host.config):
        raise AgentManagementDenied("ORIGINAL_MANAGEMENT_PINNED_HOST_MISMATCH")
    config = get_app_config()
    if config.database.backend != "sqlite":
        raise AgentManagementDenied("ORIGINAL_MANAGEMENT_SQLITE_PROFILE_REQUIRED")
    database = Path(config.database.sqlite_path).resolve(strict=True)

    def current():
        try:
            return (host.check("agent") is True and account.current() is True
                    and (proc / "stat").read_text().split()[21] == process_identity
                    and Path(os.readlink(proc / "cwd")).resolve() == backend)
        except (OSError, ValueError):
            return False

    async def live_policy(record):
        user = await asyncio.to_thread(project_management_user, database, record)
        return {"threads:write", "runs:create"} <= set(
            await resolve_route_permissions(user, is_internal=False))

    return ManagementDefinitionService(consent_store=ManagementConsentStore(runtime.root,
        account=account, current=current), grant_id=grant_id, agent_store=get_agent_store(),
        live_policy=live_policy, current=current)
