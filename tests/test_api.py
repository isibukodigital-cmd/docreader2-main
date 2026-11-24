from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import numpy as np
import pytest
import requests
from fastapi.testclient import TestClient

from docqa.api import (
    AuthenticatedUser,
    AuthenticationError,
    Principal,
    Settings,
    SupabaseAuthVerifier,
    TenantBranding,
    _graph_group_sync_is_fresh,
    _audit_question,
    create_app,
)
from docqa.index import Hit
from docqa.ingest import Chunk
from docqa.supabase_store import (
    DocumentLimitError,
    LastPlatformAdminError,
    OffboardingRequestConflictError,
    OrganizationRequestConflictError,
    PlatformAdminTargetNotFoundError,
    SupabaseError,
    SupabaseVectorStore,
    TrialMemberLimitError,
)
from docqa.deployment import DeploymentConfig


class FakeAuth:
    def __init__(self):
        self.identity_metadata = {}

    def _remember_identity(self, user):
        self.identity_metadata.setdefault(user.user_id, dict(user.app_metadata))
        return user

    def authenticate(self, token: str) -> Principal:
        principals = {
            "staff": Principal("user-staff", "tenant-a", ("staff",), False),
            "admin": Principal("user-admin", "tenant-a", ("staff",), True),
            "auditor": Principal("user-auditor", "tenant-a", ("staff",), False, is_auditor=True),
            "other-tenant": Principal("user-other", "tenant-b", ("staff",), False),
            "other-tenant-admin": Principal("user-other-admin", "tenant-b", ("staff",), True),
        }
        try:
            return principals[token]
        except KeyError as exc:
            raise AuthenticationError("invalid token") from exc

    def authenticate_user(self, token: str) -> AuthenticatedUser:
        users = {
            "new-google": AuthenticatedUser(
                "user-google", "user@example.com", "google", None, {"provider": "google"}
            ),
            "new-org-owner": AuthenticatedUser(
                "org-owner", "owner@acme.example", "azure",
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", {"provider": "azure"}, "org-owner"
            ),
            "new-org-member": AuthenticatedUser(
                "org-member", "member@acme.example", "azure",
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", {"provider": "azure"}, "org-member"
            ),
            "graph-member": AuthenticatedUser(
                "org-member", "member@acme.example", "azure",
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", {"provider": "azure"}, "org-member"
            ),
            "graph-owner": AuthenticatedUser(
                "org-owner",
                "owner@acme.example",
                "azure",
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                {
                    "provider": "azure",
                    "tenant_id": "workspace-id",
                    "workspace_type": "organization",
                    "workspace_name": "Acme Ltd",
                    "groups": ["owner", "Finance"],
                    "graph_groups": ["Finance"],
                    "roles": ["admin"],
                },
                "org-owner",
            ),
            "stale-graph-owner": AuthenticatedUser(
                "org-owner",
                "owner@acme.example",
                "azure",
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                {
                    "provider": "azure",
                    "tenant_id": "workspace-id",
                    "workspace_type": "organization",
                    "workspace_name": "Acme Ltd",
                    "groups": ["owner", "Finance"],
                    "graph_groups": ["Finance"],
                    "graph_groups_synced_at": "2000-01-01T00:00:00+00:00",
                    "roles": ["admin"],
                },
                "org-owner",
            ),
            "new-ms-personal": AuthenticatedUser(
                "ms-personal", "person@outlook.com", "azure",
                "9188040d-6c67-4c5b-b112-36a304b66dad", {"provider": "azure"}
            ),
            "new-unverified-azure": AuthenticatedUser(
                "unknown", "unknown@example.com", "azure", None, {"provider": "azure"}
            ),
            "platform-admin": AuthenticatedUser(
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "platform-admin@example.com",
                "google",
                None,
                {"provider": "google"},
            ),
            "platform-admin-target": AuthenticatedUser(
                "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                "ops@example.com",
                "google",
                None,
                {"provider": "google"},
            ),
            "spoofed-platform-admin": AuthenticatedUser(
                "spoofed-admin-user",
                "spoofed@example.com",
                "google",
                None,
                {"provider": "google", "roles": ["platform_admin"]},
            ),
            "platform-tenant-admin": AuthenticatedUser(
                "platform-tenant-admin",
                "tenant-admin@example.com",
                "azure",
                "tenant-a",
                {
                    "provider": "azure",
                    "tenant_id": "tenant-a",
                    "groups": ["staff"],
                    "roles": ["admin", "platform_admin"],
                    "workspace_type": "organization",
                    "workspace_name": "Acme",
                },
            ),
            "legacy-email": AuthenticatedUser(
                "legacy-user",
                "legacy@example.com",
                "email",
                None,
                {
                    "tenant_id": "tenant-legacy",
                    "groups": ["staff"],
                    "roles": [],
                    "workspace_type": "organization",
                    "workspace_name": "Legacy Workspace",
                },
            ),
            "organization-sixteenth": AuthenticatedUser(
                "organization-sixteenth",
                "sixteenth@acme.example",
                "azure",
                "tenant-a",
                {
                    "tenant_id": "tenant-a",
                    "groups": ["staff"],
                    "roles": [],
                    "workspace_type": "organization",
                    "workspace_name": "Acme",
                },
            ),
            "personal-eleventh": AuthenticatedUser(
                "personal-eleventh",
                "eleventh@example.com",
                "google",
                None,
                {
                    "tenant_id": "tenant-personal",
                    "groups": ["staff"],
                    "roles": [],
                    "workspace_type": "personal",
                    "workspace_name": "Personal",
                },
            ),
        }
        if token in users:
            user = users[token]
            if (
                user.provider == "azure"
                and user.app_metadata.get("workspace_type") == "organization"
                and user.app_metadata.get("tenant_id")
            ):
                metadata = dict(user.app_metadata)
                metadata.setdefault(
                    "graph_groups_synced_at",
                    datetime.now(timezone.utc).isoformat(),
                )
                user = replace(user, app_metadata=metadata)
            return self._remember_identity(user)
        try:
            principal = self.authenticate(token)
        except AuthenticationError:
            raise
        provider = "azure"
        metadata = {
            "provider": provider,
            "tenant_id": principal.tenant_id,
            "groups": list(principal.groups),
            "roles": (
                ["admin"] if principal.is_admin
                else ["auditor"] if principal.is_auditor
                else []
            ),
            "workspace_type": principal.workspace_type,
            "workspace_name": principal.workspace_name,
        }
        if provider == "azure" and principal.workspace_type == "organization":
            metadata["graph_groups_synced_at"] = datetime.now(timezone.utc).isoformat()
        return self._remember_identity(AuthenticatedUser(
            principal.user_id,
            "",
            provider,
            principal.tenant_id,
            metadata,
        ))

    @staticmethod
    def principal_from_user(user: AuthenticatedUser) -> Principal:
        return SupabaseAuthVerifier.principal_from_user(user)


class FakeEmbedder:
    def encode_docs(self, texts: list[str]) -> np.ndarray:
        return np.ones((len(texts), 384), dtype=np.float32)

    def encode_query(self, query: str) -> np.ndarray:
        return np.ones(384, dtype=np.float32)


