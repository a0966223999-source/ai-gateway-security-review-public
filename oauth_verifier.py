"""Owner-bound Auth0 access-token verification for synthetic MCP tools.

Any Auth0 application for this API is accepted, including Dynamic Client
Registration clients whose ids are not known in advance. Signature, issuer,
exp, iat, the allowed subject, the synthetic:events scope, and the preview
guard still apply. AccessToken.client_id is the token's azp.

Accepted audiences are AUTH0_AUDIENCE and the protected-resource URL this
server advertises. XJ_AUTH0_EXTRA_AUDIENCES may add more; unset or blank
adds none, so the advertised /mcp URL needs no new environment variable.

XJ_AUTH0_ALLOWED_CLIENT_IDS is an optional comma-separated restriction on
additional clients. Unset or blank accepts them. XJ_AUTH0_CLIENT_ID is
always accepted.
"""
import asyncio
import os
import re
import jwt
from mcp.server.auth.provider import AccessToken

# Auth0 application ids and Dynamic Client Registration ids (tpc_...).
_AZP = re.compile(r'^[A-Za-z0-9_-]{1,200}$')


def protected_resource(env=None):
    """Return the host pair and URL advertised at /.well-known/oauth-protected-resource."""
    env = os.environ if env is None else env
    hostname = env['VERCEL_URL']
    if not hostname.endswith('.vercel.app') or '/' in hostname:
        raise ValueError('INVALID_RESOURCE_HOST')
    public_hostname = env.get('XJ_MCP_PUBLIC_HOST', hostname)
    if not public_hostname.endswith('.vercel.app') or '/' in public_hostname:
        raise ValueError('INVALID_PUBLIC_RESOURCE_HOST')
    return hostname, public_hostname, 'https://' + public_hostname + '/mcp'


class Auth0Verifier:
    def __init__(self):
        self.issuer = 'https://' + os.environ['AUTH0_DOMAIN'].rstrip('/') + '/'
        self.audience = os.environ['AUTH0_AUDIENCE']
        self.client_id = os.environ['XJ_AUTH0_CLIENT_ID']
        self.resource = protected_resource()[2]
        self.jwks = jwt.PyJWKClient(self.issuer+'.well-known/jwks.json', timeout=3)

    def _accepted_audiences(self):
        # AUTH0_AUDIENCE stays required. The advertised resource is always
        # included. A blank extra list does not remove either one.
        accepted = []
        for value in (self.audience, self.resource):
            if value not in accepted:
                accepted.append(value)
        raw = os.getenv('XJ_AUTH0_EXTRA_AUDIENCES', '')
        for item in raw.split(','):
            item = item.strip()
            if item and item not in accepted:
                accepted.append(item)
        return accepted

    def _azp_allowed(self, azp):
        if not isinstance(azp, str) or not azp:
            return False
        if azp == self.client_id:
            return True
        if _AZP.fullmatch(azp) is None:
            return False
        listed = {item.strip() for item in os.getenv('XJ_AUTH0_ALLOWED_CLIENT_IDS', '').split(',') if item.strip()}
        # Empty allowlist is the default: accept the dynamic client.
        return not listed or azp in listed

    def verify_sync(self, token):
        allowed = os.getenv('XJ_AUTH0_ALLOWED_SUBJECT', '')
        if os.getenv('VERCEL_ENV') != 'preview' or not allowed or not token:
            return None
        try:
            key = self.jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(token, key.key, algorithms=['RS256'],
                                issuer=self.issuer, audience=self._accepted_audiences(),
                                options={'require':['iss','aud','exp','iat','sub']})
            raw_scope = claims.get('scope', '')
            if not isinstance(raw_scope, str) or not self._azp_allowed(claims.get('azp')) or claims['sub'] != allowed:
                return None
            scopes = raw_scope.split()
            if 'synthetic:events' not in scopes:
                return None
            return AccessToken(token=token, client_id=claims['azp'], scopes=scopes,
                               expires_at=claims['exp'], subject=claims['sub'], claims=claims)
        except (jwt.PyJWTError, ValueError, TypeError):
            return None

    async def verify_token(self, token):
        return await asyncio.to_thread(self.verify_sync, token)
