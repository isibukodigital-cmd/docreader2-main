# Deployment Architecture

## Supported boundary

The preview has explicit deployment configuration:

- `DOCQA_DEPLOYMENT_MODEL`
  - `shared-hosted`: one application deployment serves multiple tenants.
  - `dedicated-customer`: one application deployment is reserved for one customer.
  - `local-preview`: local evaluation or development.
- `DOCQA_AUTH_PROVIDER`: `supabase` or `cognito`.
- `DOCQA_DOCUMENT_STORE`: `supabase` or `postgres`. Direct PostgreSQL uses
  `DOCQA_DATABASE_URL` and a bounded connection pool.

Unknown values fail during startup. This prevents a deployment from falling back
to an unvalidated identity or storage provider.

## Tenant isolation rules

Every document and retrieval operation must remain scoped to a tenant. The
application resolves the tenant from server-validated identity metadata and
forbids mismatches with tenant hostname branding. Search, listing, ingestion,
deletion, ACL updates, membership registration, usage, and audit writes must
continue to pass the tenant identifier to the implementation.

The service-role key must only be used by the server. It must never be placed in
browser code or returned by an API response.

## Replaceable interfaces

`src/docqa/deployment.py` defines the deployment contract and `Protocol`
interfaces for authentication and document storage. Supabase remains the
default implementation. Direct PostgreSQL transport is implemented against the
same RPC contract; any provider configuration must preserve the same tenant,
authorization, and audit guarantees rather than only matching response shapes.

The append-only identity-registry migration separates application user
records from Supabase Auth. It backfills and synchronizes Supabase user identity,
email-verification status, and trusted app metadata into an application-owned
table. API authorization now reads tenant, group, and role metadata from that
registry; changes are synchronized back to Supabase Auth during this transition.
The business SQL functions and application foreign keys now use the identity
registry instead of Supabase's `auth.users`. Supabase and Cognito token
verification are supported. Cognito uses Hosted UI authorization-code/PKCE in
the browser and verifies both access and identity tokens on the API. AWS
resources and Cognito identity-provider configuration must still be provisioned
separately.

For direct PostgreSQL storage, set `DOCQA_DOCUMENT_STORE=postgres` and
`DOCQA_DATABASE_URL` to a TLS-enabled connection URI. The database must expose
the application's `public.docqa_*` functions and the `extensions.vector(384)`
type. Initialize a fresh database with the one-command installer below; do not
run it against an existing manually migrated database.

## Supabase deployment

1. Configure `SUPABASE_URL`, `SUPABASE_ANON_KEY`, and
   `SUPABASE_SERVICE_ROLE_KEY`.
2. For a fresh database, set `SUPABASE_DB_URL` to a database-owner connection
   URI and run `python scripts/setup_database.py --provider supabase`. The setup
   script applies the ordered migrations and records them in
   `public.docqa_schema_migrations`. Existing Supabase deployments should
   continue using their established migration workflow.
3. Configure `DOCQA_DEPLOYMENT_MODEL`, `DOCQA_AUTH_PROVIDER`, and
   `DOCQA_DOCUMENT_STORE`.
4. Use HTTPS and test tenant/ACL isolation before accepting customer documents.
5. Keep hosted-model keys server-side and disclose third-party provider access.
6. Configure the reverse proxy or hosting ingress to reject request bodies above
   21 MiB. The application enforces the same limit for document uploads, limits
   files to 20 MiB, and rejects PDFs over 500 pages or taking longer than 30
   seconds to parse.
7. Organization members must refresh their Microsoft group sync at least every
   12 hours. The app uses transitive group membership; only active directory
   roles satisfy organization-request authorization, not inactive PIM eligibility.

## One-command PostgreSQL setup

Supabase, Neon, and RDS are PostgreSQL providers, so the versioned schema can be
installed through one setup command. For a fresh Neon/RDS database, provide a
database-owner URI through `DOCQA_DATABASE_URL` and run:

```powershell
python -m pip install "psycopg[binary]>=3.2"
python scripts/setup_database.py --provider postgres
```

The script asks you to confirm the target database, applies pending files from
`supabase/migrations/` in filename order, and records successful migrations so
the same command can apply later updates. For non-Supabase PostgreSQL it creates
a temporary compatibility schema needed by historical migrations and removes
it after verifying that active functions and foreign keys no longer depend on
`auth.users`. The application database therefore has no Supabase Auth schema
dependency after setup. Use `--yes` only in controlled automation.

This installer is for fresh databases or databases already installed by this
installer. Do not run it against an existing manually migrated database; use
that deployment's current migration mechanism instead. The direct PostgreSQL
runtime adapter and Cognito auth are available. AWS infrastructure provisioning
and deployment remain separate work.

## Cognito and Microsoft Graph authentication

Set `DOCQA_AUTH_PROVIDER=cognito` and configure
`DOCQA_COGNITO_REGION`, `DOCQA_COGNITO_USER_POOL_ID`,
`DOCQA_COGNITO_CLIENT_ID`, and `DOCQA_COGNITO_DOMAIN`. The Cognito app client
must enable the authorization-code grant, PKCE, and the `openid`, `email`, and
`profile` scopes. Add the application origin with a trailing slash as both its
callback and sign-out URL; `/v1/auth/config` derives these URLs from the request
origin. Configure Google and Microsoft as Cognito identity providers and map
Microsoft tenant and user IDs to `custom:tenant_id` and
`custom:microsoft_user_id` so organization onboarding can match Graph identities.

