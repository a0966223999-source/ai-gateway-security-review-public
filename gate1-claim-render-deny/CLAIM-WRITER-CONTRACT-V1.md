# Claim Writer boundary contract V1

Status: DESIGN_ONLY / DENY. This is not a deployed writer.

## Request
POST /internal/claim, TLS and trusted ingress only.
Authorization: signed OIDC token; verify issuer https://oidc.vercel.com/yoyo-5560, audience https://vercel.com/yoyo-5560, exp/iat, sub and exact team/project/preview claims against trusted JWKS. Never accept a caller-provided "verified" boolean.
Body: exact JSON object {"trial_id": opaque 16..128 chars, "digest": lowercase SHA256 hex}. Reject additional fields, excessive body, malformed JSON, missing bearer, wrong principal.

## Database
Connect only to Neon branch br-patient-cake-b8nstq1s, database neondb. Parameterized SELECT xj_gate1_once.claim_once($1,$2) with bounded timeouts and one transaction. No arbitrary SQL endpoint. On commit uncertainty, return UNKNOWN_DENY and never automatically retry. Never expose DB credentials to Vercel.
Known blocker: Neon role xj_gate1_once_executor inherits neon_superuser; direct INSERT effective privilege true. Do not connect that role to runtime. Need independently verified credential and isolated writer before G0 PASS.

## Response
Only status DENY / CLAIMED_NOT_AUTHORIZED / UNKNOWN_DENY. Never return an ALLOW, KMS signature, JWT, DB URL, or secret. Claim success alone cannot authorize sign.

## Safety tests before deployment
Authentic JWT verification including JWKS rotation; wrong issuer/audience/sub; expired/future token; replay; concurrent distinct DB connections; ACK lost after commit; rollback/stop and independent readback; no KMS or Host effects.

## Rollback
Existing Render service srv-dbgfjsl9fdbs73deqo50 at deploy dep-dbgfjt59fdbs73deqom0, source SHA 1a28cc25c089e532ef2ee33c81e9a1031a5b9cca; autoDeploy off. Do not deploy this design.
