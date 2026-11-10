"""Owner-bound Auth0 access-token verification for synthetic MCP tools."""
import asyncio
import os
import jwt
from mcp.server.auth.provider import AccessToken


class Auth0Verifier:
    def __init__(self):
        self.issuer = 'https://' + os.environ['AUTH0_DOMAIN'].rstrip('/') + '/'
        self.audience = os.environ['AUTH0_AUDIENCE']
        self.client_id = os.environ['XJ_AUTH0_CLIENT_ID']
        self.jwks = jwt.PyJWKClient(self.issuer+'.well-known/jwks.json', timeout=3)

    def verify_sync(self, token):
        allowed = os.getenv('XJ_AUTH0_ALLOWED_SUBJECT', '')
        if os.getenv('VERCEL_ENV') != 'preview' or not allowed or not token:
            return None
        try:
            key = self.jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(token, key.key, algorithms=['RS256'],
                                issuer=self.issuer, audience=self.audience,
                                options={'require':['iss','aud','exp','iat','sub']})
            scopes = claims.get('scope', '').split()
            if claims.get('azp') != self.client_id or claims['sub'] != allowed:
                return None
            if 'synthetic:events' not in scopes:
                return None
            return AccessToken(token=token, client_id=self.client_id, scopes=scopes,
                               expires_at=claims['exp'], subject=claims['sub'], claims=claims)
        except (jwt.PyJWTError, ValueError, TypeError):
            return None

    async def verify_token(self, token):
        return await asyncio.to_thread(self.verify_sync, token)