Microsoft Graph authorization uses a separate Entra public-client application
with no client secret. Set `DOCQA_MICROSOFT_GRAPH_CLIENT_ID`, register the same
application-origin callback URL as a **single-page application** redirect URI,
and grant delegated `User.Read`, `Group.Read.All`, and
`RoleManagement.Read.Directory` permissions with the required tenant consent.
Graph tokens are held in browser memory and sent to the API only for requested
Graph operations. Verify sign-in, callback/logout URLs, Graph consent, and
organization identity mapping against real test tenants before production.
The AWS ALB, private EC2, RDS, Cognito, and ECR Terraform configuration is in
[`deploy/aws/README.md`](./deploy/aws/README.md). It is not applied automatically;
review its cost notes and plan before provisioning.

## Backup and restore

Set and document RPO/RTO targets with the selected Supabase plan before production
use. Supabase backups and point-in-time recovery must be enabled by the deployment
operator; application exports are not a database backup because they omit
embeddings and Auth-provider state.

Before every schema or application release:

1. Confirm the Supabase backup/PITR status and retention window.
2. Record the migration timestamp through which the database is known to be valid.
3. Export release configuration without secrets:
   `DOCQA_DEPLOYMENT_MODEL`, provider names, model IDs, embedding model and limits.
4. Run the CI migration and Python suites against a disposable database.

Restore procedure:

1. Stop writes or route traffic to maintenance mode.
2. Restore the database/backup into a new project or recovery instance.
3. Verify Auth users, workspaces, documents, ACLs, audit events and migration state.
4. Run tenant-isolation and SQL integration checks against the recovery instance.
5. Restore the matching application release and secret references.
6. Promote only after `/health/live`, `/health/ready`, `/metrics`, and smoke tests pass.
7. Test identity sign-in and one permitted and one denied document query.

For a PostgreSQL-level restore drill, restore Supabase/PITR output into a
disposable local recovery instance, set `DOCQA_RESTORE_DATABASE_URL`, and run:

```powershell
./tests/run_restore_test.ps1
```

The script refuses non-local recovery hosts and runs the Supabase-compatible
stand-ins, every migration, and the tenant/ACL integration assertions. Record:
restore start, recovery point, restore completion, application cutover, RTO,
RPO, and one permitted plus one denied tenant query result.

Do not run a restore as a destructive repair in the original production database
until the recovery instance is verified. Supabase provider retention and backups
may outlive application-level deletion.

## Migration and rollback

- Supabase migrations are append-only. Never edit or remove migrations already
  applied to a database.
- Apply `supabase/migrations/*.sql` in filename order using a transaction-safe
  operator process. Stop traffic if a migration can briefly remove RPC compatibility.
- Backward compatibility should be preserved where possible: deploy a compatible
  application first, apply the migration, then enable the new behavior.
- For identity-registry migrations, apply the additive database migrations first
  and then deploy the API version that calls the new identity RPCs.
- If an application release fails, redeploy the previous application image/config.
  Do not reverse a database migration unless its SQL has a separately tested
  down-migration and a backup restore is not safer.
- Migration 15 is a database function replacement for HNSW recall. Verify
  `hnsw.iterative_scan` support on the deployed pgvector version before rollout.

## Release checklist

1. Rotated and validated secrets; no secret values are in source or deployment logs.
2. `python -m pytest -q` passes.
3. Disposable PostgreSQL migration integration passes.
4. Tenant, ACL and cross-tenant search negative tests pass.
5. Hosted providers and model IDs are explicitly verified.
6. Ingress TLS, body limits and request timeouts are configured.
7. `/health/live`, `/health/ready`, and `/metrics` pass monitoring checks.
8. Prometheus/Grafana dashboards and alert rules are loaded.
9. Supabase backup/PITR status and retention are confirmed.
10. Rollback image/config and database recovery owner are identified.

## Production ingress and observability stack

The `deploy/` directory provides Caddy, Prometheus, Grafana and Alertmanager
configuration:

1. Copy `deploy/.env.caddy.example`, `.env.app.example`, and `.env.grafana.example`
   to `.env.caddy`, `.env.app`, and `.env.grafana`.
   Copy `.env.alerting.example` to `.env.alerting` and set an approved Slack,
   Teams, PagerDuty or incident-management webhook relay URL. A file receiver
   is intentionally not deployed.
2. Set a real `DOCQA_DOMAIN`, Supabase keys and Grafana administrator password.
3. Replace `GF_SECURITY_ADMIN_PASSWORD` and keep Grafana loopback-bound.
4. Start the stack:
   `cd deploy && docker compose up -d --build`
5. Caddy serves public HTTPS on ports 80/443 and enforces a 21 MiB request body.
   Grafana and Prometheus bind only to `127.0.0.1`.
6. `/metrics` is blocked at the public Caddy ingress and scraped only over the
   internal Docker observability network.
7. Run `deploy/validate.ps1` on a host with Docker and PowerShell to verify:
   Compose configuration, Prometheus, Grafana, application readiness, and
   Alertmanager API/notification configuration. The script cannot prove that
   an external relay reached a person.

Before production use, verify in the external relay that firing and resolution
notifications reached a real person or on-call queue.

## Azure/Microsoft Entra migration boundary

Azure support is not declared as available until it is implemented and tested.
Before enabling it:

- Decide shared versus dedicated hosting and identity-tenant mapping.
- Validate Microsoft tenant claims and personal-account rejection.
- Implement document-storage transactions and tenant-scoped RPC equivalents.
- Exercise tenant isolation, account linking, rollback, and data export.
- Replace shared service-role credentials with the approved managed identity or
  secret-management strategy.
