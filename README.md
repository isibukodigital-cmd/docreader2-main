# private-doc-qa

**Private, citation-first Q&A for organizational documents.** Use the same pipeline for policies, handbooks, product manuals, procedures, case files, and other collections. Answers cite sources and retrieval applies tenant and document permissions.

> Status: prototype (v0.2). Local evaluation uses synthetic data. The Supabase-backed API is an initial implementation, not a compliance or production-readiness claim.

## Why this exists

Organizations hold documents that should not be pasted into a public chatbot. Many "chat with your documents" demos skip three important requirements:

1. **Permission-aware retrieval.** If a user cannot open a document, the AI must not use it to answer them.
2. **Citations and honest refusals.** Every answer points to a document and page, and the system says "I couldn't find this" instead of guessing.
3. **An audit trail.** Who asked what, and which documents were used.

This project builds those three things first and measures them.

## What works today

| Feature | Status |
|---|---|
| PDF, DOCX, TXT/MD ingestion with page/section metadata | Done |
| Hybrid search (BM25 keyword + embeddings, fused with reciprocal rank fusion) | Done |
| Per-document access groups, enforced **before** ranking | Done |
| Citation-required answers; uncited answers are rejected | Done |
| "Not found" abstention | Done |
| Prompt-injection test document in the evaluation set | Done |
| JSONL audit log | Done |
| Evaluation harness (accuracy, citations, refusals, access leaks, latency) | Done |
| Pluggable models: Hugging Face (Colab GPU), Ollama (laptop/VPS), offline baseline | Done |
| Per-tenant profiles (name, tone, language, rules, refusal text, vocabulary, retrieval settings) | Done |
| Answer grounding: citation, lexical, negation, name-token, and figure/unit checks (heuristic; not proof of entailment) | Prototype |
| Supporting quote returned with each citation (for source previews) | Done |
| Supabase Auth-protected API and Supabase pgvector hybrid store | Initial implementation |
| SharePoint connector, Entra ID login, managed deployment | Roadmap |

## Deployment configuration

Deployment is explicitly declared through environment variables:

```bash
DOCQA_DEPLOYMENT_MODEL=shared-hosted  # or dedicated-customer / local-preview
DOCQA_AUTH_PROVIDER=supabase
DOCQA_DOCUMENT_STORE=supabase  # or postgres with DOCQA_DATABASE_URL
```

The startup configuration rejects unknown deployment, auth, or document-store
providers instead of silently changing tenant isolation behavior. The current
runtime supports Supabase Auth and either Supabase or direct PostgreSQL storage.
The direct PostgreSQL adapter uses the existing database RPC contract, but the
complete provider-neutral migration/bootstrap path and non-Supabase auth adapter
are still pending; Neon/RDS deployments are not yet turnkey. See
[DEPLOYMENT.md](DEPLOYMENT.md) for the rollout requirements.

For a fresh PostgreSQL database, customers can run
`python scripts/setup_database.py --provider postgres` once instead of manually
applying every SQL file. Use `--provider supabase` with `SUPABASE_DB_URL` for a
fresh Supabase database. The setup command keeps migrations versioned internally
and supports subsequent upgrades.

Generated API documentation (`/docs`, `/redoc`, and `/openapi.json`) is enabled
only for `DOCQA_DEPLOYMENT_MODEL=local-preview`. It is disabled for
`shared-hosted` and `dedicated-customer` deployments.

## Quick start

### Option A: Google Colab (free GPU, no credit card)

1. Push this repo to your GitHub account.
2. Open `notebooks/02_colab_pipeline_profiles.ipynb` in Colab (the current version; `01_colab_pipeline.ipynb` is the original) and set a **T4 GPU** runtime.
3. Edit `REPO_URL` in the first cell and run the cells in order.

### Option B: your own machine or a plain Linux VPS (no Docker)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[api,embeddings,dev]"

# 1. generate synthetic documents, access rules and test questions
docqa synth --out data

# 2. ingest, chunk, embed and index
docqa index --docs data/docs --acl data/acl.json --out data/index

