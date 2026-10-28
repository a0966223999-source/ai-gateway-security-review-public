# Gate1 G2 executor privilege correction evidence (2026-10-28)

Scope: Neon project gentle-voice-21415420, isolated branch br-patient-cake-b8nstq1s, database neondb. Main untouched.

Read-only observations:
- Schema xj_gate1_once, function claim_once, table claims and primary key exist.
- Function uses INSERT ON CONFLICT DO NOTHING, SECURITY DEFINER, fixed search_path.
- pg_roles reports xj_gate1_once_executor rolcanlogin=true, rolcreatedb=true, rolcreaterole=true.
- Neon management API reports authentication_method=no_login for the same role.
- neondb_owner is the connected SQL user.

Attempted correction:
ALTER ROLE xj_gate1_once_executor NOLOGIN NOCREATEDB NOCREATEROLE;
Native response: permission denied to alter role.
No successful role change. A subsequent read still showed elevated attributes.

Status: EXECUTOR_LEAST_PRIVILEGE_FAIL / ALTER_PERMISSION_DENIED / G2 NO-GO.
Do not connect this role to Vercel or use owner credentials in runtime.
Do not retry with broader credentials or touch main without a precise separate authorization and privilege analysis.

Next safe actions:
1. Determine the actual role ownership/admin-option boundary and the supported Neon management route for modifying this role.
2. If needed, create a *new* isolated least-privilege login role only after reviewing default grants and actual capabilities; never infer least privilege from its name.
3. Validate real concurrency and crash recovery without resetting a claimed trial ID.
4. Independently prove external WIF disable/revoke and readback before STS/KMS.
5. Keep WIF pool/provider disabled and Gate1 DENY.
