# G2 single-shot WIF -> STS -> Signer SA -> KMS admission gate

Status: NOT EXECUTED. Gate1 DENY. No token or credential is stored here.

Verified baseline (2026-10-28): Vercel Yoyo Preview OIDC claims diagnostic matches expected issuer/audience/subject; this diagnostic does not verify JWT signatures. Google WIF pool and provider are disabled. Signer SA has one workloadIdentityUser principal binding. KMS key version 1 is ENABLED and signer role is bound to the broker signer SA.

## Mandatory G0 before any enable
- Confirm exact Git SHA, protected Preview, runtime OIDC availability and audience.
- Confirm operator can actually update/disable BOTH pool and provider and revoke the exact IAM binding. CLI --help is not sufficient.
- Establish an external, atomic, durable one-shot claim with expiry and compare-and-set. A process-local boolean is insufficient across serverless instances.
- Verify a trusted server-side trigger that is not publicly callable, and bound to that one-shot claim.
- Prepare durable cleanup evidence even if the runtime crashes. A serverless finally block alone is insufficient.
- Confirm maximum token lifetime, cost ceiling, logging redaction and no replay.
- Validate expected STS audience and allowed audiences against official Google and Vercel APIs.
- Keep Gate1, D2, Receiver and Host in DENY / disconnected states.

## Execution, only after all gates PASS
1. Take immutable pre-state snapshot and confirm one-shot lock.
2. Temporarily enable only the existing WIF pool/provider; record exact operation IDs.
3. Obtain a fresh Vercel OIDC token inside protected Preview runtime; never log or return token.
4. Exchange token at Google STS using the exact workload provider audience; obtain federated access token.
5. Impersonate the existing broker signer service account using the supported IAM Credentials API.
6. Sign a fixed non-sensitive digest once using KMS asymmetricSign version 1.
7. Fetch public key and verify signature locally; record only digest, public key version, result and redacted IDs.
8. Disable provider and pool, remove only trial IAM grant, read back both controls and the SA policy.
9. If any step fails, do not retry signing. Revoke/disable through independent control plane and mark UNKNOWN until verified.
10. Archive redacted raw responses, SHA256, execution and billing evidence. Do not label PASS until native STS, IAMCredentials, KMS and cleanup evidence all exist.

## Current blockers
- No independent atomic single-use claim or trusted trigger has been verified.
- No proven independent rollback executor or crash recovery has been verified.
- No native STS/KMS signature has been executed.

DO NOT enable WIF or run a signature from this document alone.