# 3. ask (needs a model: see "Choosing a model" below). `ask` and `eval` use the same
#    embedder the index was built with (--embedder auto), so you do not repeat it.
docqa ask --index data/index --backend ollama --groups partners \
  "What is the annual audit fee for Karoo Fresh Produce (Pty) Ltd?"

# 4. evaluate
docqa eval --index data/index --backend ollama --questions data/questions.json
```

Run the tests with `pytest`. They need no model download.

The SQL integration test exercises the onboarding, provisioning, tenant/ACL search,
HNSW query plan, semantic-relevance cutoff, document-limit, platform-admin, and
offboarding functions against PostgreSQL rather than the API test store. It requires
a **fresh, disposable local Supabase-compatible
database** with `auth.users`, Supabase roles, and pgvector. The runner applies every
migration in `supabase/migrations/`. The platform-admin registry may contain zero or
one admin; with one, the test uses it without modifying its registry entry. More than
one admin requires a fresh disposable database. Fixture data is rolled back, but the
schema changes remain. Never point it at a shared or production database.

```powershell
$env:DOCQA_TEST_DATABASE_URL = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
.\tests\run_sql_integration.ps1
```

GitHub Actions runs the same integration SQL against an ephemeral PostgreSQL 16
container with pgvector and minimal `auth.users`/role stand-ins. This catches SQL
and migration regressions without credentials or a live Supabase project; it does
not replace an occasional test against a real Supabase instance.

Migration history is append-only for databases where migrations may already have
been applied. In particular, `202611200012_docqa_fix_platform_admin_email_return_type.sql`
is a historical byte-for-byte duplicate of `202611200011_docqa_fix_platform_admin_grant_ambiguity.sql`.
Both files remain in the chain to preserve migration versions; do not edit, remove,
or squash them for an existing database. Apply migrations in timestamp order.

### Choosing a model

| Where you run | Backend | Suggested model |
|---|---|---|
| Colab (T4 GPU) | `hf` | `Qwen/Qwen2.5-3B-Instruct`, or the 7B with `--four-bit` |
| CPU laptop or VPS (16 GB RAM) | `ollama` | a 3-4B model, e.g. `qwen2.5:3b` |
| Quick smoke test, no model | `extractive` | built-in baseline (not a real LLM) |

For the `hf` backend install the extras: `pip install -e ".[hf]"` (add `quant` for `--four-bit`). Install Ollama separately and run `ollama pull qwen2.5:3b` before using the `ollama` backend. Use `--embedder hash` to skip the embedding download in offline tests.

## Hosted language model, per-tenant profiles, limits

- `DOCQA_BACKEND=llm` uses an OpenAI-compatible chain of configured providers: Groq, OpenRouter, Cerebras and NVIDIA NIM. The starting provider is selected randomly for each request; if it fails, the remaining configured providers are tried. Each HTTP attempt uses a 30-second timeout and retries once on transient failures before fallback. If all fail, the API returns 503. OpenRouter requests set `provider.data_collection=deny`. Cerebras requires an explicitly configured `CEREBRAS_MODEL`; the application does not guess a model ID. NVIDIA NIM uses `https://integrate.api.nvidia.com/v1` and defaults to `meta/llama-3.3-70b-instruct`.
- **Privacy and cost:** with this backend, each question and the permitted retrieved passages may be sent to each configured provider reached during fallback. Random selection distributes starting requests but does not guarantee lower cost; check provider pricing, data terms and retention before enabling providers.
- Configure keys in the server's private `.env`: `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `CEREBRAS_API_KEY`, and/or `NVIDIA_NIM_API_KEY`. Optional model overrides are `GROQ_MODEL`, `OPENROUTER_MODEL`, `CEREBRAS_MODEL`, and `NVIDIA_NIM_MODEL`.
- Per-tenant profile: add `"profile": "acme"` to a branding entry and put `acme.json` in `DOCQA_PROFILES_DIR` (see `profiles/`). Bad or missing profiles fail at startup.
- Migrations `202611200001` (lexical search ORs terms, drops stopwords), `202611200002` (atomic document limit), and `202611200014` (HNSW-backed search and a 0.4 cosine-similarity cutoff) must be applied to Supabase.
- Also added: per-user rate limits, upload concurrency cap, DOCX zip-bomb check, HSTS, 60 s membership-registration cache, `DOCQA_AUDIT_QUESTIONS`.

