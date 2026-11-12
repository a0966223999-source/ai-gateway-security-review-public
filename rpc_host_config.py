"""Original service deployment config. No keys, credentials, or activation writes."""
import math
import os


def host_rpc_config():
    """Read explicit trusted deployment settings; default all new tools disabled."""
    config = {
        'budgets_verified': os.getenv('XJ_SOURCE_RPC_BUDGETS_VERIFIED') == 'true',
        'diagnostic_enabled': os.getenv('XJ_SOURCE_RPC_DIAGNOSTIC_ENABLED') == 'true',
    }
    numbers = {
        'max_wait_seconds': ('XJ_SOURCE_RPC_MAX_WAIT_SECONDS', 30),
        'function_budget_seconds': ('XJ_SOURCE_RPC_FUNCTION_BUDGET_SECONDS', None),
        'connector_budget_seconds': ('XJ_SOURCE_RPC_CONNECTOR_BUDGET_SECONDS', None),
        'diagnostic_max_wait_seconds': ('XJ_SOURCE_RPC_DIAGNOSTIC_MAX_WAIT_SECONDS', 5),
    }
    for name, (key, default) in numbers.items():
        raw = os.getenv(key)
        try:
            value = float(raw) if raw is not None else default
            if value is not None and (not math.isfinite(value) or value <= 0):
                raise ValueError('INVALID_BOUND')
        except (TypeError, ValueError):
            value = None
        config[name] = value
    return config
