"""Opt-in consent routes on the ORIGINAL Gateway application.

Install alongside the existing auth/CSRF middleware and Source lifespan. No
new login, token, server, registry or checkpoint-link upgrade is introduced.
"""
import asyncio
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from deer_agent_management import AgentManagementDenied
from deer_management_consent import ManagementConsentStore


class ManagementApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation_key: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")
    names: list[str] = Field(min_length=1, max_length=25)
    actions: list[Literal["list", "read", "create"]]
    ttl_seconds: int = Field(default=600, ge=1, le=3600)
    confirmation: Literal["named-definitions-only-no-models-tools-or-delegation"]


def install_gateway_management_consent(app, *, enabled=False):
    if enabled is not True:
        return False
    from app.gateway.authz import require_auth
    from app.gateway.platform_account_link import PinnedMcpAccount
    from app.gateway.platform_source_http import PlatformSourceRuntime

    router = APIRouter(prefix="/api/threads/{thread_id}/agent-management-consents",
                       tags=["threads"])

    @app.get("/api/v1/auth/agent-management-consent", include_in_schema=False)
    async def consent_page(request: Request):
        from fastapi.responses import HTMLResponse
        from app.gateway.csrf_middleware import (
            CSRF_COOKIE_NAME, generate_csrf_token, auth_csrf_cookie_settings)
        response = HTMLResponse(Path(__file__).with_name("agent_management_consent.html").read_text(),
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                     "X-Content-Type-Options": "nosniff",
                     "Content-Security-Policy": "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        if not request.cookies.get(CSRF_COOKIE_NAME):
            secure, max_age = auth_csrf_cookie_settings(request)
            response.set_cookie(CSRF_COOKIE_NAME, generate_csrf_token(), httponly=False,
                                secure=secure, samesite="strict", max_age=max_age, path="/")
        return response

    async def authority(request, thread_id):
        runtime = getattr(request.app.state, "platform_source_runtime", None)
        if (type(runtime) is not PlatformSourceRuntime or not runtime.current()
                or type(runtime.account) is not PinnedMcpAccount):
            raise HTTPException(503, "MANAGEMENT_CONSENT_RUNTIME_NOT_BOUND")
        if getattr(request.state, "auth_source", None) != "session":
            raise HTTPException(403, "MANAGEMENT_CONSENT_INTERACTIVE_SESSION_REQUIRED")
        owner = await runtime.service(request, thread_id)._principal(request, thread_id)
        user = request.state.user
        auth = request.state.auth
        if (str(user.id) != owner or user.system_role != "admin" or user.needs_setup
                or not auth.has_permission("threads", "write")
                or not auth.has_permission("runs", "create")):
            raise HTTPException(403, "MANAGEMENT_CONSENT_NAMED_ADMIN_POLICY_REQUIRED")
        return ManagementConsentStore(runtime.root, account=runtime.account,
                                      current=runtime.current), {
            "user_id": owner, "thread_id": thread_id, "token_version": user.token_version}

    def public(record):
        return {key: record[key] for key in ("grant_id", "names", "actions", "scope",
            "created_at", "expires_at", "model_execution", "revoked", "expired")}

    async def respond(operation):
        try:
            return await operation
        except FileNotFoundError:
            raise HTTPException(404, "MANAGEMENT_CONSENT_NOT_FOUND") from None
        except AgentManagementDenied as error:
            raise HTTPException(409 if "QUERY_ONLY" in str(error) else 403, str(error)) from None
        except ValueError:
            raise HTTPException(403, "MANAGEMENT_CONSENT_ORIGINAL_AUTHORITY_DENIED") from None
        except OSError:
            raise HTTPException(503, "MANAGEMENT_CONSENT_UNKNOWN_QUERY_ONLY") from None

    @router.post("")
    @require_auth
    async def approve(thread_id: str, body: ManagementApproval, request: Request):
        async def operation():
            store, binding = await authority(request, thread_id)
            record = await asyncio.to_thread(store.approve, **binding,
                operation_key=body.operation_key, names=body.names,
                actions=body.actions, ttl_seconds=body.ttl_seconds)
            await authority(request, thread_id)
            return public(record)
        return await respond(operation())

    @router.get("/by-operation/{operation_key}")
    @require_auth
    async def query(thread_id: str, operation_key: str, request: Request):
        async def operation():
            store, binding = await authority(request, thread_id)
            record = await asyncio.to_thread(store.by_operation,
                user_id=binding["user_id"], thread_id=thread_id, operation_key=operation_key,
                verified_owner=store.account.config["owner"])
            if record["token_version"] != binding["token_version"]:
                raise AgentManagementDenied("MANAGEMENT_CONSENT_TOKEN_VERSION_CHANGED")
            await authority(request, thread_id)
            return public(record)
        return await respond(operation())

    @router.delete("/{grant_id}")
    @require_auth
    async def revoke(thread_id: str, grant_id: str, request: Request):
        async def operation():
            store, binding = await authority(request, thread_id)
            return await asyncio.to_thread(store.revoke, grant_id,
                verified_owner=store.account.config["owner"],
                user_id=binding["user_id"], thread_id=thread_id)
        return await respond(operation())

    app.include_router(router)
    return True