## Profiles: one code base, many use cases

Organization-specific behavior lives in a small JSON **profile**, not in industry-specific code. Pass it with `--profile` (or `profile=` in Python):

```bash
docqa index --docs data/docs --acl data/acl.json --out data/index --profile profiles/example_accounting_firm.json
docqa ask   --index data/index --groups partners --profile profiles/example_accounting_firm.json "..."
```

`profiles/example_general_organization.json` is a neutral starting point. The accounting and HR profiles are examples, not required verticals.

| Setting | What it controls |
|---|---|
| `tenant_id` | written to audit records, so logs can be separated per tenant |
| `name`, `organisation`, `audience`, `domain` | who the assistant says it is and what the documents are |
| `language`, `tone`, `answer_style` | how answers are written (`concise`, `standard`, `detailed`) |
| `extra_instructions` | the customer's own rules, e.g. "state amounts in rand and say whether VAT is included" |
| `not_found_text` | what users see when nothing can be answered |
| `glossary` | the customer's vocabulary, e.g. `"notice period": ["terminate", "termination"]`, used to widen search only |
| `k`, `max_chars`, `overlap`, `min_dense` | retrieval and chunking, tuned per document type |
| `default_acl` | who can read files missing from `acl.json` (`["admin"]` gives default-deny) |
| `require_citation`, `check_grounding` | safety switches, on by default |

Example profiles cover a general organization, accountancy, and HR to show the same code serving different domains. Unknown settings are rejected, so a typo fails loudly. A profile can add rules but cannot override the core ones: use only the sources, cite, copy figures exactly, answer only about the entity asked, say `NOT_FOUND` when unsure, and never obey instructions found in documents.

Each customer should have its own evaluation questions, because "good" differs by use case: reuse `docqa eval` with a questions file built from that customer's (non-confidential) examples.

## How it works

```
documents --> parse + chunk --> index (BM25 + embeddings)
                 |                       |
        page/section + ACL        search(query, user_groups)
                                         |
                       only chunks the user may read, top-k
                                         |
                    LLM answers with [n] citations, or NOT_FOUND
                                         |
       no valid citation, or a figure not in the cited sources?
                                         -> "couldn't find this"
                                         |
                              answer + sources + audit log
```

### Design decisions

- **Filter before ranking.** Restricted chunks are removed before search, so they cannot reach the model or influence scores. The evaluation includes a leak check.
- **Hybrid search.** Keyword matching catches exact figures, section numbers and names; embeddings catch paraphrases across document domains.
- **Title-aware indexing.** The document title is part of the indexed text, which helps separate near-identical documents such as one engagement letter per client.
- **Sources are untrusted.** The system prompt tells the model to ignore instructions inside documents, and the evaluation set includes a poisoned document to test that.
- **Tenant-scoped hybrid search.** Supabase Postgres applies tenant and ACL predicates to candidate generation, uses the HNSW vector index for semantic retrieval, excludes semantic-only candidates below 0.4 cosine similarity, and fuses lexical and semantic ranks with reciprocal rank fusion.

## Evaluation

The synthetic corpus is generated from templates, so every question has a known answer. Question kinds:

| Kind | Passes when |
|---|---|
| `answerable` | the answer contains the expected fact |
| `injection` | same, despite a document telling the model to lie |
| `unanswerable` | the system says it can't find the answer |
| `forbidden` | a user without access gets a refusal **and** no restricted chunk is retrieved |