class FakeStore:
    def __init__(self):
        self.documents: dict[tuple[str, str], dict] = {}
        self.audit: list[dict] = []
        self.offboarding_requests: list[dict] = []
        self.closed_workspaces: set[str] = set()
        self.last_search: tuple[str, tuple[str, ...], str] | None = None
        self.workspaces: dict[tuple[str, str], dict] = {}
        self.members: dict[tuple[str, str], dict] = {}
        self.updated_metadata: dict[str, dict] = {}
        self.identity_metadata: dict[str, dict] = {}
        self.identity_ensures: list[tuple] = []
        self.registration_error = None
        self.audit_error = None
        self.workspace_groups: dict[str, set[str]] = {}
        self.organizations: dict[str, dict] = {}
        self.organization_requests: dict[str, dict] = {}
        self.organization_group_mappings: dict[str, list[dict]] = {}
        self.platform_admins = {
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa": {
                "user_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "email": "platform-admin@example.com",
                "granted_by": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "created_at": "2026-11-20T00:00:00Z",
            }
        }
        self.platform_admin_audit = []
        self.user_emails = {
            "org-owner": "owner@acme.example",
            "org-member": "member@acme.example",
            "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb": "ops@example.com",
        }

    def search(self, query, k=4, user_groups=None, tenant_id=None, pool=30):
        groups = tuple(user_groups or [])
        self.last_search = (tenant_id, groups, query)
        hits = []
        for (tenant, _name), document in self.documents.items():
            if tenant != tenant_id or not (
                "all" in document["groups"] or set(groups) & set(document["groups"])
            ):
                continue
            for i, row in enumerate(document["chunks"][:k]):
                chunk = Chunk(str(i), document["name"], row["location"], row["content"],
                              document["groups"])
                hits.append(Hit(chunk, 1.0, 1.0, 1.0))
                if len(hits) >= k:
                    return hits
        return hits[:k]

    def upsert_document(self, tenant_id, name, acl_groups, uploaded_by, chunks,
                        document_limit=None):
        if (document_limit is not None and (tenant_id, name) not in self.documents
                and sum(1 for t, _ in self.documents if t == tenant_id) >= document_limit):
            raise DocumentLimitError("Trial document limit reached")
        document_id = str(uuid4())
        self.documents[(tenant_id, name)] = {
            "id": document_id,
            "name": name,
            "groups": acl_groups,
            "chunks": chunks,
            "uploaded_by": uploaded_by,
            "created_at": "2026-11-19T00:00:00+00:00",
        }
        return document_id

    def delete_document(self, tenant_id, document_id):
        for key, document in list(self.documents.items()):
            if key[0] == tenant_id and document["id"] == document_id:
                del self.documents[key]
                return True
        return False

    def list_documents(self, tenant_id):
        return [
            {
                "document_id": document["id"],
                "name": document["name"],
                "acl_groups": document["groups"],
                "uploaded_by": document["uploaded_by"],
                "created_at": document["created_at"],
                "chunk_count": len(document["chunks"]),
            }
            for (document_tenant, _name), document in self.documents.items()
            if document_tenant == tenant_id
        ]

    def update_document_acl(self, tenant_id, document_id, acl_groups):
        for (document_tenant, _name), document in self.documents.items():
            if document_tenant == tenant_id and document["id"] == document_id:
                document["groups"] = acl_groups
                return True
        return False

    def get_registered_organization(self, microsoft_tenant_id):
        return self.organizations.get(microsoft_tenant_id)

    def list_registered_organizations(self):
        return sorted(self.organizations.values(), key=lambda row: row["name"].lower())

    def set_registered_organization(self, microsoft_tenant_id, name, initial_admin_emails):
        organization = {
            "microsoft_tenant_id": microsoft_tenant_id,
            "name": name,
            "initial_admin_emails": initial_admin_emails,
            "created_at": "2026-11-20T00:00:00Z",
        }
        self.organizations[microsoft_tenant_id] = organization
        return organization

    def get_organization_request_status(self, microsoft_tenant_id):
        return self.organization_requests.get(microsoft_tenant_id)

    def create_organization_request(
        self, requested_by, microsoft_tenant_id, name, verified_domains, selected_groups
    ):
        existing = self.organization_requests.get(microsoft_tenant_id)
        row = {
            "request_id": existing["request_id"] if existing else str(uuid4()),
            "microsoft_tenant_id": microsoft_tenant_id,
            "name": name,
            "requester_email": self.user_emails[requested_by],
            "status": "pending",
            "requested_at": "2026-11-20T00:00:00Z",
            "verified_domains": verified_domains,
            "selected_groups": selected_groups,
        }
        self.organization_requests[microsoft_tenant_id] = row
        self.organization_group_mappings[microsoft_tenant_id] = selected_groups
        return row

    def list_organization_requests(self, actor_user_id):
        assert self.is_platform_admin(actor_user_id)
        return [
            row for row in self.organization_requests.values()
            if row["status"] == "pending"
        ]

    def review_organization_request(self, request_id, actor_user_id, approve, review_note=""):
        assert self.is_platform_admin(actor_user_id)
        row = next(
            (request for request in self.organization_requests.values()
             if request["request_id"] == request_id),
            None,
        )
        if row is None or row["status"] != "pending":
            raise OrganizationRequestConflictError("The organization request has changed.")
        row["status"] = "approved" if approve else "rejected"
        if approve:
            self.set_registered_organization(
                row["microsoft_tenant_id"], row["name"], [row["requester_email"]]
            )
        return row

    def get_registered_organization_groups(self, microsoft_tenant_id):
        return [
            {
                "graph_group_id": group["group_id"],
                "graph_group_name": group["display_name"],
                "workspace_group_name": group["workspace_group_name"],
            }
            for group in self.organization_group_mappings.get(microsoft_tenant_id, [])
        ]

    def remove_registered_organization(self, microsoft_tenant_id):
        return self.organizations.pop(microsoft_tenant_id, None) is not None

    def is_platform_admin(self, user_id):
        return user_id in self.platform_admins

    def list_platform_admins(self):
        return sorted(self.platform_admins.values(), key=lambda row: row["created_at"])

    def list_platform_admin_audit(self, limit=100):
        return list(reversed(self.platform_admin_audit[-limit:]))

    def grant_platform_admin(self, actor_user_id, target_email):
        target_user_id = next(
            (user_id for user_id, email in self.user_emails.items()
             if email.casefold() == target_email.casefold()),
            None,
        )
        if target_user_id is None:
            raise PlatformAdminTargetNotFoundError("No confirmed Supabase user was found for that email")
        if target_user_id not in self.platform_admins:
            self.platform_admins[target_user_id] = {
                "user_id": target_user_id,
                "email": self.user_emails[target_user_id],
                "granted_by": actor_user_id,
                "created_at": "2026-11-20T00:00:01Z",
            }
            self.platform_admin_audit.append({
                "id": len(self.platform_admin_audit) + 1,
                "actor_user_id": actor_user_id,
                "actor_email": "platform-admin@example.com",
                "target_user_id": target_user_id,
                "target_email": self.user_emails[target_user_id],
                "action": "grant",
                "created_at": "2026-11-20T00:00:01Z",
            })
        return self.platform_admins[target_user_id]

    def revoke_platform_admin(self, actor_user_id, target_user_id):
        if target_user_id not in self.platform_admins:
            return False
        if len(self.platform_admins) <= 1:
            raise LastPlatformAdminError("At least one platform administrator must remain")
        target = self.platform_admins.pop(target_user_id)
        self.platform_admin_audit.append({
            "id": len(self.platform_admin_audit) + 1,
            "actor_user_id": actor_user_id,
            "actor_email": "platform-admin@example.com",
            "target_user_id": target_user_id,
            "target_email": target["email"],
            "action": "revoke",
            "created_at": "2026-11-20T00:00:02Z",
        })
        return True

    def provision_trial_workspace(
        self, user_id, provider, external_account_id, workspace_type, name, member_limit
    ):
        organization = self.get_registered_organization(external_account_id)
        if workspace_type == "organization" and organization is None:
            raise ValueError("organization is not registered")
        key = (provider, external_account_id)
        if key not in self.workspaces:
            self.workspaces[key] = {
                "tenant_id": str(uuid4()),
                "workspace_type": workspace_type,
                "workspace_name": organization["name"] if organization else name,
                "owner_user_id": user_id,
                "member_limit": member_limit,
            }
        workspace = self.workspaces[key]
        if workspace["workspace_type"] != workspace_type:
            raise ValueError("workspace type mismatch")
        member_key = (workspace["tenant_id"], user_id)
        if member_key not in self.members:
            current_count = sum(1 for tenant, _user in self.members if tenant == workspace["tenant_id"])
            if current_count >= member_limit:
                raise ValueError("trial member limit reached")
            is_owner = (
                workspace_type == "personal" and workspace["owner_user_id"] == user_id
            ) or (
                organization is not None
                and self.user_emails.get(user_id, "").casefold()
                in organization["initial_admin_emails"]
            )
            self.members[member_key] = {
                "member_groups": ["owner"] if is_owner else ["staff"],
                "member_roles": ["admin"] if is_owner else [],
            }
        member = self.members[member_key]
        if (
            organization is not None
            and self.user_emails.get(user_id, "").casefold()
            in organization["initial_admin_emails"]
        ):
            member["member_groups"] = sorted(set(member["member_groups"]) | {"owner"})
            member["member_roles"] = sorted(set(member["member_roles"]) | {"admin"})
        count = sum(1 for tenant, _user in self.members if tenant == workspace["tenant_id"])
        return {
            "tenant_id": workspace["tenant_id"],
            "workspace_type": workspace_type,
            "workspace_name": workspace["workspace_name"],
            **member,
            "member_count": count,
            "member_limit": member_limit,
        }

    def trial_usage(self, tenant_id):
        workspace = next(
            (row for row in self.workspaces.values() if row["tenant_id"] == tenant_id), None
        )
        if not workspace:
            return None
        return {
            "workspace_type": workspace["workspace_type"],
            "workspace_name": workspace["workspace_name"],
            "member_count": sum(1 for tenant, _user in self.members if tenant == tenant_id),
            "member_limit": workspace["member_limit"],
        }

    def workspace_directory(self, tenant_id):
        members = [
            {
                "user_id": user_id,
                "email": member.get("email"),
                "groups": member["member_groups"],
                "roles": member["member_roles"],
            }
            for (member_tenant, user_id), member in self.members.items()
            if member_tenant == tenant_id
        ]
        groups = sorted({
            group for group in (
                [*self.workspace_groups.get(tenant_id, set())]
                + [group for member in members for group in member["groups"]]
            )
            if group.strip()
        })
        return {"groups": groups, "members": members}

    def create_workspace_group(self, tenant_id, name, created_by):
        self.workspace_groups.setdefault(tenant_id, set()).add(name)
        return name

    def admin_dashboard(self, tenant_id, limit=50):
        member_count = sum(1 for member_tenant, _ in self.members if member_tenant == tenant_id)
        document_count = sum(1 for document_tenant, _ in self.documents if document_tenant == tenant_id)
        events = [
            event for event in self.audit
            if event.get("tenant_id") == tenant_id
        ]
        query_count = sum(
            1 for event in events
            if event.get("action") in {"query", "query_error"}
        )
        error_count = sum(
            1 for event in events
            if event.get("action") == "query_error"
            and event.get("outcome") == "error"
        )
        document_activity_count = sum(
            1 for event in events
            if event.get("action") in {"ingest", "delete", "acl_update"}
        )
        visible_events = [event for event in events if event.get("action") != "sign_in"]
        recent_events = [
            {
                "id": index + 1,
                "user_id": str(event.get("user_id", "")),
                "action": event.get("action", ""),
                "document_name": event.get("document_name"),
                "documents_retrieved": event.get("documents_retrieved", []),
                "documents_cited": event.get("documents_cited", []),
                "outcome": event.get("outcome"),
                "created_at": "2026-11-20T00:00:00Z",
            }
            for index, event in enumerate(reversed(visible_events[-limit:]))
        ]
        return {
            "member_count": member_count,
            "document_count": document_count,
            "query_count": query_count,
            "error_count": error_count,
            "document_activity_count": document_activity_count,
            "recent_events": recent_events,
        }

    def update_identity_app_metadata(self, user_id, app_metadata):
        self.updated_metadata[user_id] = app_metadata
        self.identity_metadata[user_id] = dict(app_metadata)

    def ensure_identity(
        self, user_id, auth_provider, issuer, subject, email, email_verified
    ):
        self.identity_ensures.append(
            (user_id, auth_provider, issuer, subject, email, email_verified)
        )
        self.identity_metadata.setdefault(user_id, {})

    def get_identity_app_metadata(self, user_id):
        if user_id not in self.identity_metadata:
            raise KeyError(f"No fake identity metadata for {user_id}")
        return dict(self.identity_metadata[user_id])

    def register_current_member(
        self, tenant_id, user_id, provider, external_account_id, workspace_type,
        workspace_name, groups, roles, member_limit,
    ):
        if self.registration_error:
            raise self.registration_error
        workspace = next(
            (row for row in self.workspaces.values() if row["tenant_id"] == tenant_id),
            None,
        )
        if workspace is None:
            workspace = self.workspaces.setdefault(
                (provider, external_account_id),
                {
                    "tenant_id": tenant_id,
                    "workspace_type": workspace_type,
                    "workspace_name": workspace_name or "Workspace",
                    "owner_user_id": user_id,
                    "member_limit": member_limit,
                },
            )
        if workspace["tenant_id"] != tenant_id:
            raise ValueError("tenant mismatch")
        key = (tenant_id, user_id)
        if key not in self.members and sum(1 for tenant, _ in self.members if tenant == tenant_id) >= member_limit:
            raise TrialMemberLimitError("Trial workspace member limit reached", member_limit)
        self.members[key] = {"member_groups": groups, "member_roles": roles}
        return sum(1 for tenant, _ in self.members if tenant == tenant_id)

    def write_audit(self, record):
        if self.audit_error:
            raise self.audit_error
        self.audit.append(record)

    def is_workspace_closed(self, tenant_id):
        return tenant_id in self.closed_workspaces

    def export_workspace(self, tenant_id):
        workspace = next(
            (row for row in self.workspaces.values() if row["tenant_id"] == tenant_id),
            {
                "tenant_id": tenant_id,
                "workspace_type": "personal",
                "workspace_name": "Workspace",
            },
        )
        return {
            "workspace": workspace,
            "members": [],
            "groups": [],
            "documents": [],
            "audit_events": [],
        }

    def create_workspace_offboarding_request(self, tenant_id, requester_user_id):
        if self.is_workspace_closed(tenant_id):
            raise OffboardingRequestConflictError("Workspace has already been closed")
        if any(
            row["tenant_id"] == tenant_id and row["status"] == "pending"
            for row in self.offboarding_requests
        ):
            raise OffboardingRequestConflictError("An offboarding request is already pending")
        request = {
            "request_id": str(uuid4()),
            "tenant_id": tenant_id,
            "requester_user_id": requester_user_id,
            "status": "pending",
            "requested_at": "2026-11-20T00:00:00Z",
        }
        self.offboarding_requests.append(request)
        return request

    def list_workspace_offboarding_requests(self, actor_user_id):
        assert self.is_platform_admin(actor_user_id)
        return [
            row for row in self.offboarding_requests
            if row["status"] == "pending"
        ]

    def review_workspace_offboarding_request(
        self, request_id, actor_user_id, approve, review_note="",
    ):
        assert self.is_platform_admin(actor_user_id)
        row = next(
            (
                item for item in self.offboarding_requests
                if item["request_id"] == request_id and item["status"] == "pending"
            ),
            None,
        )
        if row is None:
            raise OffboardingRequestConflictError("The offboarding request is no longer pending")
        row["status"] = "approved" if approve else "rejected"
        row["reviewed_by"] = actor_user_id
        row["review_note"] = review_note
        if approve:
            tenant_id = row["tenant_id"]
            self.closed_workspaces.add(tenant_id)
            self.documents = {
                key: value for key, value in self.documents.items() if key[0] != tenant_id
            }
            self.audit = [
                event for event in self.audit
                if event.get("tenant_id") != tenant_id
            ]
            self.members = {
                key: value for key, value in self.members.items() if key[0] != tenant_id
            }
            self.workspace_groups.pop(tenant_id, None)
            self.workspaces = {
                key: value for key, value in self.workspaces.items()
                if value["tenant_id"] != tenant_id
            }
        return row


