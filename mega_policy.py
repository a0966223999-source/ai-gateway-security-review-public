"""Per-AI allowlist for the MEGA 知識庫 bridge (no per-operation phone approval).

Owner rulings 2026-11-21: listed AIs may read and change anything inside 知識庫
(create new files, overwrite files keeping one previous version) without a
fingerprint per operation. Each AI is identified by its Auth0-verified OAuth
client ID and must be listed here; each reports its own ID via gateway_whoami.
Default off.

  XJ_MEGA_KB_CLIENTS_ENABLED=true
  XJ_MEGA_KB_CLIENTS_JSON   {"<client_id>": {"member": "GPT", "operations": ["read", "create", "update"]}}
                            update = overwrite a text file in 知識庫 (keeps one previous version)
  XJ_MEGA_KB_DAILY_CREATES  creates+updates per client per UTC day (default 50, max 1000)

The deployment Owner remains the OAuth subject gate. Unknown clients, unknown
operations or a malformed list mean "not allowed". The list is read from the
deployment's environment, so removing a client takes effect on the next deploy.
"""
from __future__ import annotations

import json
import os
import re

OPERATIONS = ('read', 'create', 'update')
CLIENT = re.compile(r'[A-Za-z0-9_-]{1,200}\Z')
DEFAULT_DAILY = 50


class MegaClientPolicy:
    def __init__(self, env, owner_of_deployment):
        self.env = env
        self.owner_of_deployment = owner_of_deployment
        try:
            daily = int(env.get('XJ_MEGA_KB_DAILY_CREATES') or DEFAULT_DAILY)
        except ValueError:
            daily = 0
        self.daily_creates = daily if 0 < daily <= 1000 else 0

    def _clients(self):
        try:
            data = json.loads(self.env.get('XJ_MEGA_KB_CLIENTS_JSON') or '{}')
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def member(self, owner, client_id, operation):
        """Canonical member name when this verified client may do operation, else None."""
        try:
            if (self.env.get('XJ_MEGA_KB_CLIENTS_ENABLED') != 'true'
                    or owner != self.owner_of_deployment()
                    or operation not in OPERATIONS
                    or not isinstance(client_id, str) or not CLIENT.fullmatch(client_id)):
                return None
            entry = self._clients().get(client_id)
            name, ops = entry['member'], entry['operations']
            if isinstance(name, str) and name.strip() and isinstance(ops, list) and operation in ops:
                return name.strip()
        except Exception:
            pass
        return None


    def describe(self, owner, client_id):
        """For gateway_whoami: what this verified client may do (never the list itself)."""
        ops = [op for op in OPERATIONS if self.member(owner, client_id, op) is not None]
        return {'member': self.member(owner, client_id, ops[0]) if ops else None,
                'operations': ops, 'daily_writes': self.daily_creates}


def policy_from_env(env=None, owner_of_deployment=None):
    env = os.environ if env is None else env
    if env.get('XJ_MEGA_KB_CLIENTS_ENABLED') != 'true':
        return None
    if owner_of_deployment is None:
        from mail_hook import owner_principal
        owner_of_deployment = lambda: owner_principal(env)  # noqa: E731
    return MegaClientPolicy(env, owner_of_deployment)
