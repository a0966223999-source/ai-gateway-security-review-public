"""Deployment-owned generation settings. No subscription identity is bundled."""
import os
import re
from cycle_transport import validate_policy


def host_generation_config():
    if os.getenv('XJ_GENERATION_ENABLED') != 'true':
        return {'enabled': False}
    try:
        sid = os.environ['XJ_GENERATION_SUBSCRIPTION_ID']
        if not re.fullmatch(r'sub_[0-9a-f]{64}', sid):
            raise ValueError('FIXED_ORIGINAL_SUBSCRIPTION_REQUIRED')
        values = {}
        for key, env in (
                ('round_budget_seconds','XJ_GENERATION_ROUND_BUDGET_SECONDS'),
                ('max_generations','XJ_GENERATION_MAX_GENERATIONS'),
                ('min_interval_seconds','XJ_GENERATION_MIN_INTERVAL_SECONDS')):
            raw = os.environ[env]
            if not re.fullmatch(r'[0-9]{1,4}', raw):
                raise ValueError('EXPLICIT_INTEGER_POLICY_REQUIRED')
            values[key] = int(raw)
        policy = validate_policy(dict(wait_seconds=80, **values))
        return {'enabled': True, 'policy': policy, 'subscription_id': sid}
    except (KeyError, ValueError, TypeError):
        # A missing/malformed extension setting cannot break the original MCP.
        return {'enabled': False}