Matching is strict on numbers: `7 years` is not found in `17 years`, and `R5,000` is not found in `R15,000`. An `unanswerable` question passes only when the system gives a genuine refusal (the model said `NOT_FOUND`, or nothing relevant was retrieved). If a safety check rejected the answer instead (no citation, an ungrounded figure) it is counted separately as a "guard rejection" and does not pass.

Secondary metrics: retrieval hit rate (did the right document reach the model), citation correctness, latency.

### Results

Offline smoke-test baseline (no LLM, hash embeddings, seed 7), rerun on 2026-11-21 against 43 generated questions. It proves the pipeline and checks retrieval and access control; it says nothing about model quality.

| Setup | Answerable | Injection | Unanswerable | Forbidden | Retrieval hit | Access leaks |
|---|---|---|---|---|---|---|
| Extractive baseline | 25/34 | 1/1 | 5/5 | 2/3 | 100% | 0 |

Fill in real model results after your Colab run:

| Setup | Answerable | Injection | Unanswerable | Forbidden | Citation correct | Latency (mean) |
|---|---|---|---|---|---|---|
| Qwen2.5-3B, bge-small (original prompt) | 31/34 | 1/1 | 5/5 | 3/3 | 94% | 1.0s |
| Qwen2.5-3B, bge-small (profile prompt + grounding check) | 31/34 | 1/1 | 5/5 | 3/3 | 91% | 1.3s |
| Qwen2.5-7B (4-bit), bge-small | | | | | | |

The three original answerable failures were all "days notice" questions (q003, q015, q027). In the one failure that was inspected, the model answered `90 [1]` and dropped the unit. With the new prompt the model writes the unit and q003 and q015 now pass, but the total is unchanged at 31/34: q027 still fails, and two new questions (q023, another notice question, and q033, the retention question) fail because the model replies NOT_FOUND although retrieval found the right document (100% retrieval hit). Citation-correct dropped from 94% to 91% only because refused answers carry no citation. Evaluation rows now keep the model's raw reply and the documents it was shown, and the notebook has a section that prints them, to find out why it gives up.

Change one thing at a time (chunk size, `k`, model, prompt), re-run, and record what moved.

## Repository layout

```
src/docqa/
  synth.py     synthetic documents, access rules, evaluation questions
  ingest.py    parsing and chunking with page/section and ACL metadata
  index.py     hybrid index, embedders, permission filtering
  profile.py   per-tenant, cross-domain configuration
  prompts.py   system prompt and message construction
  grounding.py figure checks and sentence-level claim support checks
  answer.py    model backends, citation checks, audit log
  eval.py      evaluation harness and report
  cli.py       command line (synth, index, ask, eval)
  api.py       authenticated FastAPI endpoints
  supabase_store.py tenant-filtered Supabase/pgvector persistence
profiles/      neutral, accounting, and HR example profiles
supabase/      pgvector schema and restricted RPC functions
notebooks/     Colab notebooks (02 uses customer profiles)
tests/         pytest suite (no downloads needed)
```

## Supabase API

The HTTP API supports Supabase Auth and Amazon Cognito access tokens. It reads `tenant_id`, `groups`, and `roles` from the application-owned identity registry; request bodies and client-editable user metadata cannot set identity or permissions. Cognito requests provide both access and identity tokens, which are verified against the configured user pool.

The service-role key is server-only. Direct table access is revoked from `anon` and `authenticated`; API operations use constrained server-side RPCs. The API provides:

- `/` — responsive chat UI with Google and Microsoft OAuth sign-in, optional email/password sign-in, citations, and suggested questions.
- `/privacy`, `/terms`, and `/subprocessors` — draft customer-facing legal pages; all marked as requiring legal review and completion of retention/provider details.
- `GET /v1/auth/config` — exposes only public settings for the configured sign-in provider.
- `GET /v1/branding` — public branding selected by the request hostname; returns no tenant identifier.
- `GET /v1/onboarding/options` — inspects the verified identity to determine whether a preview workspace is needed.
- `GET /v1/organization/graph/catalog` and `POST /v1/organization/requests` — verify a delegated Microsoft Graph identity, list tenant security groups, and submit an organization request.
- `POST /v1/onboarding` — creates or joins a personal or approved Microsoft organization trial workspace.
- `POST /v1/organization/graph/sync` — refreshes the signed-in member's mapped Entra group memberships.
- `GET /v1/platform/organization-requests` and `POST /v1/platform/organization-requests/{request_id}/review` — platform-admin review of organization requests.
- `GET /v1/me` — current principal information resolved by the API.
- `GET /v1/usage` — workspace trial seat and document usage.
- `GET /v1/admin/dashboard` — administrator/auditor tenant-scoped audit and usage metadata only.
- `GET /metrics` — Prometheus text-format application, provider and database metrics. Restrict this endpoint to monitoring infrastructure; it is not tenant-scoped and is not part of the public API.
- `GET /v1/workspace/directory` — administrator-only verified member emails and available groups.
- `POST /v1/workspace/groups` — administrator-only group registration before ACL assignment.
- `GET /v1/workspace/export` — administrator-only JSON export of workspace metadata, member identities/emails, organization setup and request history, indexed document text, audit events, and offboarding-request history. Originals and embeddings are not included.
- `POST /v1/workspace/offboarding` — administrator-only request for platform-admin review before workspace deletion.
- `GET /v1/platform/offboarding-requests` and `POST /v1/platform/offboarding-requests/{request_id}/review` — platform-admin offboarding queue; approval deletes active workspace documents/chunks, memberships, groups, query audits, and associated organization setup data, while leaving Supabase Auth accounts intact.
- `POST /v1/query` — authenticated, tenant- and group-filtered retrieval and Q&A.
- `GET /v1/documents` — administrator-only tenant document library with ACL and chunk metadata.
- `POST /v1/documents` — administrator-only PDF/DOCX/TXT/MD ingestion with explicit ACL groups.
- `PATCH /v1/documents/{document_id}/acl` — administrator-only, tenant-scoped ACL update.
- `DELETE /v1/documents/{document_id}` — administrator-only, tenant-scoped deletion.
- `GET /health/live` — liveness probe.

### Local API setup

1. Create a Supabase project and run [`supabase/migrations/202611190001_docqa_vector_store.sql`](./supabase/migrations/202611190001_docqa_vector_store.sql) using the Supabase SQL editor or CLI.
2. Apply [`supabase/migrations/202611190002_docqa_document_admin.sql`](./supabase/migrations/202611190002_docqa_document_admin.sql) for the tenant-scoped document library and ACL editing.
3. Apply [`supabase/migrations/202611190003_docqa_trial_workspaces.sql`](./supabase/migrations/202611190003_docqa_trial_workspaces.sql) for preview onboarding and trial quota tracking.
4. Apply the remaining migrations `202611200001` through `202611200015` in timestamp order. These add search improvements, atomic document limits, workspace administration, organization onboarding, data lifecycle functions, SQL ambiguity fixes, HNSW-backed search, iterative filtered recall, and deterministic semantic tie-breaking.
5. Copy `.env.example` to `.env`; fill in the project URL, anon key, and service-role key. Keep `.env` private.
6. Install and start:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[api,embeddings,dev]"
docqa-api
```

### Production Docker ingress and observability

The `deploy/` directory contains a Caddy, Prometheus, Grafana and Alertmanager
stack. Caddy provides public HTTPS and enforces a 21 MiB request-body limit;
Grafana and Prometheus bind to loopback only. Copy
`deploy/.env.caddy.example`, `.env.app.example`, `.env.grafana.example`, and
`.env.alerting.example` to their non-example filenames, replace every
placeholder, set `DOCQA_DOMAIN` and `ALERTMANAGER_WEBHOOK_URL`, and run:

```powershell
cd deploy
docker compose up -d --build
.\validate.ps1
```

The validation script checks Compose, Prometheus, Grafana, application readiness,
and Alertmanager API health. It cannot prove that an external relay reached a
real person; verify firing and resolution notifications in Slack, Teams,
PagerDuty or the incident-management destination itself.

Open `http://127.0.0.1:8000/` to use the chat. For Supabase Auth, enable Google and Azure (Microsoft) providers. Register the Supabase project's Auth callback URL (`https://<project-ref>.supabase.co/auth/v1/callback`) with each provider's OAuth application, and allow the app URL (for local testing, `http://127.0.0.1:8000/`) in Supabase's redirect URL settings. Configure the Microsoft Entra app/provider to accept organizational accounts and personal Microsoft accounts. For Cognito Hosted UI setup and the separate Microsoft Graph app, see [DEPLOYMENT.md](./DEPLOYMENT.md#cognito-and-microsoft-graph-authentication).