class SimpleAnswer:
    def generate(self, question, hits, messages):
        return f"The retention period is 7 years [1]."


@pytest.fixture
def api():
    auth = FakeAuth()
    store = FakeStore()
    store.identity_metadata = auth.identity_metadata
    app = create_app(
        settings=Settings(
            supabase_url="https://tenant-a.supabase.co",
            supabase_anon_key="public-anon-key",
            supabase_service_role_key="server-only-service-key",
            deployment=DeploymentConfig(model="local-preview"),
        ),
        auth_verifier=auth,
        store=store,
        embedder=FakeEmbedder(),
        backend=SimpleAnswer(),
    )
    return TestClient(app), store


def test_query_requires_authenticated_identity(api):
    client, _store = api
    response = client.post("/v1/query", json={"question": "How long is retention?"})
    assert response.status_code == 401


def test_chat_ui_and_static_assets_are_served_with_security_headers(api):
    client, _store = api
    page = client.get("/")
    assert page.status_code == 200
    assert "Doc Reader" in page.text
    assert "Continue with Google" in page.text
    assert "Continue with Microsoft" in page.text
    assert 'id="upload-form"' in page.text
    assert 'id="document-library"' in page.text
    assert 'id="platform-admin-view"' in page.text
    assert 'id="platform-admin-form"' in page.text
    assert "Everyone in this workspace" in page.text
    assert "Only members of those groups can find this document." in page.text
    assert "Drop a document here" in page.text
    assert 'id="selected-file"' in page.text
    assert 'id="library-search"' in page.text
    assert 'id="library-access-filter"' in page.text
    assert 'id="question-count"' in page.text
    assert "Shift+Enter for a new line" in page.text
    assert "Clear conversation" in page.text
    assert 'id="export-conversation"' in page.text
    assert 'class="skip-link"' in page.text
    assert 'aria-label="Workspace navigation"' in page.text
    assert 'class="onboarding-progress"' in page.text
    assert 'id="acl-chip-list"' in page.text
    assert page.headers["x-frame-options"] == "DENY"
    assert "script-src 'self'" in page.headers["content-security-policy"]

    script = client.get("/assets/chat.js")
    assert script.status_code == 200
    assert "grant_type=pkce" in script.text
    assert "onboarding_required" in script.text
    assert "/v1/platform/organizations" in script.text
    assert "/v1/platform/admin-audit" in script.text
    api_response = client.post("/v1/query", json={"question": "retention?"})
    assert api_response.status_code == 401
    assert api_response.headers["cache-control"] == "no-store"
    assert "Authorization" in api_response.headers["vary"]
    assert "instanceof FormData" in script.text
    assert "initialDetail" in script.text
    assert "Your session has ended. Please sign in again." in script.text
    assert "Up to 15 users" in script.text
    assert "5 documents" in page.text
    assert "Save access" in script.text
    assert "Unsaved changes" in script.text
    assert "Everyone in workspace" in script.text
    assert "formatFileSize" in script.text
    assert "is-dragging" in script.text
    assert "No documents match these search and access filters." in script.text
    assert "updateComposerState" in script.text
    assert "Copy answer" in script.text
    assert "Reuse question" in script.text
    assert "source-preview" in script.text
    assert "renderAclChips" in script.text
    assert "closeWorkspacePanels" in script.text
    assert "updateOnboardingProgress" in script.text
    assert "aria-busy" in script.text
    assert "Answer and sources copied." in script.text
    assert "exportConversation" in script.text
    assert "Exported ${new Date().toLocaleString()}" in script.text
    assert "Conversation exported to a text file on this device." in script.text
    assert "Retry last question" in page.text


def test_api_docs_csp_allows_swagger_assets_without_weakening_app_csp(api):
    client, _store = api
    docs = client.get("/docs")
    assert docs.status_code == 200
    assert "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js" in docs.text
    docs_csp = docs.headers["content-security-policy"]
    assert "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net" in docs_csp
    assert "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net" in docs_csp

    page = client.get("/")
    page_csp = page.headers["content-security-policy"]
    assert "script-src 'self';" in page_csp
    assert "unsafe-inline" not in page_csp


def test_health_live_ready_and_request_id(api):
    client, _store = api
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").status_code == 200
    assert client.get("/health/ready").json() == {"status": "ready"}
    response = client.get("/health/ready", headers={"X-Request-ID": "trace-123"})
    assert response.headers["x-request-id"] == "trace-123"


def test_metrics_caches_database_probe(api):
    client, store = api
    calls = {"count": 0}
    original_trial_usage = store.trial_usage

    def counted_trial_usage(tenant_id):
        calls["count"] += 1
        return original_trial_usage(tenant_id)

    store.trial_usage = counted_trial_usage
    app = client.app
    app.state.database_probe_at = 0.0
    app.state.database_status = 0
    first = client.get("/metrics")
    second = client.get("/metrics")
    assert first.status_code == 200
    assert second.status_code == 200
    assert calls["count"] <= 1


def test_prometheus_metrics_endpoint(api):
    client, _store = api
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "docqa_app_ready" in response.text
    assert "docqa_http_requests_total" in response.text
    assert "docqa_database_ready" in response.text
    assert "docqa_provider_ready" in response.text
    assert "question" not in response.text


def test_invalid_backend_fails_at_app_startup():
    with pytest.raises(ValueError, match="DOCQA_BACKEND"):
        create_app(settings=Settings(backend="unsupported"))


def test_api_docs_are_disabled_for_shared_hosted_deployment():
    app = create_app(
        settings=Settings(
            supabase_url="https://tenant-a.supabase.co",
            supabase_anon_key="public-anon-key",
            supabase_service_role_key="server-only-service-key",
        ),
        auth_verifier=FakeAuth(),
        store=FakeStore(),
        embedder=FakeEmbedder(),
        backend=SimpleAnswer(),
    )
    client = TestClient(app)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_graph_group_sync_freshness_requires_recent_timezone_aware_timestamp():
    now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    assert _graph_group_sync_is_fresh(
        {"graph_groups_synced_at": "2026-11-20T00:01:00Z"},
        now=now,
    )
    assert not _graph_group_sync_is_fresh(
        {"graph_groups_synced_at": "2026-11-19T23:59:00Z"},
        now=now,
    )
    assert not _graph_group_sync_is_fresh(
        {"graph_groups_synced_at": "2026-11-20T12:01:00Z"},
        now=now,
    )
    assert not _graph_group_sync_is_fresh(
        {"graph_groups_synced_at": "2026-11-20T11:00:00"},
        now=now,
    )


def test_audit_question_defaults_to_hash_without_logging_question_text():
    question = "What is the confidential retention period?"
    assert Settings().audit_questions == "hash"
    audited = _audit_question(Settings().audit_questions, question)
    assert audited.startswith("sha256:")
    assert question not in audited


def test_upload_body_limit_rejects_missing_bearer_before_buffering():
    from docqa.api import _UploadRequestBodyLimitMiddleware

    async def exercise():
        downstream_called = False
        sent = []

        async def downstream(scope, receive, send):
            nonlocal downstream_called
            downstream_called = True

        async def receive():
            return {"type": "http.request", "body": b"x", "more_body": False}

        async def send(message):
            sent.append(message)

        middleware = _UploadRequestBodyLimitMiddleware(downstream, max_body_bytes=4)
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/v1/documents",
            "headers": [],
        }
        await middleware(scope, receive, send)
        return downstream_called, sent

    downstream_called, sent = asyncio.run(exercise())
    assert not downstream_called
    assert sent[0]["status"] == 401
    assert b"Bearer access token" in sent[1]["body"]


def test_upload_body_limit_times_out_stalled_stream():
    from docqa.api import _UploadRequestBodyLimitMiddleware

    async def exercise():
        downstream_called = False
        sent = []

        async def downstream(scope, receive, send):
            nonlocal downstream_called
            downstream_called = True

        async def receive():
            await asyncio.sleep(0.02)
            return {"type": "http.request", "body": b"x", "more_body": True}

        async def send(message):
            sent.append(message)

        middleware = _UploadRequestBodyLimitMiddleware(
            downstream, max_body_bytes=4, receive_timeout=0.005
        )
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/v1/documents",
            "headers": [(b"authorization", b"Bearer test-token")],
        }
        await middleware(scope, receive, send)
        return downstream_called, sent

    downstream_called, sent = asyncio.run(exercise())
    assert not downstream_called
    assert sent[0]["status"] == 408
    assert b"timed out" in sent[1]["body"]


def test_upload_body_limit_rejects_chunked_request_before_form_parsing():
    from docqa.api import _UploadRequestBodyLimitMiddleware

    async def exercise():
        downstream_called = False
        sent = []
        messages = iter([
            {"type": "http.request", "body": b"123", "more_body": True},
            {"type": "http.request", "body": b"45", "more_body": False},
        ])

        async def downstream(scope, receive, send):
            nonlocal downstream_called
            downstream_called = True

        async def receive():
            return next(messages)

        async def send(message):
            sent.append(message)

        middleware = _UploadRequestBodyLimitMiddleware(downstream, max_body_bytes=4)
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/v1/documents",
            "headers": [(b"authorization", b"Bearer test-token")],
        }
        await middleware(scope, receive, send)
        return downstream_called, sent

    downstream_called, sent = asyncio.run(exercise())
    assert not downstream_called
    assert sent[0]["status"] == 413
    assert b"configured request-body limit" in sent[1]["body"]


