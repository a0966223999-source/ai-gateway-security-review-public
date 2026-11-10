"""Optional same-MCP read-only bounded await diagnostic; never auto-installed.
No Redis, Source, event, credentials, or host callback is accessed or changed.
"""
import asyncio
import hashlib
import math
import os
import re
import time
from mcp.types import ToolAnnotations


def public_status(host_config):
    value=os.getenv('XJ_SOURCE_RPC_HOST_PUBLIC_KEY','')
    key_valid=bool(re.fullmatch('[0-9a-f]{64}',value))
    fingerprint=hashlib.sha256(bytes.fromhex(value)).hexdigest() if key_valid else None
    configured=False
    try:
        nums=[host_config[k] for k in ('max_wait_seconds','function_budget_seconds','connector_budget_seconds')]
        configured=(host_config.get('budgets_verified') is True
            and all(type(n) in (int,float) and math.isfinite(n) for n in nums)
            and 0<nums[0]<=600 and nums[0]<min(nums[1:])-5 and key_valid)
    except (KeyError,TypeError,AttributeError):pass
    return {'configured':configured,'host_public_key_sha256':fingerprint}


async def measure_await(seconds, *, principal, host_config):
    if not isinstance(host_config,dict) or host_config.get('diagnostic_enabled') is not True:
        raise ValueError('DIAGNOSTIC_DISABLED')
    limit=host_config.get('diagnostic_max_wait_seconds',5)
    if type(limit) not in (int,float) or not math.isfinite(limit) or not 0<limit<=60:
        raise ValueError('DIAGNOSTIC_HOST_LIMIT_INVALID')
    if type(seconds) not in (int,float) or not math.isfinite(seconds) or not 0<seconds<=limit:
        raise ValueError('DIAGNOSTIC_WAIT_OUTSIDE_HOST_BOUND')
    if not callable(principal):raise ValueError('ORIGINAL_PRINCIPAL_REQUIRED')
    owner=principal()
    if not isinstance(owner,str) or not owner:raise ValueError('OWNER_REQUIRED')
    before=public_status(host_config)
    start=time.monotonic()
    await asyncio.sleep(seconds)
    elapsed=time.monotonic()-start
    if principal()!=owner:raise ValueError('OWNER_CHANGED')
    after=public_status(host_config)
    return dict(after,requested_seconds=seconds,elapsed_seconds=elapsed,
        configuration_changed=(before!=after),measurement='single_bounded_async_wait',
        source_accessed=False,event_sent=False,state_written=False,
        host_reply_verified=False,end_to_end_performance_verified=False,
        observed_minimum_supported_seconds=seconds,maximum_timeout_verified=False)


def install_rpc_diagnostic(mcp, principal, oauth_meta, *, host_config):
    """Explicit optional hook on original mcp. Not enabled by install_rpc_tools."""
    if not callable(principal):raise ValueError('ORIGINAL_PRINCIPAL_REQUIRED')
    if not isinstance(host_config,dict) or host_config.get('diagnostic_enabled') is not True:
        return {'installed':False,'reason':'DIAGNOSTIC_DISABLED'}
    limit=host_config.get('diagnostic_max_wait_seconds',5)
    if type(limit) not in (int,float) or not math.isfinite(limit) or not 0<limit<=60:
        return {'installed':False,'reason':'DIAGNOSTIC_HOST_LIMIT_INVALID'}
    @mcp.tool(meta=oauth_meta,annotations=ToolAnnotations(readOnlyHint=True,
        destructiveHint=False,idempotentHint=True,openWorldHint=False))
    async def gateway_source_rpc_diagnostic(wait_seconds:float=5)->dict:
        """Read-only single await measurement (host-enabled bound, at most 60 seconds), public-key fingerprint and configuration flag. No Source/event/write or host reply test; not a timeout or full-path performance guarantee."""
        return await measure_await(wait_seconds,principal=principal,host_config=host_config)
    return {'tool':'gateway_source_rpc_diagnostic','max_wait_seconds':limit,'installed':True}