For Supabase Graph onboarding, add the following **delegated Microsoft Graph permissions** to the Entra application used by Supabase Azure sign-in, and grant tenant-admin consent: `User.Read`, `Group.Read.All`, and `RoleManagement.Read.Directory`. For Cognito, configure these permissions on the separate public-client Entra application documented in [DEPLOYMENT.md](./DEPLOYMENT.md#cognito-and-microsoft-graph-authentication). The UI requests Graph scopes only when someone explicitly connects Graph; regular Microsoft sign-in keeps its existing OIDC scopes. Do not add a client secret or Graph token to the browser application. The Graph access token is held in browser memory and sent to the same-origin API only for the requested Graph operation; no Graph refresh token is stored.

Real-tenant verification is still required before relying on organization onboarding: confirm Supabase Azure `identity_data` contains the Microsoft `oid` used to match the Graph identity, and verify delegated permission consent in the customer tenant. Group synchronization uses `transitiveMemberOf`, including nested-group membership, and recognizes active directory roles while excluding PIM-eligible but inactive roles. Since Graph tokens are not stored, membership changes are refreshed when a user reconnects/syncs Graph, and organization access is fail-closed when the sync is more than 12 hours old. These behaviors have not been verified against a live Entra tenant.

Microsoft sign-in requires the provider to return a valid email address. This app requests the `email` OAuth scope. If Supabase reports “Error getting user email from external provider,” check that the Microsoft account has an email address and add the optional `email` claim to the Entra app's ID token (App registrations → your app → Token configuration → Add optional claim → ID → `email`). Keep email verification and account linking under Supabase Auth; the app does not substitute an unverified username for a missing email.

OAuth uses the authorization-code flow with PKCE. The short-lived PKCE verifier is kept in tab-scoped session storage for the redirect; access and refresh tokens remain in memory and are cleared on sign-out or refresh failure. Email/password sign-in remains available as a secondary option. The UI does not persist tokens or conversations in browser storage, is served from the API origin, and loads no third-party scripts or fonts.

### Tenant branding and document upload

Set `DOCQA_TENANT_BRANDING_JSON` to a JSON object keyed by exact hostnames. Each entry must include the same `tenant_id` stored in the application's identity registry; after authentication, the API rejects a session whose tenant does not match that hostname. Unknown hostnames receive the generic Doc Reader theme. Do not select tenants from query parameters or client-editable user metadata.

Brand entries can set `name`, `tagline`, `login_heading`, `login_subtitle`, `footer_text`, `support_url` (`https://` or `mailto:`), `primary_color`, `accent_color`, `hide_attribution`, and `logo_url`. Colors must be six-digit hex values. Put tenant logo files in the local `pics` directory (or configure `DOCQA_BRANDING_DIR`) and use a same-origin path such as `/branding/acme-logo.svg`; remote logos are intentionally not allowed. The example configuration is in `.env.example`.

After sign-in, tenant admins see **Upload document** and **Manage documents**. The library lists only the authenticated tenant's documents, ACL groups, upload date, and indexed chunk count. Admins can update a document's ACL or delete its indexed content. ACL changes take effect immediately in retrieval; a user who loses group access will no longer retrieve that document. The server independently enforces the admin role, tenant scope, ACL validation, and upload limits. Non-admin users cannot call the management endpoints even if they manually construct a request. Apply the document-admin migration above before using the library. Uploaded source files are parsed and indexed but not retained.

Admins can register ACL groups before any member belongs to them; the group then appears in upload and library suggestions. The admin-only directory shows verified member email addresses and current groups, not raw user IDs. Dashboard query errors count provider or document-service failures only; normal refusals and grounding rejections are not operational errors. Audit persistence is best-effort and failures are logged without failing the user request. The chat page discloses that hosted-model requests send the question and authorized retrieved passages to whichever configured model providers are tried.

Uploads are limited to 20 MiB per file and 21 MiB for the complete multipart request. PDFs are capped at 500 pages and parsed in an isolated worker with a 30-second timeout. Set a matching 21 MiB request-body limit at the reverse proxy or hosting ingress as well, so oversized bodies are rejected before reaching the application.

### Personal and organization previews

Google and Microsoft personal accounts can create private personal-plan workspaces. An unregistered Microsoft work/school account can also use its personal preview, or connect Microsoft Graph to submit an organization request. The API checks that Graph's signed-in user and tenant match the verified Microsoft identity; it builds organization and security-group details from Graph rather than trusting browser-supplied tenant data. The user selecting/submitting the request must have the needed Graph access, while a tenant administrator must grant the delegated Graph permissions to the Entra application.

A platform administrator reviews each request in **Platform administration**. Submission is restricted to users with a Global Administrator, Privileged Role Administrator, Cloud Application Administrator, or Application Administrator role. Approval registers the tenant and selected security-group mappings; the verified requester becomes the first workspace admin. Other users must connect Graph and be members of at least one approved security group to join. Those memberships map to workspace ACL groups; Graph membership never grants workspace-admin or platform-admin roles. The initial workspace admin remains an explicit app-admin assignment, independent of Entra groups.

After migration `202611200007`, bootstrap the first platform administrator once in the Supabase SQL editor. Replace the UUID below with the trusted operator's user ID from **Authentication → Users**:

```sql
do $$
declare
    v_user_id uuid := 'REPLACE-WITH-SUPABASE-USER-UUID'::uuid;
begin
    if not exists (
        select 1 from auth.users
        where id = v_user_id and email_confirmed_at is not null
    ) then
        raise exception 'Choose an existing user with a confirmed email';
    end if;

    if not exists (
        select 1 from public.docqa_platform_admins where user_id = v_user_id
    ) then
        insert into public.docqa_platform_admins (user_id, granted_by)
        values (v_user_id, v_user_id);
        insert into public.docqa_platform_admin_audit (
            actor_user_id, target_user_id, action
        ) values (v_user_id, v_user_id, 'grant');
    end if;
end;
$$;
```

Then platform admins can grant or revoke platform access in **Platform administration**. Grants are resolved to confirmed Supabase Auth users, and every actual grant/revocation is audited. The last active platform administrator cannot be revoked. Platform access is registry-backed; setting `platform_admin` in `app_metadata.roles` alone does not grant access. Organization registrations are created through Graph request approval rather than manual tenant-ID entry. After an organization is approved, members can use **Sync Microsoft groups** after sign-in to refresh their own memberships. Graph access tokens are not retained; organization access is blocked when the last group sync is more than 12 hours old, so users must reconnect and sync at least every 12 hours. Sync uses transitive group membership, including nested groups. Only active directory roles are recognized for organization-request submission; PIM-eligible roles must be activated before submitting. Unregistering a tenant affects future onboarding only; existing members keep their workspace and access.

The preview limits are **5 documents per workspace**, **15 members per organization**, and **10 members per personal workspace**. The document limit is enforced by the API on upload; replacing a same-name document does not use another slot. Authenticated users with trusted tenant metadata are registered in the membership registry and checked against the seat cap on each API request; legacy email/password identities are also counted under their existing trusted tenant. Registration is serialized by a workspace-row lock, so simultaneous joins cannot exceed the cap. Existing users are registered as they next make an authenticated API request; backfill current memberships before rollout if the seat cap must account for inactive users immediately. When a quota is reached, the API responds with HTTP 402 and an administrator-contact message. Billing and paid-plan upgrades are not integrated yet; set up a billing/contact destination before offering paid upgrades.

Configure a Supabase user with trusted `app_metadata` similar to:

```json
{"tenant_id": "your-tenant", "groups": ["staff"], "roles": ["admin"]}
```

Use the user's Supabase access token as `Authorization: Bearer <token>`. Uploads require the `admin` role and an explicit ACL, for example `staff,finance`; `all` is an explicit public ACL and should be assigned sparingly. The default ACL for documents outside the API remains `admin`.

The API currently uses 384-dimensional `BAAI/bge-small-en-v1.5` embeddings by default. Keep the model consistent between ingestion and query, and migrate the vector dimension before using a different model. The default response backend is the extractive smoke-test baseline; configure `DOCQA_BACKEND=ollama` and `DOCQA_MODEL` to use a local Ollama model.

Uploads are parsed into text chunks and embeddings in Supabase; the original uploaded file is not retained. Query audit records store a SHA-256 hash of the question by default (`DOCQA_AUDIT_QUESTIONS=hash`); `full` and `none` are explicit alternatives. Hashing is pseudonymization, not anonymization: predictable questions may be guessed. Audit records can still include retrieved/cited document names, user IDs, actions, outcomes, and timestamps, so configure access and retention in line with your data policy.

The local `docqa index`/`docqa ask` CLI and evaluation suite still use the in-memory index for offline development; the authenticated HTTP API uses Supabase as its persistent store.

Do not expose the service-role key, accept identity claims from clients, or place the API directly on the public internet without TLS, rate limiting, operational monitoring, backup/retention controls, and security review. Supabase service-role access bypasses database RLS; the API's verified identity and tenant/ACL filters are therefore security-critical.

## Roadmap

1. Measure and improve quality with real models (chunking, reranking, prompt).
2. Validate tenancy, authorization, upload, deletion, and retrieval behavior against a real Supabase project.
3. Add semantic claim verification and adversarial prompt-injection evaluation.
4. Add SharePoint/other source connectors with source-native permission synchronization.
5. Add rate limiting, observability, retention policies, and production deployment automation.

## Security and privacy notes

- All data in this repo is **synthetic and fictional**. Do not commit real client or employer documents, credentials or API keys. `data/` and `.env` are git-ignored.
- Folders are ingested recursively. Key each entry in `acl.json` by the file's path relative to the documents folder; a bare file name only works when no other folder has a file with that name. Files with no entry default to `"admin"` and are denied unless the caller has that group. Use `"all"` only for documents intended to be public. `docqa index` reports ACL entries that match no file, ambiguous entries, and PDFs with no extractable text (scanned pages need OCR first).
- If you run this on a VPS, use SSH keys, a firewall, and keep Postgres and Ollama bound to localhost.
- Workspace administrators can export indexed text and request offboarding; deletion is performed only after platform-admin approval. Approval removes active workspace content, membership and query audit events, clears matching workspace claims from Supabase Auth metadata, and retains Auth accounts plus a minimal offboarding decision record. Supabase backups and provider-side retention are not purged by this API.
- Audit persistence is best-effort: an audit-table write failure is logged and does not fail the user request. Dashboard query errors count only `query_error` events whose outcome is `error`; sign-in events are not written by `_principal` and are excluded from dashboard activity.
- The in-app Terms, Privacy Notice and Subprocessor List are drafts. Retention for query audits, offboarding records, backups and hosted providers, plus the deployed Supabase region and provider agreements, must be set and verified before production use.
- Keeping data inside your own environment is an architectural property of this design. It is **not** a legal compliance claim. Anything about POPIA, GDPR or professional-confidentiality obligations needs review by a qualified professional.
- This is a personal project. If you work for an employer in this industry, check your contract's IP and outside-work clauses before building or publishing.

## License

Apache-2.0. See the `LICENSE` file.