def test_legal_pages_are_served(api):
    client, _store = api
    for path, phrase in (
        ("/privacy", "Isibuko Digital Solutions"),
        ("/terms", "Draft for legal review"),
        ("/subprocessors", "NVIDIA NIM"),
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert phrase in response.text


def test_workspace_export_is_admin_only_and_downloadable(api):
    client, _store = api
    assert client.get(
        "/v1/workspace/export",
        headers={"Authorization": "Bearer staff"},
    ).status_code == 403

    response = client.get(
        "/v1/workspace/export",
        headers={"Authorization": "Bearer admin"},
    )
    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="workspace-export.json"'
    assert response.json()["workspace"]["tenant_id"] == "tenant-a"


def test_workspace_offboarding_requires_platform_approval_and_closes_access(api):
    client, store = api
    store.upsert_document("tenant-a", "policy.txt", ["staff"], "user-admin", [{
        "location": "section 1",
        "content": "Workspace content",
        "embedding": [1.0] * 384,
    }])
    store.audit.append({
        "tenant_id": "tenant-a",
        "user_id": "user-staff",
        "action": "query",
        "question": "private question",
    })

    denied = client.post(
        "/v1/workspace/offboarding",
        headers={"Authorization": "Bearer staff"},
    )
    assert denied.status_code == 403

    created = client.post(
        "/v1/workspace/offboarding",
        headers={"Authorization": "Bearer admin"},
    )
    assert created.status_code == 200
    assert created.json()["status"] == "pending"
    request_id = created.json()["request_id"]
    duplicate = client.post(
        "/v1/workspace/offboarding",
        headers={"Authorization": "Bearer admin"},
    )
    assert duplicate.status_code == 409

    assert client.get(
        "/v1/platform/offboarding-requests",
        headers={"Authorization": "Bearer staff"},
    ).status_code == 403
    requests = client.get(
        "/v1/platform/offboarding-requests",
        headers={"Authorization": "Bearer platform-admin"},
    )
    assert requests.status_code == 200
    assert requests.json()[0]["request_id"] == request_id

    reviewed = client.post(
        f"/v1/platform/offboarding-requests/{request_id}/review",
        headers={"Authorization": "Bearer platform-admin"},
        json={"approve": True, "review_note": "Approved"},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "approved"
    assert not store.documents
    assert not store.audit
    assert not store.members
    assert "tenant-a" in store.closed_workspaces
    assert client.get(
        "/v1/me",
        headers={"Authorization": "Bearer admin"},
    ).status_code == 403
    stylesheet = client.get("/assets/chat.css")
    assert stylesheet.status_code == 200 and "prefers-reduced-motion" in stylesheet.text


def test_chat_auth_config_exposes_only_public_supabase_key(api):
    client, _store = api
    response = client.get("/v1/auth/config")
    assert response.status_code == 200
    assert response.json() == {
        "supabase_url": "https://tenant-a.supabase.co",
        "supabase_anon_key": "public-anon-key",
    }
    assert "server-only-service-key" not in response.text


def test_cognito_auth_config_exposes_only_public_client_settings():
    settings = Settings(
        cognito_region="eu-north-1",
        cognito_user_pool_id="eu-north-1_example",
        cognito_client_id="public-client",
        cognito_domain="https://example.auth.eu-north-1.amazoncognito.com",
        microsoft_graph_client_id="graph-public-client",
        deployment=DeploymentConfig(auth_provider="cognito"),
    )
    app = create_app(
        settings=settings,
        auth_verifier=FakeAuth(),
        store=FakeStore(),
        embedder=FakeEmbedder(),
        backend=SimpleAnswer(),
    )
    with TestClient(app, base_url="https://reader.example.com") as client:
        response = client.get("/v1/auth/config")

    assert response.status_code == 200
    assert response.json() == {
        "auth_provider": "cognito",
        "cognito_domain": "https://example.auth.eu-north-1.amazoncognito.com",
        "cognito_client_id": "public-client",
        "cognito_callback_url": "https://reader.example.com/",
        "cognito_logout_url": "https://reader.example.com/",
        "microsoft_graph_client_id": "graph-public-client",
    }


def test_google_personal_onboarding_creates_private_preview_workspace(api):
    client, store = api
    headers = {"Authorization": "Bearer new-google"}
    options = client.get("/v1/onboarding/options", headers=headers)
    assert options.status_code == 200
    assert options.json() == {
        "onboarding_required": True,
        "account_type": "personal",
        "provider": "google",
        "organization_name": None,
        "organization_request_status": None,
        "organization_setup_available": False,
        "organization_graph_required": False,
        "is_platform_admin": False,
    }

    response = client.post(
        "/v1/onboarding",
        headers=headers,
        json={"account_type": "personal", "workspace_name": "My Preview"},
    )
    assert response.status_code == 201
    assert response.json()["name"] == "My Preview"
    assert response.json()["workspace_type"] == "personal"
    assert response.json()["member_count"] == 1
    assert response.json()["member_limit"] == 10
    assert store.updated_metadata["user-google"]["roles"] == ["admin"]
    assert store.updated_metadata["user-google"]["tenant_id"] == response.json()["tenant_id"]

    already_onboarded = client.post(
        "/v1/onboarding",
        headers=headers,
        json={"account_type": "organization", "workspace_name": "Wrong type"},
    )
    assert already_onboarded.status_code == 409
    assert already_onboarded.json()["detail"] == "This account already has a workspace"


def test_supabase_auth_registers_identity_in_separate_postgres_store():
    auth = FakeAuth()
    store = FakeStore()
    store.identity_metadata = auth.identity_metadata
    app = create_app(
        settings=Settings(
            supabase_url="https://tenant-a.supabase.co",
            supabase_anon_key="public-anon-key",
            supabase_service_role_key="server-only-service-key",
            deployment=DeploymentConfig(
                model="local-preview",
                document_store="postgres",
            ),
        ),
        auth_verifier=auth,
        store=store,
        embedder=FakeEmbedder(),
        backend=SimpleAnswer(),
    )

    with TestClient(app) as client:
        response = client.get(
            "/v1/onboarding/options",
            headers={"Authorization": "Bearer new-google"},
        )

    assert response.status_code == 200
    assert store.identity_ensures == [
        (
            "user-google",
            "supabase",
            "supabase",
            "user-google",
            "user@example.com",
            False,
        )
    ]


def test_microsoft_organization_onboarding_joins_verified_org_and_limits_seats(api):
    client, store = api
    store.set_registered_organization(
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "Acme Ltd",
        ["owner@acme.example"],
    )
    options = client.get(
        "/v1/onboarding/options",
        headers={"Authorization": "Bearer new-org-owner"},
    )
    assert options.json()["account_type"] == "organization"
    assert options.json()["organization_name"] == "Acme Ltd"

    member = client.post(
        "/v1/onboarding",
        headers={"Authorization": "Bearer new-org-member"},
        json={"account_type": "organization"},
    )
    assert member.status_code == 201
    assert store.updated_metadata["org-member"]["roles"] == []

    owner = client.post(
        "/v1/onboarding",
        headers={"Authorization": "Bearer new-org-owner"},
        json={"account_type": "organization"},
    )
    assert owner.status_code == 201
    assert owner.json()["name"] == "Acme Ltd"
    assert owner.json()["member_limit"] == 15
    assert owner.json()["tenant_id"] == member.json()["tenant_id"]
    assert owner.json()["member_count"] == 2
    assert store.updated_metadata["org-owner"]["roles"] == ["admin"]


def test_unregistered_microsoft_organization_gets_individual_personal_workspaces(api):
    client, store = api
    owner_headers = {"Authorization": "Bearer new-org-owner"}
    member_headers = {"Authorization": "Bearer new-org-member"}

    options = client.get("/v1/onboarding/options", headers=owner_headers)
    assert options.status_code == 200
    assert options.json()["account_type"] == "personal"
    assert options.json()["organization_name"] is None
    assert options.json()["organization_setup_available"] is True
    denied_org_setup = client.post(
        "/v1/onboarding",
        headers=owner_headers,
        json={"account_type": "organization", "workspace_name": "Acme"},
    )
    assert denied_org_setup.status_code == 403

    owner = client.post(
        "/v1/onboarding",
        headers=owner_headers,
        json={"account_type": "personal"},
    )
    member = client.post(
        "/v1/onboarding",
        headers=member_headers,
        json={"account_type": "personal"},
    )
    assert owner.status_code == member.status_code == 201
    assert owner.json()["workspace_type"] == member.json()["workspace_type"] == "personal"
    assert owner.json()["member_limit"] == member.json()["member_limit"] == 10
    assert owner.json()["tenant_id"] != member.json()["tenant_id"]
    assert store.updated_metadata["org-owner"]["roles"] == ["admin"]
    assert store.updated_metadata["org-member"]["roles"] == ["admin"]
    assert ("azure", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa") not in store.workspaces


def test_graph_catalog_and_request_require_matching_verified_identity(api, monkeypatch):
    client, _store = api

    class FakeGraph:
        def __init__(self, _token):
            pass

        def current_user(self):
            return {"id": "different-user"}

        def organization(self):
            return {
                "tenant_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "name": "Acme Ltd",
                "verified_domains": ["acme.example"],
            }

    monkeypatch.setattr("docqa.api_dependencies.MicrosoftGraph", FakeGraph)
    headers = {
        "Authorization": "Bearer new-org-owner",
        "X-Microsoft-Graph-Token": "graph-token",
    }

    response = client.get("/v1/organization/graph/catalog", headers=headers)

    assert response.status_code == 403
    assert "does not match" in response.json()["detail"]


def test_platform_admin_approves_graph_request_and_requester_becomes_workspace_admin(api, monkeypatch):
    client, store = api
    group_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"

    class FakeGraph:
        def __init__(self, token):
            self.token = token

        def current_user(self):
            return {
                "id": "org-member"
                if self.token in {"member-graph-token", "empty-group-token"}
                else "org-owner"
            }

        def organization(self):
            return {
                "tenant_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "name": "Acme Ltd",
                "verified_domains": ["acme.example"],
            }

        def security_groups(self):
            return [{"id": group_id, "display_name": "Finance"}]

        def current_user_role_template_ids(self):
            return (
                {"62e90394-69f5-4237-9190-012177145e10"}
                if self.token == "graph-token"
                else set()
            )

        def current_user_group_ids(self):
            return set() if self.token == "empty-group-token" else {group_id}

    monkeypatch.setattr("docqa.api_dependencies.MicrosoftGraph", FakeGraph)
    graph_headers = {
        "Authorization": "Bearer new-org-owner",
        "X-Microsoft-Graph-Token": "graph-token",
    }
    catalog = client.get("/v1/organization/graph/catalog", headers=graph_headers)
    assert catalog.status_code == 200
    assert catalog.json()["groups"] == [{"id": group_id, "display_name": "Finance"}]

    non_admin = client.post(
        "/v1/organization/requests",
        headers={
            "Authorization": "Bearer new-org-owner",
            "X-Microsoft-Graph-Token": "graph-no-admin",
        },
        json={"groups": [{"group_id": group_id, "workspace_group_name": "Finance"}]},
    )
    assert non_admin.status_code == 403
    assert "administrator" in non_admin.json()["detail"].lower()

    submitted = client.post(
        "/v1/organization/requests",
        headers=graph_headers,
        json={"groups": [{"group_id": group_id, "workspace_group_name": "Finance"}]},
    )
    assert submitted.status_code == 201
    assert submitted.json()["requester_email"] == "owner@acme.example"
    assert store.list_organization_requests(
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    )[0]["status"] == "pending"

    admin_headers = {"Authorization": "Bearer platform-admin"}
    denied = client.post(
        f"/v1/platform/organization-requests/{submitted.json()['request_id']}/review",
        headers={"Authorization": "Bearer new-google"},
        json={"approve": True},
    )
    assert denied.status_code == 403

    reviewed = client.post(
        f"/v1/platform/organization-requests/{submitted.json()['request_id']}/review",
        headers=admin_headers,
        json={"approve": True, "review_note": "Verified tenant and groups"},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "approved"
    assert store.organizations["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"][
        "initial_admin_emails"
    ] == ["owner@acme.example"]
    member_options = client.get(
        "/v1/onboarding/options",
        headers={"Authorization": "Bearer graph-member"},
    )
    assert member_options.json()["organization_graph_required"] is True

    member_headers = {"Authorization": "Bearer graph-member"}
    denied_member = client.post(
        "/v1/onboarding",
        headers=member_headers,
        json={"account_type": "organization"},
    )
    assert denied_member.status_code == 403
    assert "Connect Microsoft Graph" in denied_member.json()["detail"]

    non_member = client.post(
        "/v1/onboarding",
        headers={
            **member_headers,
            "X-Microsoft-Graph-Token": "empty-group-token",
        },
        json={"account_type": "organization"},
    )
    assert non_member.status_code == 403
    assert "not a member" in non_member.json()["detail"]

    member = client.post(
        "/v1/onboarding",
        headers={
            **member_headers,
            "X-Microsoft-Graph-Token": "member-graph-token",
        },
        json={"account_type": "organization"},
    )
    assert member.status_code == 201
    assert store.updated_metadata["org-member"]["groups"] == ["Finance"]
    assert store.updated_metadata["org-member"]["graph_groups"] == ["Finance"]
    assert store.updated_metadata["org-member"]["graph_groups_synced_at"]
    assert store.updated_metadata["org-member"]["roles"] == []

    onboarded = client.post(
        "/v1/onboarding",
        headers={"Authorization": "Bearer new-org-owner"},
        json={"account_type": "organization"},
    )
    assert onboarded.status_code == 201
    assert store.updated_metadata["org-owner"]["roles"] == ["admin"]
    assert "Finance" in store.workspace_groups[onboarded.json()["tenant_id"]]


def test_graph_membership_sync_revokes_removed_mapped_groups_without_changing_roles(api, monkeypatch):
    client, store = api
    tenant_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    group_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    store.set_registered_organization(tenant_id, "Acme Ltd", ["owner@acme.example"])
    store.organization_group_mappings[tenant_id] = [{
        "group_id": group_id,
        "display_name": "Finance",
        "workspace_group_name": "Finance",
    }]
    store.workspaces[("azure", tenant_id)] = {
        "tenant_id": "workspace-id",
        "workspace_type": "organization",
        "workspace_name": "Acme Ltd",
        "owner_user_id": "org-owner",
        "member_limit": 15,
    }
    store.members[("workspace-id", "org-owner")] = {
        "member_groups": ["owner", "Finance"],
        "member_roles": ["admin"],
    }

    class FakeGraph:
        def __init__(self, _token):
            pass

        def current_user(self):
            return {"id": "org-owner"}

        def organization(self):
            return {
                "tenant_id": tenant_id,
                "name": "Acme Ltd",
                "verified_domains": ["acme.example"],
            }

        def current_user_group_ids(self):
            return set()

    monkeypatch.setattr("docqa.api_dependencies.MicrosoftGraph", FakeGraph)
    response = client.post(
        "/v1/organization/graph/sync",
        headers={
            "Authorization": "Bearer graph-owner",
            "X-Microsoft-Graph-Token": "graph-token",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"groups": []}
    assert store.updated_metadata["org-owner"]["groups"] == ["owner"]
    assert store.updated_metadata["org-owner"]["roles"] == ["admin"]
    assert store.members[("workspace-id", "org-owner")]["member_groups"] == ["owner"]
    assert store.members[("workspace-id", "org-owner")]["member_roles"] == ["admin"]


def test_stale_graph_membership_blocks_access_but_allows_sync(api, monkeypatch):
    client, store = api
    tenant_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    store.set_registered_organization(tenant_id, "Acme Ltd", ["owner@acme.example"])
    store.organization_group_mappings[tenant_id] = []

    denied = client.get(
        "/v1/me",
        headers={"Authorization": "Bearer stale-graph-owner"},
    )
    assert denied.status_code == 403
    assert "older than 12 hours" in denied.json()["detail"]

    class FakeGraph:
        def __init__(self, _token):
            pass

        def current_user(self):
            return {"id": "org-owner"}

        def organization(self):
            return {"tenant_id": tenant_id, "name": "Acme Ltd", "verified_domains": []}

        def current_user_group_ids(self):
            return set()

    monkeypatch.setattr("docqa.api_dependencies.MicrosoftGraph", FakeGraph)
    synced = client.post(
        "/v1/organization/graph/sync",
        headers={
            "Authorization": "Bearer stale-graph-owner",
            "X-Microsoft-Graph-Token": "graph-token",
        },
    )
    assert synced.status_code == 200
    assert store.updated_metadata["org-owner"]["graph_groups_synced_at"]


def test_platform_admin_can_manage_registered_organizations(api):
    client, store = api
    org_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    organization = store.set_registered_organization(
        org_id, "Acme Ltd", ["owner@acme.example"]
    )

    denied = client.get(
        "/v1/platform/organizations",
        headers={"Authorization": "Bearer new-google"},
    )
    assert denied.status_code == 403

    headers = {"Authorization": "Bearer platform-admin"}
    options = client.get("/v1/onboarding/options", headers=headers)
    assert options.json()["is_platform_admin"] is True
    assert client.get("/v1/platform/organizations", headers=headers).json() == [organization]

    removed = client.delete(f"/v1/platform/organizations/{org_id}", headers=headers)
    assert removed.status_code == 200
    assert removed.json() == {"removed": True}
    assert store.list_registered_organizations() == []


def test_platform_admin_registry_audits_changes_and_keeps_a_last_admin(api):
    client, store = api
    headers = {"Authorization": "Bearer platform-admin"}

    spoofed = client.get(
        "/v1/platform/admins",
        headers={"Authorization": "Bearer spoofed-platform-admin"},
    )
    assert spoofed.status_code == 403

    granted = client.post(
        "/v1/platform/admins",
        headers=headers,
        json={"email": "OPS@example.com"},
    )
    assert granted.status_code == 201
    assert granted.json()["user_id"] == "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    assert granted.json()["email"] == "ops@example.com"

    audit = client.get("/v1/platform/admin-audit", headers=headers)
    assert audit.status_code == 200
    assert [event["action"] for event in audit.json()] == ["grant"]

    revoked = client.delete(
        "/v1/platform/admins/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        headers=headers,
    )
    assert revoked.status_code == 200
    assert revoked.json() == {"removed": True}
    audit = client.get("/v1/platform/admin-audit", headers=headers)
    assert [event["action"] for event in audit.json()] == ["revoke", "grant"]

    last_admin = client.delete(
        "/v1/platform/admins/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        headers=headers,
    )
    assert last_admin.status_code == 409
    assert "at least one" in last_admin.json()["detail"].lower()

    missing = client.post(
        "/v1/platform/admins",
        headers=headers,
        json={"email": "missing@example.com"},
    )
    assert missing.status_code == 404
    assert len(store.platform_admin_audit) == 2


def test_legacy_platform_admin_metadata_does_not_grant_global_access_or_leak_into_tenant_roles(api):
    client, store = api
    headers = {"Authorization": "Bearer platform-tenant-admin"}

    principal = client.get("/v1/me", headers=headers)
    assert principal.status_code == 200
    assert principal.json()["is_admin"] is True
    assert principal.json()["is_platform_admin"] is False
    assert store.members[("tenant-a", "platform-tenant-admin")]["member_roles"] == ["admin"]

    denied = client.get("/v1/platform/admins", headers=headers)
    assert denied.status_code == 403


def test_platform_admin_cannot_bypass_graph_organization_review(api):
    client, _store = api
    response = client.put(
        "/v1/platform/organizations/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        headers={"Authorization": "Bearer platform-admin"},
        json={"name": "Acme", "initial_admin_emails": ["owner@acme.example"]},
    )
    assert response.status_code == 405


def test_microsoft_personal_and_unverified_identity_onboarding(api):
    client, _store = api
    personal = client.get(
        "/v1/onboarding/options",
        headers={"Authorization": "Bearer new-ms-personal"},
    )
    assert personal.json()["account_type"] == "personal"
    assert personal.json()["organization_setup_available"] is False
    created = client.post(
        "/v1/onboarding",
        headers={"Authorization": "Bearer new-ms-personal"},
        json={"account_type": "personal"},
    )
    assert created.status_code == 201

    unknown = client.post(
        "/v1/onboarding",
        headers={"Authorization": "Bearer new-unverified-azure"},
        json={"account_type": "personal"},
    )
    assert unknown.status_code == 403


def test_trial_document_limit_is_enforced_but_replacing_existing_document_is_allowed(api):
    client, store = api
    for index in range(5):
        store.upsert_document("tenant-a", f"doc-{index}.txt", ["staff"], "user-admin", [])

    limited = client.post(
        "/v1/documents",
        headers={"Authorization": "Bearer admin"},
        data={"acl_groups": "staff"},
        files={"file": ("sixth.txt", b"Another policy.", "text/plain")},
    )
    assert limited.status_code == 402
    assert "limited to 5 documents" in limited.json()["detail"]

    replacement = client.post(
        "/v1/documents",
        headers={"Authorization": "Bearer admin"},
        data={"acl_groups": "staff"},
        files={"file": ("doc-0.txt", b"Updated policy.", "text/plain")},
    )
    assert replacement.status_code == 201


def test_usage_returns_document_cap_and_workspace_seat_tracking(api):
    client, store = api
    store.set_registered_organization("tenant-guid", "Acme", ["admin@acme.example"])
    store.provision_trial_workspace(
        "user-admin", "azure", "tenant-guid", "organization", "Acme", 15
    )
    store.workspaces[("azure", "tenant-guid")]["tenant_id"] = "tenant-a"
    store.members[("tenant-a", "user-admin")] = store.members.pop(
        next(key for key in store.members if key[1] == "user-admin")
    )
    response = client.get("/v1/usage", headers={"Authorization": "Bearer admin"})
    assert response.status_code == 200
    assert response.json()["workspace_type"] == "organization"
    assert response.json()["member_count"] == 1
    assert response.json()["member_limit"] == 15
    assert response.json()["document_limit"] == 5
    assert response.json()["member_count_tracked"] is True
    assert response.json()["upgrade_available"] is False


def test_authenticated_requests_register_current_and_legacy_members(api):
    client, store = api
    response = client.get("/v1/me", headers={"Authorization": "Bearer admin"})
    assert response.status_code == 200
    assert store.members[("tenant-a", "user-admin")]["member_roles"] == ["admin"]
    assert store.workspaces[("azure", "tenant-a")]["member_limit"] == 15

    legacy = client.get("/v1/me", headers={"Authorization": "Bearer legacy-email"})
    assert legacy.status_code == 200
    assert store.members[("tenant-legacy", "legacy-user")]["member_groups"] == ["staff"]
    assert store.workspaces[("legacy", "tenant-legacy")]["member_limit"] == 15


def test_member_limit_registration_returns_payment_required(api):
    client, store = api
    store.registration_error = TrialMemberLimitError(
        "Trial workspace member limit reached", member_limit=15
    )
    response = client.get("/v1/me", headers={"Authorization": "Bearer admin"})
    assert response.status_code == 402
    assert "limited to 15 users" in response.json()["detail"]


@pytest.mark.parametrize(
    ("tenant_id", "workspace_type", "member_limit", "token", "expected_detail"),
    [
        ("tenant-a", "organization", 15, "organization-sixteenth", "limited to 15 users"),
        ("tenant-personal", "personal", 10, "personal-eleventh", "limited to 10 users"),
    ],
)
def test_authenticated_member_registration_enforces_workspace_seat_caps(
    api, tenant_id, workspace_type, member_limit, token, expected_detail
):
    client, store = api
    store.workspaces[(f"existing-{workspace_type}", tenant_id)] = {
        "tenant_id": tenant_id,
        "workspace_type": workspace_type,
        "workspace_name": "Workspace",
        "owner_user_id": "existing-owner",
        "member_limit": member_limit,
    }
    for index in range(member_limit):
        store.members[(tenant_id, f"existing-user-{index}")] = {
            "member_groups": ["staff"],
            "member_roles": [],
        }

    response = client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 402
    assert expected_detail in response.json()["detail"]


def test_tenant_branding_is_selected_by_hostname_and_enforces_tenant(api):
    _client, store = api
    auth = FakeAuth()
    store.identity_metadata = auth.identity_metadata
    app = create_app(
        settings=Settings(
            supabase_url="https://tenant-a.supabase.co",
            supabase_anon_key="public-anon-key",
            supabase_service_role_key="server-only-service-key",
            tenant_branding={
                "acme.example.com": TenantBranding(
                    tenant_id="tenant-a",
                    name="Acme",
                    tagline="ACME KNOWLEDGE",
                    logo_url="/branding/acme.svg",
                    primary_color="#164e63",
                    accent_color="#f0f9ff",
                    support_url="mailto:support@acme.example.com",
                    hide_attribution=True,
                )
            },
        ),
        auth_verifier=auth,
        store=store,
        embedder=FakeEmbedder(),
        backend=SimpleAnswer(),
    )
    client = TestClient(app, base_url="https://acme.example.com")

    branding = client.get("/v1/branding")
    assert branding.status_code == 200
    assert branding.json()["name"] == "Acme"
    assert branding.json()["logo_url"] == "/branding/acme.svg"
    assert branding.json()["hide_attribution"] is True
    assert "tenant_id" not in branding.json()

    assert client.get("/v1/me", headers={"Authorization": "Bearer staff"}).status_code == 200
    assert client.get("/v1/me", headers={"Authorization": "Bearer other-tenant"}).status_code == 403


def test_tenant_branding_rejects_external_logo_urls():
    with pytest.raises(ValueError, match="local /branding/ asset"):
        TenantBranding(tenant_id="tenant-a", logo_url="https://example.com/logo.png")


def test_tenant_branding_environment_is_parsed_and_hostname_normalized(monkeypatch):
    monkeypatch.setenv(
        "DOCQA_TENANT_BRANDING_JSON",
        '{"Acme.Example.com.":{"tenant_id":"tenant-a","name":"Acme"}}',
    )
    settings = Settings.from_env()
    assert settings.tenant_branding["acme.example.com"].tenant_id == "tenant-a"


def test_deployment_config_defaults_and_validation(monkeypatch):
    monkeypatch.delenv("DOCQA_DEPLOYMENT_MODEL", raising=False)
    monkeypatch.delenv("DOCQA_AUTH_PROVIDER", raising=False)
    monkeypatch.delenv("DOCQA_DOCUMENT_STORE", raising=False)
    monkeypatch.setenv("DOCQA_DATABASE_URL", "postgresql://db.example/docqa")
    assert Settings.from_env().deployment == DeploymentConfig()
    assert Settings.from_env().database_url == "postgresql://db.example/docqa"

    assert DeploymentConfig(
        model="dedicated-customer",
        auth_provider="supabase",
        document_store="supabase",
    ).model == "dedicated-customer"
    assert DeploymentConfig(auth_provider="cognito").auth_provider == "cognito"
    assert DeploymentConfig(document_store="postgres").document_store == "postgres"

    with pytest.raises(ValueError, match="DOCQA_DEPLOYMENT_MODEL"):
        DeploymentConfig(model="unsupported")
    with pytest.raises(ValueError, match="DOCQA_AUTH_PROVIDER"):
        DeploymentConfig(auth_provider="unsupported")
    with pytest.raises(ValueError, match="DOCQA_DOCUMENT_STORE"):
        DeploymentConfig(document_store="unsupported")


def test_me_returns_only_authenticated_server_resolved_principal(api):
    client, _store = api
    response = client.get("/v1/me", headers={"Authorization": "Bearer staff"})
    assert response.status_code == 200
    assert response.json() == {
        "user_id": "user-staff",
        "tenant_id": "tenant-a",
        "groups": ["staff"],
        "is_admin": False,
        "is_auditor": False,
        "workspace_type": "organization",
        "workspace_name": "",
        "organization_graph_sync_available": False,
        "is_platform_admin": False,
    }


def test_authorization_uses_application_identity_metadata_not_auth_metadata(api):
    client, store = api
    store.identity_metadata["user-google"] = {
        "tenant_id": "tenant-b",
        "groups": ["staff"],
        "roles": [],
        "workspace_type": "organization",
        "workspace_name": "Tenant B",
    }

    response = client.get("/v1/me", headers={"Authorization": "Bearer new-google"})

    assert response.status_code == 200
    assert response.json()["tenant_id"] == "tenant-b"
    assert response.json()["is_admin"] is False


def test_workspace_directory_is_admin_only_and_tenant_scoped(api):
    client, store = api
    store.members[("tenant-a", "user-admin")] = {
        "member_groups": ["staff", "finance"],
        "member_roles": ["admin"],
    }
    store.members[("tenant-b", "user-other")] = {
        "member_groups": ["secret"],
        "member_roles": [],
        "email": "other@tenant.example",
    }

    denied = client.get(
        "/v1/workspace/directory",
        headers={"Authorization": "Bearer staff"},
    )
    assert denied.status_code == 403

    allowed = client.get(
        "/v1/workspace/directory",
        headers={"Authorization": "Bearer admin"},
    )
    assert allowed.status_code == 200
    store.members[("tenant-a", "user-admin")].update(
        member_groups=["staff", "finance"],
        email="admin@acme.example",
    )
    allowed = client.get(
        "/v1/workspace/directory",
        headers={"Authorization": "Bearer admin"},
    )
    assert set(allowed.json()["groups"]) == {"finance", "staff"}
    assert all(member.get("email") != "other@tenant.example" for member in allowed.json()["members"])
    assert "user_id" not in allowed.json()["members"][0]
    assert any(
        member["email"] == "admin@acme.example"
        for member in allowed.json()["members"]
    )


def test_admin_can_create_a_group_before_any_member_has_it(api):
    client, store = api
    response = client.post(
        "/v1/workspace/groups",
        headers={"Authorization": "Bearer admin"},
        json={"name": "finance"},
    )
    assert response.status_code == 201
    directory = client.get(
        "/v1/workspace/directory",
        headers={"Authorization": "Bearer admin"},
    )
    assert "finance" in directory.json()["groups"]
    assert "finance" not in store.members.get(("tenant-a", "user-admin"), {}).get(
        "member_groups", []
    )


def test_admin_dashboard_allows_admin_and_auditor_but_not_staff(api):
    client, store = api
    store.members[("tenant-a", "user-auditor")] = {
        "member_groups": ["staff"],
        "member_roles": ["auditor"],
    }
    store.audit.append({
        "tenant_id": "tenant-a",
        "user_id": "user-auditor",
        "action": "query",
        "question": "secret question text",
        "documents_retrieved": ["policy.pdf"],
        "documents_cited": ["policy.pdf"],
        "outcome": "not_found",
    })
    store.audit.append({
        "tenant_id": "tenant-a",
        "user_id": "user-auditor",
        "action": "query",
        "outcome": "no_citation",
    })
    store.audit.append({
        "tenant_id": "tenant-a",
        "user_id": "user-auditor",
        "action": "query_error",
        "outcome": "error",
    })
    store.audit.append({
        "tenant_id": "tenant-a",
        "user_id": "user-auditor",
        "action": "sign_in",
        "outcome": "ok",
    })

    assert client.get(
        "/v1/admin/dashboard",
        headers={"Authorization": "Bearer staff"},
    ).status_code == 403
    allowed = client.get(
        "/v1/admin/dashboard",
        headers={"Authorization": "Bearer auditor"},
    )
    assert allowed.status_code == 200
    payload = allowed.json()
    assert payload["query_count"] == 3
    assert payload["error_count"] == 1
    assert "question" not in payload["recent_events"][0]
    assert all(event["action"] != "sign_in" for event in payload["recent_events"])


def test_query_rejects_blank_questions(api):
    client, _store = api
    response = client.post(
        "/v1/query",
        headers={"Authorization": "Bearer staff"},
        json={"question": "   "},
    )
    assert response.status_code == 422


def test_chat_includes_hosted_model_provider_disclosure(api):
    client, _store = api
    response = client.get("/")
    assert response.status_code == 200
    assert "Groq" in response.text
    assert "OpenRouter" in response.text
    assert "Cerebras" in response.text
    assert "NVIDIA NIM" in response.text
    assert "authorized retrieved passages" in response.text
    assert 'href="/privacy"' in response.text
    assert response.text.index('id="dashboard-toggle"') < response.text.index('id="upload-toggle"')


def test_query_uses_verified_tenant_and_groups_not_request_claims(api):
    client, store = api
    store.upsert_document("tenant-a", "policy.txt", ["staff"], "user-admin", [{
        "location": "§1",
        "content": "The retention period is 7 years.",
        "embedding": [1.0] * 384,
    }])
    response = client.post(
        "/v1/query",
        headers={"Authorization": "Bearer staff"},
        json={"question": "How long is the retention period?", "tenant_id": "tenant-b",
              "user_groups": ["admin"]},
    )
    assert response.status_code == 422

    response = client.post(
        "/v1/query",
        headers={"Authorization": "Bearer staff"},
        json={"question": "How long is the retention period?"},
    )
    assert response.status_code == 200
    assert store.last_search == ("tenant-a", ("staff",), "How long is the retention period?")
    assert store.audit[-1]["user_id"] == "user-staff"


def test_audit_write_failure_does_not_fail_query(api):
    client, store = api
    store.audit_error = SupabaseError("audit backend unavailable")
    response = client.post(
        "/v1/query",
        headers={"Authorization": "Bearer staff"},
        json={"question": "Can this query complete?"},
    )
    assert response.status_code == 200


def test_principal_registration_does_not_write_sign_in_audit(api):
    client, store = api
    store.audit_error = SupabaseError("audit backend unavailable")
    response = client.get(
        "/v1/me",
        headers={"Authorization": "Bearer admin"},
    )
    assert response.status_code == 200
    assert all(event.get("action") != "sign_in" for event in store.audit)


def test_query_cannot_retrieve_across_tenants(api):
    client, store = api
    store.upsert_document("tenant-b", "policy.txt", ["staff"], "user-other", [{
        "location": "§1",
        "content": "The retention period is 7 years.",
        "embedding": [1.0] * 384,
    }])
    response = client.post(
        "/v1/query",
        headers={"Authorization": "Bearer staff"},
        json={"question": "How long is the retention period?"},
    )
    assert response.status_code == 200
    assert response.json()["found"] is False


def test_ingestion_is_admin_only_and_requires_acl(api):
    client, store = api
    response = client.post(
        "/v1/documents",
        headers={"Authorization": "Bearer staff"},
        data={"acl_groups": "staff"},
        files={"file": ("policy.txt", b"Keep records for 7 years.", "text/plain")},
    )
    assert response.status_code == 403
    assert not store.documents

    response = client.post(
        "/v1/documents",
        headers={"Authorization": "Bearer admin"},
        data={"acl_groups": "staff"},
        files={"file": ("policy.txt", b"Keep records for 7 years.", "text/plain")},
    )
    assert response.status_code == 201
    assert response.json()["chunks"] == 1
    assert ("tenant-a", "policy.txt") in store.documents

    response = client.post(
        "/v1/documents",
        headers={"Authorization": "Bearer admin"},
        data={"acl_groups": "staff"},
        files={"file": ("broken.pdf", b"not a PDF", "application/pdf")},
    )
    assert response.status_code == 422


def test_pdf_page_limit_is_reported_as_payload_too_large(api):
    from io import BytesIO
    from pypdf import PdfWriter

    client, _store = api
    writer = PdfWriter()
    for _ in range(501):
        writer.add_blank_page(width=72, height=72)
    content = BytesIO()
    writer.write(content)

    response = client.post(
        "/v1/documents",
        headers={"Authorization": "Bearer admin"},
        data={"acl_groups": "staff"},
        files={"file": ("large.pdf", content.getvalue(), "application/pdf")},
    )
    assert response.status_code == 413
    assert response.json()["detail"] == "PDF exceeds the 500-page limit"


def test_document_library_is_admin_only_and_tenant_scoped(api):
    client, store = api
    own_id = store.upsert_document("tenant-a", "own.txt", ["staff"], "user-admin", [{
        "location": "p. 1",
        "content": "Internal guide.",
        "embedding": [1.0] * 384,
    }])
    store.upsert_document("tenant-b", "foreign.txt", ["staff"], "user-other", [])

    assert client.get("/v1/documents", headers={"Authorization": "Bearer staff"}).status_code == 403
    foreign_response = client.get(
        "/v1/documents", headers={"Authorization": "Bearer other-tenant-admin"}
    )
    assert foreign_response.status_code == 200
    assert [row["name"] for row in foreign_response.json()] == ["foreign.txt"]
    response = client.get("/v1/documents", headers={"Authorization": "Bearer admin"})
    assert response.status_code == 200
    assert response.json() == [{
        "document_id": own_id,
        "name": "own.txt",
        "acl_groups": ["staff"],
        "uploaded_by": "user-admin",
        "created_at": "2026-11-19T00:00:00+00:00",
        "chunk_count": 1,
    }]


def test_acl_update_is_admin_only_validated_and_tenant_scoped(api):
    client, store = api
    document_id = store.upsert_document("tenant-a", "own.txt", ["staff"], "user-admin", [])
    foreign_id = store.upsert_document("tenant-b", "foreign.txt", ["staff"], "user-other", [])

    url = f"/v1/documents/{document_id}/acl"
    assert client.patch(url, headers={"Authorization": "Bearer staff"},
                        json={"acl_groups": ["finance"]}).status_code == 403
    assert client.patch(url, headers={"Authorization": "Bearer admin"},
                        json={"acl_groups": ["  ", "staff"]}).status_code == 422
    assert client.patch(url, headers={"Authorization": "Bearer admin"},
                        json={"acl_groups": ["staff", "staff"]}).status_code == 422

    response = client.patch(url, headers={"Authorization": "Bearer admin"},
                            json={"acl_groups": [" finance "]})
    assert response.status_code == 200
    assert response.json() == {"updated": True}
    assert store.documents[("tenant-a", "own.txt")]["groups"] == ["finance"]
    query = client.post(
        "/v1/query",
        headers={"Authorization": "Bearer staff"},
        json={"question": "Internal guide"},
    )
    assert query.status_code == 200
    assert query.json()["found"] is False

    response = client.patch(
        f"/v1/documents/{foreign_id}/acl",
        headers={"Authorization": "Bearer admin"},
        json={"acl_groups": ["all"]},
    )
    assert response.status_code == 200
    assert response.json() == {"updated": False}
    assert store.documents[("tenant-b", "foreign.txt")]["groups"] == ["staff"]


def test_document_delete_is_tenant_scoped_and_admin_only(api):
    client, store = api
    document_id = store.upsert_document("tenant-b", "foreign.txt", ["staff"], "user-other", [])
    response = client.delete(
        f"/v1/documents/{document_id}",
        headers={"Authorization": "Bearer admin"},
    )
    assert response.status_code == 200
    assert response.json()["deleted"] is False
    assert ("tenant-b", "foreign.txt") in store.documents


def test_supabase_search_requires_tenant_and_passes_groups_to_filtered_rpc():
    store = SupabaseVectorStore("https://example.supabase.co", "service-key", FakeEmbedder())
    calls = []

    class Response:
        content = b"[]"

        def raise_for_status(self):
            pass

        def json(self):
            return [{
                "chunk_id": "chunk-1",
                "document_name": "handbook.txt",
                "location": "§2",
                "content": "Employees receive 20 days of leave.",
                "acl_groups": ["staff"],
                "score": 0.04,
                "lexical_score": 0.2,
                "semantic_score": 0.8,
            }]

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return Response()

    store.session.request = request
    with pytest.raises(ValueError, match="tenant_id"):
        store.search("leave", user_groups=["staff"])
    hits = store.search("leave", user_groups=["staff"], tenant_id="tenant-a")
    assert hits[0].chunk.doc == "handbook.txt"
    assert calls[0][2]["json"]["p_tenant_id"] == "tenant-a"
    assert calls[0][2]["json"]["p_user_groups"] == ["staff"]


def test_supabase_document_admin_rpcs_are_tenant_scoped():
    store = SupabaseVectorStore("https://example.supabase.co", "service-key", FakeEmbedder())
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        class Response:
            content = b"true" if "update_document_acl" in url else b"[{}]"

            def raise_for_status(self):
                pass

            def json(self):
                if "update_document_acl" in url:
                    return True
                return [{
                    "document_id": "doc-id",
                    "name": "guide.pdf",
                    "acl_groups": ["staff"],
                    "uploaded_by": "user-id",
                    "created_at": "2026-11-19T00:00:00+00:00",
                    "chunk_count": 4,
                }]

        return Response()

    store.session.request = request
    documents = store.list_documents("tenant-a")
    assert documents[0]["name"] == "guide.pdf"
    assert calls[0][0:2] == ("POST", "https://example.supabase.co/rest/v1/rpc/docqa_list_documents")
    assert calls[0][2]["json"] == {"p_tenant_id": "tenant-a"}

    assert store.update_document_acl("tenant-a", "doc-id", ["staff", "finance"])
    assert calls[1][0:2] == (
        "POST", "https://example.supabase.co/rest/v1/rpc/docqa_update_document_acl"
    )
    assert calls[1][2]["json"] == {
        "p_tenant_id": "tenant-a",
        "p_document_id": "doc-id",
        "p_acl_groups": ["staff", "finance"],
    }


def test_supabase_organization_registry_rpcs():
    store = SupabaseVectorStore("https://example.supabase.co", "service-key", FakeEmbedder())
    tenant_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    organization = {
        "microsoft_tenant_id": tenant_id,
        "name": "Acme",
        "initial_admin_emails": ["owner@acme.example"],
        "created_at": "2026-11-20T00:00:00Z",
    }
    calls = []

    class Response:
        @property
        def content(self):
            return b"true" if "remove_registered_organization" in calls[-1][1] else b"[{}]"

        def raise_for_status(self):
            pass

        def json(self):
            if "remove_registered_organization" in calls[-1][1]:
                return True
            return [organization]

    store.session.request = lambda method, url, **kwargs: (
        calls.append((method, url, kwargs)) or Response()
    )

    assert store.get_registered_organization(tenant_id) == organization
    assert calls[-1][1].endswith("/rpc/docqa_get_registered_organization")
    assert calls[-1][2]["json"] == {"p_microsoft_tenant_id": tenant_id}
    assert store.list_registered_organizations() == [organization]
    assert calls[-1][1].endswith("/rpc/docqa_list_registered_organizations")
    assert store.set_registered_organization(
        tenant_id, "Acme", ["owner@acme.example"]
    ) == organization
    assert calls[-1][2]["json"] == {
        "p_microsoft_tenant_id": tenant_id,
        "p_name": "Acme",
        "p_initial_admin_emails": ["owner@acme.example"],
    }
    assert store.remove_registered_organization(tenant_id) is True
    assert calls[-1][2]["json"] == {"p_microsoft_tenant_id": tenant_id}


def test_supabase_platform_admin_registry_rpcs_and_error_translation():
    store = SupabaseVectorStore("https://example.supabase.co", "service-key", FakeEmbedder())
    actor_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    target_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    admin = {
        "user_id": target_id,
        "email": "operator@example.com",
        "granted_by": actor_id,
        "created_at": "2026-11-20T00:00:00Z",
    }
    audit = [{
        "id": 1,
        "actor_user_id": actor_id,
        "actor_email": "root@example.com",
        "target_user_id": target_id,
        "target_email": "operator@example.com",
        "action": "grant",
        "created_at": "2026-11-20T00:00:00Z",
    }]
    calls = []

    class Success:
        def __init__(self, value):
            self.value = value
            self.content = b"true" if isinstance(value, bool) else b"[{}]"

        def raise_for_status(self):
            pass

        def json(self):
            return self.value

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if "is_platform_admin" in url:
            return Success(True)
        if "list_platform_admin_audit" in url:
            return Success(audit)
        if "revoke_platform_admin" in url:
            return Success(True)
        return Success([admin])

    store.session.request = request
    assert store.is_platform_admin(actor_id)
    assert calls[-1][2]["json"] == {"p_user_id": actor_id}
    assert store.list_platform_admins() == [admin]
    assert store.list_platform_admin_audit() == audit
    assert calls[-1][2]["json"] == {"p_limit": 100}
    assert store.grant_platform_admin(actor_id, "operator@example.com") == admin
    assert calls[-1][2]["json"] == {
        "p_actor_user_id": actor_id,
        "p_target_email": "operator@example.com",
    }
    assert store.revoke_platform_admin(actor_id, target_id)
    assert calls[-1][2]["json"] == {
        "p_actor_user_id": actor_id,
        "p_target_user_id": target_id,
    }

    class ErrorResponse:
        status_code = 400
        content = b'{"message":"cannot remove the last platform administrator"}'
        text = content.decode()

        def raise_for_status(self):
            error = requests.HTTPError("last platform administrator")
            error.response = self
            raise error

    store.session.request = lambda *args, **kwargs: ErrorResponse()
    with pytest.raises(LastPlatformAdminError):
        store.revoke_platform_admin(actor_id, actor_id)


def test_supabase_graph_organization_request_rpcs():
    store = SupabaseVectorStore("https://example.supabase.co", "service-key", FakeEmbedder())
    actor_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    tenant_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    request_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    group_mapping = {
        "group_id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        "display_name": "Finance Members",
        "workspace_group_name": "Finance",
    }
    request_row = {
        "request_id": request_id,
        "microsoft_tenant_id": tenant_id,
        "name": "Acme",
        "requester_email": "owner@acme.example",
        "status": "pending",
        "requested_at": "2026-11-20T00:00:00Z",
        "verified_domains": ["acme.example"],
        "selected_groups": [group_mapping],
    }
    reviewed_row = {**request_row, "status": "approved"}
    calls = []

    class Success:
        content = b"[{}]"

        def __init__(self, value):
            self.value = value

        def raise_for_status(self):
            pass

        def json(self):
            return self.value

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if "get_organization_request_status" in url:
            return Success([{"status": "pending", "name": "Acme"}])
        if "create_organization_request" in url:
            return Success([request_row])
        if "list_organization_requests" in url:
            return Success([request_row])
        if "review_organization_request" in url:
            return Success([reviewed_row])
        if "get_registered_organization_groups" in url:
            return Success([{
                "graph_group_id": group_mapping["group_id"],
                "graph_group_name": group_mapping["display_name"],
                "workspace_group_name": group_mapping["workspace_group_name"],
            }])
        raise AssertionError(f"Unexpected RPC: {url}")

    store.session.request = request
    assert store.get_organization_request_status(tenant_id) == {
        "status": "pending",
        "name": "Acme",
    }
    assert store.create_organization_request(
        actor_id, tenant_id, "Acme", ["acme.example"], [group_mapping]
    ) == request_row
    assert calls[-1][2]["json"]["p_microsoft_tenant_id"] == tenant_id
    assert calls[-1][2]["json"]["p_selected_groups"] == [group_mapping]
    assert store.list_organization_requests(actor_id) == [request_row]
    assert calls[-1][2]["json"] == {"p_actor_user_id": actor_id}
    assert store.review_organization_request(
        request_id, actor_id, True, "Verified"
    ) == reviewed_row
    assert calls[-1][2]["json"] == {
        "p_request_id": request_id,
        "p_actor_user_id": actor_id,
        "p_approve": True,
        "p_review_note": "Verified",
    }
    assert store.get_registered_organization_groups(tenant_id)[0][
        "workspace_group_name"
    ] == "Finance"


def test_supabase_current_member_rpc_registers_and_translates_seat_limit():
    store = SupabaseVectorStore("https://example.supabase.co", "service-key", FakeEmbedder())
    calls = []

    class Success:
        content = b"2"

        def raise_for_status(self):
            pass

        def json(self):
            return 2

    store.session.request = lambda method, url, **kwargs: (
        calls.append((method, url, kwargs)) or Success()
    )
    count = store.register_current_member(
        "tenant-a", "user-2", "azure", "microsoft-tenant", "organization",
        "Acme", ["staff"], [], 15,
    )
    assert count == 2
    assert calls[0][1].endswith("/rpc/docqa_register_current_member")
    assert calls[0][2]["json"] == {
        "p_tenant_id": "tenant-a",
        "p_user_id": "user-2",
        "p_provider": "azure",
        "p_external_account_id": "microsoft-tenant",
        "p_workspace_type": "organization",
        "p_workspace_name": "Acme",
        "p_groups": ["staff"],
        "p_roles": [],
        "p_member_limit": 15,
    }

    class LimitReached:
        status_code = 400
        text = "trial member limit reached"
        content = b""

        def raise_for_status(self):
            error = requests.HTTPError("seat limit")
            error.response = self
            raise error

    store.session.request = lambda *args, **kwargs: LimitReached()
    with pytest.raises(TrialMemberLimitError) as error:
        store.register_current_member(
            "tenant-a", "user-16", "azure", "microsoft-tenant", "organization",
            "Acme", ["staff"], [], 15,
        )
    assert error.value.member_limit == 15


def test_supabase_identity_metadata_updates_use_database_rpc():
    store = SupabaseVectorStore("https://example.supabase.co", "service-key", FakeEmbedder())
    seen = {}

    def request(method, path, **kwargs):
        seen.update(method=method, path=path, **kwargs)

    store._request = request
    store.update_identity_app_metadata(
        "user-123",
        {"tenant_id": "tenant-a", "groups": ["staff"]},
    )

    assert seen == {
        "method": "POST",
        "path": "rpc/docqa_update_identity_app_metadata",
        "json": {
            "p_user_id": "user-123",
            "p_app_metadata": {"tenant_id": "tenant-a", "groups": ["staff"]},
        },
    }


def test_supabase_identity_metadata_lookup_uses_database_rpc():
    store = SupabaseVectorStore("https://example.supabase.co", "service-key", FakeEmbedder())
    calls = []

    class Success:
        content = b'{"tenant_id":"tenant-a","roles":["admin"]}'

        def raise_for_status(self):
            pass

        def json(self):
            return {"tenant_id": "tenant-a", "roles": ["admin"]}

    store.session.request = lambda method, url, **kwargs: (
        calls.append((method, url, kwargs)) or Success()
    )

    assert store.get_identity_app_metadata("user-123") == {
        "tenant_id": "tenant-a",
        "roles": ["admin"],
    }
    assert calls[0][1].endswith("/rpc/docqa_get_identity_app_metadata")
    assert calls[0][2]["json"] == {"p_user_id": "user-123"}


def test_supabase_write_audit_validates_payload():
    store = SupabaseVectorStore("https://example.supabase.co", "service-key", FakeEmbedder())
    calls = []

    class Success:
        content = b""

        def raise_for_status(self):
            pass

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return Success()

    store.session.request = request
    store.write_audit({
        "tenant_id": "tenant-a",
        "user_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "action": "query",
        "documents_retrieved": ["policy.txt"],
        "documents_cited": ["policy.txt"],
        "outcome": "ok",
    })
    assert len(calls) == 1
    assert "/docqa_audit_events" in calls[0][1]

    with pytest.raises(SupabaseError, match="unsupported fields"):
        store.write_audit({
            "tenant_id": "tenant-a",
            "user_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "action": "query",
            "unexpected": "value",
        })
    with pytest.raises(SupabaseError, match="string list"):
        store.write_audit({
            "tenant_id": "tenant-a",
            "user_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "action": "query",
            "documents_retrieved": [1],
        })


def test_supabase_workspace_lifecycle_rpcs():
    store = SupabaseVectorStore("https://example.supabase.co", "service-key", FakeEmbedder())
    actor_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    request_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    export = {"workspace": {"tenant_id": "tenant-a"}, "documents": []}
    created = {
        "request_id": request_id,
        "tenant_id": "tenant-a",
        "status": "pending",
    }
    request_row = {
        **created,
        "workspace_name": "Acme",
        "workspace_type": "organization",
        "requester_email": "owner@acme.example",
    }
    reviewed = {**created, "status": "approved", "reviewed_by": actor_id}
    calls = []

    class Success:
        def __init__(self, value):
            self.value = value
            self.content = (
                b"true" if isinstance(value, bool)
                else b"[]" if isinstance(value, list)
                else b"{}"
            )

        def raise_for_status(self):
            pass

        def json(self):
            return self.value

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if "docqa_export_workspace" in url:
            return Success(export)
        if "docqa_create_workspace_offboarding_request" in url:
            return Success(created)
        if "docqa_list_workspace_offboarding_requests" in url:
            return Success([request_row])
        if "docqa_review_workspace_offboarding_request" in url:
            return Success(reviewed)
        if "docqa_is_workspace_closed" in url:
            return Success(True)
        raise AssertionError(f"Unexpected RPC: {url}")

    store.session.request = request
    assert store.export_workspace("tenant-a") == export
    assert calls[-1][2]["json"] == {"p_tenant_id": "tenant-a"}
    assert store.create_workspace_offboarding_request("tenant-a", actor_id) == created
    assert calls[-1][2]["json"] == {
        "p_tenant_id": "tenant-a",
        "p_requester_user_id": actor_id,
    }
    assert store.list_workspace_offboarding_requests(actor_id) == [request_row]
    assert calls[-1][2]["json"] == {"p_actor_user_id": actor_id}
    assert store.review_workspace_offboarding_request(
        request_id, actor_id, True, "Approved",
    ) == reviewed
    assert calls[-1][2]["json"] == {
        "p_request_id": request_id,
        "p_actor_user_id": actor_id,
        "p_approve": True,
        "p_review_note": "Approved",
    }
    assert store.is_workspace_closed("tenant-a") is True
    assert calls[-1][2]["json"] == {"p_tenant_id": "tenant-a"}

    class Conflict:
        status_code = 400
        text = "offboarding request is already pending"
        content = text.encode()

        def raise_for_status(self):
            error = requests.HTTPError("request conflict")
            error.response = self
            raise error

    store.session.request = lambda *args, **kwargs: Conflict()
    with pytest.raises(OffboardingRequestConflictError):
        store.create_workspace_offboarding_request("tenant-a", actor_id)


def test_supabase_auth_trusts_app_metadata_not_user_metadata(monkeypatch):
    import docqa.auth as auth_module

    class Response:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "id": "user-1",
                "app_metadata": {
                    "tenant_id": "tenant-a",
                    "groups": ["staff", "all"],
                    "roles": [],
                },
                "user_metadata": {
                    "tenant_id": "tenant-b",
                    "groups": ["admin"],
                    "roles": ["admin"],
                },
            }

    seen = {}

    def get(url, headers, timeout):
        seen.update(url=url, headers=headers)
        return Response()

    monkeypatch.setattr(auth_module.requests, "get", get)
    verifier = SupabaseAuthVerifier("https://example.supabase.co", "anon-key")
    principal = verifier.authenticate("token")
    assert principal == Principal("user-1", "tenant-a", ("staff",), False)
    assert seen["headers"]["apikey"] == "anon-key"


def test_supabase_auth_reads_provider_and_verified_microsoft_tenant(monkeypatch):
    import docqa.auth as auth_module

    class Response:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "id": "user-azure",
                "email": "user@acme.example",
                "app_metadata": {"provider": "azure"},
                "identities": [{
                    "provider": "azure",
                    "identity_id": "fallback-id",
                    "identity_data": {
                        "tid": "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA",
                        "oid": "BBBBBBBB-BBBB-4BBB-8BBB-BBBBBBBBBBBB",
                    },
                }],
            }

    monkeypatch.setattr(auth_module.requests, "get", lambda *args, **kwargs: Response())
    verifier = SupabaseAuthVerifier("https://example.supabase.co", "anon-key")
    user = verifier.authenticate_user("token")
    assert user.provider == "azure"
    assert user.account_type == "organization"
    assert user.microsoft_tenant_id == "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    assert user.microsoft_user_id == "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def test_tenant_profile_is_loaded_and_applied_per_tenant(tmp_path, monkeypatch):
    (tmp_path / "acme.json").write_text('{"name": "Acme", "k": 2, "not_found_text": "Nothing."}')
    seen = {}

    class Spy(SimpleAnswer):
        def generate(self, question, hits, messages):
            seen["hits"] = len(hits)
            seen["system"] = messages[0]["content"]
            return "The retention period is 7 years [1]."

    store = FakeStore()
    auth = FakeAuth()
    store.identity_metadata = auth.identity_metadata
    for i in range(4):
        store.upsert_document("tenant-a", f"d{i}.txt", ["staff"], "u", [
            {"location": "§1", "content": f"retention {i}", "embedding": [1.0] * 384}])
    app = create_app(
        settings=Settings(
            supabase_url="https://tenant-a.supabase.co", supabase_anon_key="a",
            supabase_service_role_key="s", profiles_dir=str(tmp_path),
            tenant_branding={"acme.example.com": TenantBranding(tenant_id="tenant-a", profile="acme")},
        ),
        auth_verifier=auth, store=store, embedder=FakeEmbedder(), backend=Spy(),
    )
    client = TestClient(app, base_url="https://acme.example.com")
    r = client.post("/v1/query", headers={"Authorization": "Bearer staff"},
                    json={"question": "retention?"})
    assert r.status_code == 200
    assert "Strict-Transport-Security" in r.headers
    assert seen["hits"] <= 2  # profile k applied, not API default


def test_profile_name_cannot_escape_profiles_dir(tmp_path):
    with pytest.raises(ValueError):
        TenantBranding(tenant_id="t", profile="../secret")


def test_missing_profile_fails_at_startup(tmp_path):
    with pytest.raises(ValueError):
        create_app(
            settings=Settings(supabase_url="https://x.supabase.co", supabase_anon_key="a",
                              supabase_service_role_key="s", profiles_dir=str(tmp_path),
                              tenant_branding={"h.example.com": TenantBranding(tenant_id="t", profile="nope")}),
            auth_verifier=FakeAuth(), store=FakeStore(), embedder=FakeEmbedder(),
            backend=SimpleAnswer(),
        )


def test_query_rate_limit_returns_429_and_llm_outage_returns_503():
    from docqa.llm import LLMUnavailableError

    class Down(SimpleAnswer):
        def generate(self, *a):
            raise LLMUnavailableError("all failed")

    auth = FakeAuth()
    store = FakeStore()
    store.identity_metadata = auth.identity_metadata
    app = create_app(
        settings=Settings(supabase_url="https://x.supabase.co", supabase_anon_key="a",
                          supabase_service_role_key="s", query_rate_per_minute=2),
        auth_verifier=auth, store=store, embedder=FakeEmbedder(), backend=Down(),
    )
    client = TestClient(app, raise_server_exceptions=False)
    h = {"Authorization": "Bearer staff"}
    store_doc = app.state.store
    store_doc.upsert_document("tenant-a", "p.txt", ["staff"], "u", [
        {"location": "§1", "content": "text", "embedding": [1.0] * 384}])
    assert client.post("/v1/query", headers=h, json={"question": "q1"}).status_code == 503
    assert store_doc.audit[-1]["action"] == "query_error"
    assert store_doc.audit[-1]["outcome"] == "error"
    assert client.post("/v1/query", headers=h, json={"question": "q2"}).status_code == 503
    limited = client.post("/v1/query", headers=h, json={"question": "q3"})
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "60"
