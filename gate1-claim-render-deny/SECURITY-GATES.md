# Gate1 Claim Service security gates

Current deployment: DENY-only, no DB or KMS credentials. No runtime claim access.

Before connecting:
1. Authenticate Vercel-origin requests using cryptographically verified OIDC issuer, audience, subject, expiry, JWKS and fresh token; do not trust user-supplied identity fields.
2. Avoid treating project/environment OIDC as deployment-specific identity.
3. Use separate protected DB execution service; never pass Neon owner credentials to Vercel. Neon-generated role inherited neon_superuser and fails least privilege.
4. Only allow parameterized claim_once(trial_id,digest), no arbitrary SQL; durable atomic uniqueness, unknown result DENY/no retry.
5. Require verified, independent stop/rollback and native concurrent-connection test before any KMS sign.
6. Keep Gate1 DENY, no WIF/IAM change, no Host, no production DB.

Rollback: retain existing Render deploy at SHA 1a28cc25c089e532ef2ee33c81e9a1031a5b9cca; auto deploy off. Do not deploy changes before G0 PASS.
