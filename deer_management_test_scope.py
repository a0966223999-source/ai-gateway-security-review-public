"""Narrow an EXISTING management grant to one read-only test definition.

This is attenuation, never a grant issuer. A checkpoint-only binding cannot
be upgraded here. The live owner and management policy remain mandatory.
"""

from dataclasses import replace
import math
import time

from deer_agent_management import AgentManagementDenied, OriginalAgentBinding


TEST_AGENT_NAME = "qa-dr-readback-20261112"


def narrow_test_binding(binding, *, expires_at, clock=time.time):
    if (type(binding) is not OriginalAgentBinding
            or binding.authority_scope != "original-deer-agent-management"):
        raise AgentManagementDenied("EXISTING_MANAGEMENT_GRANT_REQUIRED")
    if (type(expires_at) not in (int, float) or not math.isfinite(expires_at)
            or not 0 < expires_at - clock() <= 600):
        raise AgentManagementDenied("TEST_WINDOW_MAX_600_SECONDS")

    def check():
        if clock() >= expires_at:
            raise AgentManagementDenied("MANAGEMENT_TEST_WINDOW_EXPIRED")

    async def authorize(context, action, payload):
        check()
        if action not in {"list", "read", "create"}:
            return False
        if action in {"read", "create"} and payload.get("name") != TEST_AGENT_NAME:
            return False
        if action == "create":
            for field in ("tool_groups", "skills", "allowed_subagents"):
                if tuple(payload.get(field, ())) != ():
                    return False
        result = await binding.authorize(context, action, payload)
        check()
        return result is True

    async def invoke(method, path, payload):
        check()
        if (method, path) not in {
            ("GET", "/api/agents"), ("GET", "/api/agents/" + TEST_AGENT_NAME),
            ("POST", "/api/agents"),
        }:
            raise AgentManagementDenied("MANAGEMENT_TEST_ROUTE_DENIED")
        if method == "POST" and (not isinstance(payload, dict)
                                 or payload.get("name") != TEST_AGENT_NAME):
            raise AgentManagementDenied("MANAGEMENT_TEST_NAME_DENIED")
        result = await binding.invoke(method, path, payload)
        if method == "GET" and path == "/api/agents":
            if type(result) is not dict or type(result.get("agents")) is not list:
                raise AgentManagementDenied("ORIGINAL_AGENT_LIST_INVALID")
            return {"agents": [item for item in result["agents"]
                               if isinstance(item, dict) and item.get("name") == TEST_AGENT_NAME]}
        return result

    return replace(binding, authorize=authorize, invoke=invoke)
