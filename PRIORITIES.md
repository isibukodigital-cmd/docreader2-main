# Product Priorities and Deferred Work

This backlog captures the larger organization-readiness work that was discussed
but not implemented in the current preview. Items are ordered by dependency and
product risk, not by estimated effort.

## Priority 1 — Establish the enterprise foundation

**Goal:** Avoid locking every customer into the current hosting setup.

- [x] Define supported deployment models: shared hosted service, dedicated
  customer environment, or both.
- [x] Separate authentication and document storage behind replaceable
  interfaces; retain the current Supabase implementation as the preview/default.
- [ ] Design and validate an Azure hosting option using Microsoft Entra ID and
  managed PostgreSQL with `pgvector`.
- [ ] Define tenant identity, authorization, configuration, and secrets
  boundaries consistently across deployment options.
- [ ] Specify customer data export, migration, account linking, and rollback
  behavior before migrating a customer.
- [ ] Document migration constraints, including that identity-provider changes
  may require users to sign in again or link accounts; password credentials
  should not be assumed transferable.
- [ ] Prefer configurable deployment options over customer-specific forks.

**Exit criteria:** The supported hosting/authentication combinations are
documented, and a validated deployment can be operated without changing
tenant-isolation guarantees.

## Priority 2 — Make document access practical for company admins

**Goal:** Let administrators assign access confidently during upload and
management.

- [ ] Decide whether access supports groups, individual users, or both. Proposed
  default: groups remain the primary access control, with individual grants
  added only if customers require them.
- [x] Build a people/group picker using verified members and groups already
  known to the workspace as the initial, least-permission option.
- [ ] If full Entra directory search is required, integrate Microsoft Graph with
  narrowly scoped permissions and explicit customer tenant-admin consent.
- [x] Show the resolved group or user identity in the picker; do not rely on
  administrators remembering exact text identifiers.
- [x] Let administrators register an ACL group before assigning it to documents.
- [ ] Keep authorization enforced by the API and retrieval layer. The UI picker
  must not be treated as an access-control boundary.
- [ ] Add tests for grants, revocations, tenant isolation, unknown identities,
  and documents that become inaccessible after membership changes.

**Decision needed before implementation:** Workspace-only directory versus
full Entra directory search, and whether individual-user grants are in scope.

**Exit criteria:** An administrator can select valid principals, and access
changes are reflected immediately and correctly in retrieval.

## Priority 3 — Organization administration, audit, and monitoring

**Goal:** Give company IT useful visibility while making access to conversation
content explicit and controlled.

- [x] Add an organization admin dashboard for membership, document usage,
  sign-ins, query activity, errors, and document citations.
- [x] Clearly separate usage/audit metadata from full conversation contents.
- [x] Define whether conversation transcripts are retained. The current chat
  does not provide persisted history, but query audit events currently record
  question text, retrieved/cited document names, and outcome.
- [x] Update user-facing privacy notices to disclose current question logging
  and any future transcript retention.
- [ ] If transcript retention is enabled, make it an explicit organization
  policy with a retention period, export, and deletion process.
- [x] Define separate workspace administrator and auditor/compliance roles;
  do not assume every workspace admin should be able to read all conversation
  contents.
- [ ] Audit access to audit records and transcripts, including who viewed them
  and when.
- [x] Keep tenant admin/auditor dashboards tenant-scoped; process-wide
  provider/database/application metrics are exposed only through the private
  infrastructure `/metrics` endpoint.
- [ ] Add tests for role boundaries, audit access logging, retention, export,
  and tenant isolation.

**Exit criteria:** IT can answer operational questions from scoped activity
data, and any access to conversation content is disclosed, authorized, and
audited.

## Priority 4 — Complete the trial and commercial model

**Goal:** Make trial limits, organization onboarding, and upgrade paths
operationally complete.

- [ ] Add organization member invitation, removal, and role-management flows.
- [ ] Backfill manually provisioned and inactive users into the membership
  registry before relying on seat enforcement for existing organizations.
- [ ] Reconcile membership counts and define how duplicate or linked identities
  are handled.
- [ ] Decide paid-plan limits, billing ownership, and upgrade/contact workflow.
- [ ] Implement billing only after pricing, tax, payment, and support ownership
  are decided.
- [ ] Add tenant-level settings for workspace policies and quotas.
- [ ] Clearly communicate current preview limits and what happens when each
  limit is reached.

**Exit criteria:** An organization can administer its members and understand
usage and upgrade options without manual database changes.

## Priority 5 — Production readiness and customer operations

**Goal:** Operate reliably with real customer data and support requirements.

- [x] Establish application and dependency monitoring, alerting, and actionable
  operational dashboards.
- [x] Document database backup, restore, disaster-recovery, and recovery-time
  procedures.
- [ ] Exercise and record a full PITR restore test before customer use.
- [x] Document safe migration and rollback procedures for schema and application
  releases.
- [ ] Test tenant isolation and access-control behavior across multiple
  organizations, including negative and concurrency cases.
- [ ] Define incident response, customer support ownership, and escalation
  procedures.
- [ ] Define customer data deletion, export, and offboarding procedures.
- [ ] Review privacy, identity, security, and retention requirements for each
  target customer and deployment model.
- [x] Add a release checklist covering configuration, migration status,
  monitoring, backup readiness, and rollback.

**Exit criteria:** Releases and customer operations have repeatable procedures,
and data recovery and tenant-isolation controls have been exercised.

## Suggested sequencing

1. Begin Priority 1 with architecture and deployment-model decisions; avoid
   starting a broad provider migration until the target deployment is agreed.
2. Resolve the directory and access-model decisions in Priority 2 before
   implementing a company user picker.
3. Decide audit-versus-transcript policy in Priority 3 before exposing
   organization monitoring.
4. Complete membership backfill and administration before treating trial seat
   counts as authoritative.
5. Advance production-readiness work alongside the first customer deployment,
   not after it.

## Current scope note

The items above are deferred roadmap work, not features already delivered.
Current previews use Supabase Auth and Supabase-backed Postgres RPCs; the current
document access interface accepts group names rather than searching a company
directory; and the UI does not provide an organization monitoring dashboard.
The existing query audit events are distinct from a persisted, user-visible
conversation history.
