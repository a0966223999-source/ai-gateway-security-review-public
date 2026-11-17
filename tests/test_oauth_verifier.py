"""Offline Auth0 verifier tests. Local RSA keys only; no JWKS or network."""
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from oauth_verifier import Auth0Verifier


DOMAIN = 'dev-qatwac1c6lbnvv4d.jp.auth0.com'
AUDIENCE = 'https://example.vercel.app/mcp'
CONFIGURED = 'configuredClientId'
DYNAMIC = 'tpc_8f3a9c0b1d2e4f567890abcd'
SUBJECT = 'auth0|owner'


class VerifierTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.public = cls.private.public_key()
        cls.other = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        self.env = patch.dict(os.environ, {
            'AUTH0_DOMAIN': DOMAIN,
            'AUTH0_AUDIENCE': AUDIENCE,
            'XJ_AUTH0_CLIENT_ID': CONFIGURED,
            'XJ_AUTH0_ALLOWED_SUBJECT': SUBJECT,
            'VERCEL_ENV': 'preview',
        }, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        os.environ.pop('XJ_AUTH0_ALLOWED_CLIENT_IDS', None)
        self.verifier = Auth0Verifier()
        self.verifier.jwks.get_signing_key_from_jwt = lambda token: SimpleNamespace(key=self.public)

    def token(self, key=None, **overrides):
        now = int(time.time())
        claims = {
            'iss': 'https://' + DOMAIN + '/',
            'aud': AUDIENCE,
            'exp': now + 600,
            'iat': now,
            'sub': SUBJECT,
            'azp': DYNAMIC,
            'scope': 'synthetic:events',
        }
        claims.update(overrides)
        claims = {name: value for name, value in claims.items() if value is not None}
        return jwt.encode(claims, key or self.private, algorithm='RS256')

    def test_dynamic_client_accepted_and_client_id_is_azp(self):
        raw = self.token()
        access = self.verifier.verify_sync(raw)
        self.assertIsNotNone(access)
        self.assertEqual(access.client_id, DYNAMIC)
        self.assertNotEqual(access.client_id, CONFIGURED)
        self.assertEqual(access.token, raw)
        self.assertEqual(access.subject, SUBJECT)
        self.assertIn('synthetic:events', access.scopes)
        self.assertEqual(access.claims['azp'], DYNAMIC)
        self.assertGreater(access.expires_at, int(time.time()))

    def test_configured_client_still_accepted(self):
        access = self.verifier.verify_sync(self.token(azp=CONFIGURED, scope='openid synthetic:events'))
        self.assertEqual(access.client_id, CONFIGURED)
        self.assertEqual(access.scopes, ['openid', 'synthetic:events'])

    def test_configured_client_accepted_outside_dynamic_id_pattern(self):
        self.verifier.client_id = 'configured.client'
        access = self.verifier.verify_sync(self.token(azp='configured.client'))
        self.assertEqual(access.client_id, 'configured.client')

    def test_blank_allowlist_accepts_dynamic_client(self):
        for value in ('', '   ', ',,,', ' , '):
            os.environ['XJ_AUTH0_ALLOWED_CLIENT_IDS'] = value
            access = self.verifier.verify_sync(self.token())
            self.assertEqual(getattr(access, 'client_id', None), DYNAMIC, value)

    def test_allowlist_can_name_dynamic_client(self):
        os.environ['XJ_AUTH0_ALLOWED_CLIENT_IDS'] = ' other , ' + DYNAMIC + ' '
        access = self.verifier.verify_sync(self.token())
        self.assertEqual(access.client_id, DYNAMIC)

    def test_allowlist_rejects_unlisted_client_but_keeps_configured(self):
        os.environ['XJ_AUTH0_ALLOWED_CLIENT_IDS'] = 'other-client'
        self.assertIsNone(self.verifier.verify_sync(self.token()))
        configured = self.verifier.verify_sync(self.token(azp=CONFIGURED))
        self.assertEqual(configured.client_id, CONFIGURED)

    def test_audience_list_containing_api_is_accepted(self):
        access = self.verifier.verify_sync(self.token(aud=[AUDIENCE, 'https://' + DOMAIN + '/userinfo']))
        self.assertEqual(access.client_id, DYNAMIC)

    def test_rejects_wrong_subject_scope_azp_and_guards(self):
        self.assertIsNone(self.verifier.verify_sync(self.token(sub='auth0|someone-else')))
        self.assertIsNone(self.verifier.verify_sync(self.token(scope='openid profile')))
        self.assertIsNone(self.verifier.verify_sync(self.token(scope='synthetic:events-extra')))
        self.assertIsNone(self.verifier.verify_sync(self.token(azp=None)))
        self.assertIsNone(self.verifier.verify_sync(self.token(azp='')))
        self.assertIsNone(self.verifier.verify_sync(self.token(azp='tpc bad')))
        self.assertIsNone(self.verifier.verify_sync(self.token(azp='tpc/../other')))
        self.assertIsNone(self.verifier.verify_sync(self.token(azp='tpc.dynamic')))
        self.assertIsNone(self.verifier.verify_sync(self.token(azp=12345)))
        self.assertIsNone(self.verifier.verify_sync(self.token(scope=['synthetic:events'])))
        self.assertIsNone(self.verifier.verify_sync(''))
        self.assertIsNone(self.verifier.verify_sync(None))
        os.environ['XJ_AUTH0_ALLOWED_SUBJECT'] = ''
        self.assertIsNone(self.verifier.verify_sync(self.token()))
        os.environ['XJ_AUTH0_ALLOWED_SUBJECT'] = SUBJECT
        os.environ['VERCEL_ENV'] = 'production'
        called = []
        self.verifier.jwks.get_signing_key_from_jwt = lambda token: called.append(token)
        self.assertIsNone(self.verifier.verify_sync(self.token()))
        self.assertEqual(called, [])

    def test_signature_issuer_audience_exp_and_iat_still_required(self):
        now = int(time.time())
        self.assertIsNone(self.verifier.verify_sync(self.token(key=self.other)))
        self.assertIsNone(self.verifier.verify_sync(self.token(iss='https://evil.example/')))
        self.assertIsNone(self.verifier.verify_sync(self.token(aud='https://evil.example/mcp')))
        self.assertIsNone(self.verifier.verify_sync(self.token(exp=now - 10, iat=now - 60)))
        self.assertIsNone(self.verifier.verify_sync(self.token(iat=None)))
        forged = jwt.encode({'iss': 'https://' + DOMAIN + '/', 'aud': AUDIENCE, 'exp': now + 600,
                             'iat': now, 'sub': SUBJECT, 'azp': DYNAMIC, 'scope': 'synthetic:events'},
                            'not-the-rsa-key-and-long-enough!', algorithm='HS256')
        self.assertIsNone(self.verifier.verify_sync(forged))

    def test_configured_client_env_still_required(self):
        os.environ.pop('XJ_AUTH0_CLIENT_ID')
        with self.assertRaises(KeyError):
            Auth0Verifier()

    async def test_verify_token_returns_dynamic_client_id(self):
        access = await self.verifier.verify_token(self.token())
        self.assertEqual(access.client_id, DYNAMIC)
        self.assertIsNone(await self.verifier.verify_token(self.token(sub='auth0|nope')))


if __name__ == '__main__':
    unittest.main()
