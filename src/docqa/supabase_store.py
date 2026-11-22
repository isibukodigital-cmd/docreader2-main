"""Supabase Postgres/pgvector persistence and permission-filtered retrieval."""
from __future__ import annotations

from typing import Any

import numpy as np
import requests

from .index import Hit
from .ingest import Chunk


class DocumentStoreError(RuntimeError):
    """A database operation failed; callers must not treat it as an empty result."""


class SupabaseError(DocumentStoreError):
    """A Supabase operation failed."""


class TrialMemberLimitError(SupabaseError):
    """The verified organization has reached its trial-seat limit."""

    def __init__(self, message: str, member_limit: int | None = None):
        super().__init__(message)
        self.member_limit = member_limit


class PlatformAdminTargetNotFoundError(SupabaseError):
    """The requested platform administrator has no confirmed Supabase email."""


class LastPlatformAdminError(SupabaseError):
    """The last active platform administrator cannot be revoked."""


class OrganizationRequestConflictError(SupabaseError):
    """An organization request is no longer eligible for the requested operation."""


class OffboardingRequestConflictError(SupabaseError):
    """A workspace offboarding request is no longer eligible for the requested operation."""


class UnconfirmedOrganizationRequesterError(SupabaseError):
    """Organization requesters must have a confirmed Supabase Auth email."""


class DocumentLimitError(SupabaseError):
    """The workspace already holds the maximum number of trial documents."""


class SupabaseVectorStore:
    def __init__(self, url: str, service_role_key: str, embedder, timeout: float = 20):
        if not url or not service_role_key:
            raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")
        self.url = url.rstrip("/")
        self.embedder = embedder
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
            "Content-Type": "application/json",
        })

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self.session.request(
                method, f"{self.url}/rest/v1/{path}", timeout=self.timeout, **kwargs
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            response = exc.response
            if (
                path == "rpc/docqa_ingest_document"
                and response is not None
                and response.status_code == 400
                and "trial document limit reached" in response.text.lower()
            ):
                raise DocumentLimitError("Trial document limit reached") from exc
            if (
                path in {
                    "rpc/docqa_provision_trial_workspace",
                    "rpc/docqa_register_current_member",
                }
                and response is not None
                and response.status_code == 400
                and "trial member limit reached" in response.text.lower()
            ):
                payload = kwargs.get("json", {})
                member_limit = payload.get("p_member_limit") if isinstance(payload, dict) else None
                raise TrialMemberLimitError(
                    "Trial workspace member limit reached",
                    member_limit if isinstance(member_limit, int) else None,
                ) from exc
            if (
                path == "rpc/docqa_grant_platform_admin"
                and response is not None
                and response.status_code == 400
                and "confirmed platform administrator target not found" in response.text.lower()
            ):
                raise PlatformAdminTargetNotFoundError(
                    "No confirmed Supabase user was found for that email"
                ) from exc
            if (
                path == "rpc/docqa_revoke_platform_admin"
                and response is not None
                and response.status_code == 400
                and "cannot remove the last platform administrator" in response.text.lower()
            ):
                raise LastPlatformAdminError(
                    "At least one platform administrator must remain"
                ) from exc
            if (
                path in {
                    "rpc/docqa_create_organization_request",
                    "rpc/docqa_review_organization_request",
                }
                and response is not None
                and response.status_code == 400
                and any(
                    message in response.text.lower()
                    for message in (
                        "organization already registered",
                        "organization request is not pending",
                    )
                )
            ):
                raise OrganizationRequestConflictError(
                    "The organization request has changed. Refresh and try again."
                ) from exc
            if (
                path in {
                    "rpc/docqa_create_workspace_offboarding_request",
                    "rpc/docqa_review_workspace_offboarding_request",
                }
                and response is not None
                and response.status_code == 400
                and any(
                    message in response.text.lower()
                    for message in (
                        "offboarding request is already pending",
                        "offboarding request is no longer pending",
                        "workspace has already been closed",
                        "workspace has been closed",
                        "workspace to offboard was not found",
                    )
                )
            ):
                raise OffboardingRequestConflictError(
                    "The workspace offboarding request has changed. Refresh and try again."
                ) from exc
            if (
                path == "rpc/docqa_create_organization_request"
                and response is not None
                and response.status_code == 400
                and "confirmed organization requester not found" in response.text.lower()
            ):
                raise UnconfirmedOrganizationRequesterError(
                    "Confirm the email address on your Supabase Auth account before requesting organization access."
                ) from exc
            raise SupabaseError(f"Supabase request failed ({method} {path})") from exc
        if not response.content:
            return None
        try:
            return response.json()
        except requests.JSONDecodeError as exc:
            raise SupabaseError(f"Supabase returned invalid JSON ({method} {path})") from exc

    def search(self, query: str, k: int = 4, user_groups: list[str] | None = None,
               tenant_id: str | None = None, pool: int = 30) -> list[Hit]:
        if not tenant_id:
            raise ValueError("tenant_id is required for Supabase search")
        if k < 1 or pool < 1:
            raise ValueError("k and pool must be positive")
        query_vector = np.asarray(self.embedder.encode_query(query), dtype=np.float32)
        if query_vector.shape != (384,):
            raise ValueError(f"Supabase index expects 384-dimensional embeddings, got {query_vector.shape}")
        rows = self._request("POST", "rpc/docqa_search_chunks", json={
            "p_tenant_id": tenant_id,
            "p_user_groups": list(user_groups or []),
            "p_query": query,
            "p_query_embedding": query_vector.tolist(),
            "p_match_count": k,
            "p_candidate_count": max(pool, k),
            "p_rrf_k": 60,
        })
        if not isinstance(rows, list):
            raise SupabaseError("Supabase search returned an unexpected response")
        hits: list[Hit] = []
        for row in rows:
            chunk = Chunk(
                chunk_id=str(row["chunk_id"]),
                doc=str(row["document_name"]),
                loc=str(row["location"]),
                text=str(row["content"]),
                acl=list(row["acl_groups"]),
            )
            hits.append(Hit(
                chunk=chunk,
                score=float(row["score"]),
                bm25=float(row["lexical_score"]),
                dense=float(row["semantic_score"]),
            ))
        return hits

    def upsert_document(self, tenant_id: str, name: str, acl_groups: list[str],
                        uploaded_by: str, chunks: list[dict[str, Any]],
                        document_limit: int | None = None) -> str:
        if not tenant_id:
            raise ValueError("tenant_id is required for document ingestion")
        result = self._request("POST", "rpc/docqa_ingest_document", json={
            "p_tenant_id": tenant_id,
            "p_name": name,
            "p_acl_groups": acl_groups,
            "p_uploaded_by": uploaded_by,
            "p_chunks": chunks,
            "p_document_limit": document_limit,
        })
        if not isinstance(result, str):
            raise SupabaseError("Supabase ingestion returned no document ID")
        return result

    def delete_document(self, tenant_id: str, document_id: str) -> bool:
        result = self._request("POST", "rpc/docqa_delete_document", json={
            "p_tenant_id": tenant_id,
            "p_document_id": document_id,
        })
        if not isinstance(result, bool):
            raise SupabaseError("Supabase deletion returned an unexpected response")
        return result

    def list_documents(self, tenant_id: str) -> list[dict[str, Any]]:
        if not tenant_id:
            raise ValueError("tenant_id is required for document listing")
        rows = self._request("POST", "rpc/docqa_list_documents", json={
            "p_tenant_id": tenant_id,
        })
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise SupabaseError("Supabase document listing returned an unexpected response")
        return rows

    def update_document_acl(
        self, tenant_id: str, document_id: str, acl_groups: list[str]
    ) -> bool:
        if not tenant_id:
            raise ValueError("tenant_id is required for document ACL updates")
        result = self._request("POST", "rpc/docqa_update_document_acl", json={
            "p_tenant_id": tenant_id,
            "p_document_id": document_id,
            "p_acl_groups": acl_groups,
        })
        if not isinstance(result, bool):
            raise SupabaseError("Supabase ACL update returned an unexpected response")
        return result

    def provision_trial_workspace(
        self,
        user_id: str,
        provider: str,
        external_account_id: str,
        workspace_type: str,
        name: str,
        member_limit: int,
    ) -> dict[str, Any]:
        result = self._request("POST", "rpc/docqa_provision_trial_workspace", json={
            "p_user_id": user_id,
            "p_provider": provider,
            "p_external_account_id": external_account_id,
            "p_workspace_type": workspace_type,
            "p_name": name,
            "p_member_limit": member_limit,
        })
        if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], dict):
            raise SupabaseError("Supabase workspace provisioning returned an unexpected response")
        return result[0]

    def get_registered_organization(self, microsoft_tenant_id: str) -> dict[str, Any] | None:
        rows = self._request("POST", "rpc/docqa_get_registered_organization", json={
            "p_microsoft_tenant_id": microsoft_tenant_id,
        })
        if rows == []:
            return None
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise SupabaseError("Supabase organization lookup returned an unexpected response")
        return rows[0]

    def list_registered_organizations(self) -> list[dict[str, Any]]:
        rows = self._request("POST", "rpc/docqa_list_registered_organizations", json={})
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise SupabaseError("Supabase organization listing returned an unexpected response")
        return rows

    def set_registered_organization(
        self, microsoft_tenant_id: str, name: str, initial_admin_emails: list[str]
    ) -> dict[str, Any]:
        result = self._request("POST", "rpc/docqa_set_registered_organization", json={
            "p_microsoft_tenant_id": microsoft_tenant_id,
            "p_name": name,
            "p_initial_admin_emails": initial_admin_emails,
        })
        if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], dict):
            raise SupabaseError("Supabase organization registration returned an unexpected response")
        return result[0]

    def get_organization_request_status(
        self, microsoft_tenant_id: str
    ) -> dict[str, Any] | None:
        rows = self._request(
            "POST",
            "rpc/docqa_get_organization_request_status",
            json={"p_microsoft_tenant_id": microsoft_tenant_id},
        )
        if rows == []:
            return None
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise SupabaseError("Supabase organization request lookup returned an unexpected response")
        return rows[0]

    def create_organization_request(
        self,
        requested_by: str,
        microsoft_tenant_id: str,
        name: str,
        verified_domains: list[str],
        selected_groups: list[dict[str, str]],
    ) -> dict[str, Any]:
        from uuid import uuid4

        rows = self._request(
            "POST",
            "rpc/docqa_create_organization_request",
            json={
                "p_request_id": str(uuid4()),
                "p_microsoft_tenant_id": microsoft_tenant_id,
                "p_name": name,
                "p_verified_domains": verified_domains,
                "p_requested_by": requested_by,
                "p_selected_groups": selected_groups,
            },
        )
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise SupabaseError("Supabase organization request submission returned an unexpected response")
        return rows[0]

    def list_organization_requests(self, actor_user_id: str) -> list[dict[str, Any]]:
        rows = self._request(
            "POST",
            "rpc/docqa_list_organization_requests",
            json={"p_actor_user_id": actor_user_id},
        )
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise SupabaseError("Supabase organization request listing returned an unexpected response")
        return rows

    def review_organization_request(
        self,
        request_id: str,
        actor_user_id: str,
        approve: bool,
        review_note: str = "",
    ) -> dict[str, Any]:
        rows = self._request(
            "POST",
            "rpc/docqa_review_organization_request",
            json={
                "p_request_id": request_id,
                "p_actor_user_id": actor_user_id,
                "p_approve": approve,
                "p_review_note": review_note,
            },
        )
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise SupabaseError("Supabase organization request review returned an unexpected response")
        return rows[0]

    def get_registered_organization_groups(
        self, microsoft_tenant_id: str
    ) -> list[dict[str, Any]]:
        rows = self._request(
            "POST",
            "rpc/docqa_get_registered_organization_groups",
            json={"p_microsoft_tenant_id": microsoft_tenant_id},
        )
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise SupabaseError("Supabase organization group lookup returned an unexpected response")
        return rows

    def remove_registered_organization(self, microsoft_tenant_id: str) -> bool:
        result = self._request("POST", "rpc/docqa_remove_registered_organization", json={
            "p_microsoft_tenant_id": microsoft_tenant_id,
        })
        if not isinstance(result, bool):
            raise SupabaseError("Supabase organization removal returned an unexpected response")
        return result

    def is_platform_admin(self, user_id: str) -> bool:
        result = self._request("POST", "rpc/docqa_is_platform_admin", json={
            "p_user_id": user_id,
        })
        if not isinstance(result, bool):
            raise SupabaseError("Supabase platform-admin lookup returned an unexpected response")
        return result

    def list_platform_admins(self) -> list[dict[str, Any]]:
        rows = self._request("POST", "rpc/docqa_list_platform_admins", json={})
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise SupabaseError("Supabase platform-admin listing returned an unexpected response")
        return rows

    def list_platform_admin_audit(self, limit: int = 100) -> list[dict[str, Any]]:
        if limit < 1 or limit > 200:
            raise ValueError("platform-admin audit limit must be between 1 and 200")
        rows = self._request("POST", "rpc/docqa_list_platform_admin_audit", json={
            "p_limit": limit,
        })
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise SupabaseError("Supabase platform-admin audit returned an unexpected response")
        return rows

    def grant_platform_admin(self, actor_user_id: str, target_email: str) -> dict[str, Any]:
        rows = self._request("POST", "rpc/docqa_grant_platform_admin", json={
            "p_actor_user_id": actor_user_id,
            "p_target_email": target_email,
        })
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise SupabaseError("Supabase platform-admin grant returned an unexpected response")
        return rows[0]

    def revoke_platform_admin(self, actor_user_id: str, target_user_id: str) -> bool:
        result = self._request("POST", "rpc/docqa_revoke_platform_admin", json={
            "p_actor_user_id": actor_user_id,
            "p_target_user_id": target_user_id,
        })
        if not isinstance(result, bool):
            raise SupabaseError("Supabase platform-admin revocation returned an unexpected response")
        return result

    def export_workspace(self, tenant_id: str) -> dict[str, Any]:
        if not tenant_id:
            raise ValueError("tenant_id is required for workspace export")
        result = self._request("POST", "rpc/docqa_export_workspace", json={
            "p_tenant_id": tenant_id,
        })
        if not isinstance(result, dict) or not isinstance(result.get("workspace"), dict):
            raise SupabaseError("Supabase workspace export returned an unexpected response")
        return result

    def create_workspace_offboarding_request(
        self, tenant_id: str, requester_user_id: str
    ) -> dict[str, Any]:
        if not tenant_id or not requester_user_id:
            raise ValueError("tenant_id and requester_user_id are required")
        result = self._request(
            "POST",
            "rpc/docqa_create_workspace_offboarding_request",
            json={
                "p_tenant_id": tenant_id,
                "p_requester_user_id": requester_user_id,
            },
        )
        if not isinstance(result, dict) or not isinstance(result.get("request_id"), str):
            raise SupabaseError("Supabase offboarding request returned an unexpected response")
        return result

    def list_workspace_offboarding_requests(
        self, actor_user_id: str
    ) -> list[dict[str, Any]]:
        rows = self._request(
            "POST",
            "rpc/docqa_list_workspace_offboarding_requests",
            json={"p_actor_user_id": actor_user_id},
        )
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise SupabaseError("Supabase offboarding request listing returned an unexpected response")
        return rows

    def review_workspace_offboarding_request(
        self,
        request_id: str,
        actor_user_id: str,
        approve: bool,
        review_note: str = "",
    ) -> dict[str, Any]:
        result = self._request(
            "POST",
            "rpc/docqa_review_workspace_offboarding_request",
            json={
                "p_request_id": request_id,
                "p_actor_user_id": actor_user_id,
                "p_approve": approve,
                "p_review_note": review_note,
            },
        )
        if not isinstance(result, dict) or not isinstance(result.get("request_id"), str):
            raise SupabaseError("Supabase offboarding review returned an unexpected response")
        return result

    def is_workspace_closed(self, tenant_id: str) -> bool:
        if not tenant_id:
            raise ValueError("tenant_id is required for workspace status")
        result = self._request("POST", "rpc/docqa_is_workspace_closed", json={
            "p_tenant_id": tenant_id,
        })
        if not isinstance(result, bool):
            raise SupabaseError("Supabase workspace status returned an unexpected response")
        return result

    def trial_usage(self, tenant_id: str) -> dict[str, Any] | None:
        if not tenant_id:
            raise ValueError("tenant_id is required for trial usage")
        result = self._request("POST", "rpc/docqa_trial_usage", json={
            "p_tenant_id": tenant_id,
        })
        if result == []:
            return None
        if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], dict):
            raise SupabaseError("Supabase trial usage returned an unexpected response")
        return result[0]

    def workspace_directory(self, tenant_id: str) -> dict[str, Any]:
        if not tenant_id:
            raise ValueError("tenant_id is required for workspace directory")
        rows = self._request("POST", "rpc/docqa_workspace_directory", json={
            "p_tenant_id": tenant_id,
        })
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise SupabaseError("Supabase workspace directory returned an unexpected response")
        return rows[0]

    def create_workspace_group(self, tenant_id: str, name: str, created_by: str) -> str:
        if not tenant_id or not name or not created_by:
            raise ValueError("tenant_id, group name, and creator are required")
        result = self._request("POST", "rpc/docqa_create_workspace_group", json={
            "p_tenant_id": tenant_id,
            "p_name": name,
            "p_created_by": created_by,
        })
        if not isinstance(result, str):
            raise SupabaseError("Supabase group creation returned an unexpected response")
        return result

    def admin_dashboard(self, tenant_id: str, limit: int = 50) -> dict[str, Any]:
        if not tenant_id:
            raise ValueError("tenant_id is required for the admin dashboard")
        if limit < 1 or limit > 200:
            raise ValueError("dashboard limit must be between 1 and 200")
        rows = self._request("POST", "rpc/docqa_admin_dashboard", json={
            "p_tenant_id": tenant_id,
            "p_limit": limit,
        })
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise SupabaseError("Supabase admin dashboard returned an unexpected response")
        return rows[0]

    def register_current_member(
        self,
        tenant_id: str,
        user_id: str,
        provider: str,
        external_account_id: str,
        workspace_type: str,
        workspace_name: str,
        groups: list[str],
        roles: list[str],
        member_limit: int,
    ) -> int:
        result = self._request("POST", "rpc/docqa_register_current_member", json={
            "p_tenant_id": tenant_id,
            "p_user_id": user_id,
            "p_provider": provider,
            "p_external_account_id": external_account_id,
            "p_workspace_type": workspace_type,
            "p_workspace_name": workspace_name,
            "p_groups": groups,
            "p_roles": roles,
            "p_member_limit": member_limit,
        })
        if not isinstance(result, int):
            raise SupabaseError("Supabase member registration returned an unexpected response")
        return result

    def get_identity_app_metadata(self, user_id: str) -> dict[str, Any]:
        if not user_id:
            raise SupabaseError("Identity metadata lookup requires a user ID")
        result = self._request("POST", "rpc/docqa_get_identity_app_metadata", json={
            "p_user_id": user_id,
        })
        if not isinstance(result, dict):
            raise SupabaseError("Identity metadata lookup returned no user metadata")
        return result

    def ensure_identity(
        self,
        user_id: str,
        auth_provider: str,
        issuer: str,
        subject: str,
        email: str,
        email_verified: bool,
    ) -> None:
        result = self._request("POST", "rpc/docqa_ensure_identity", json={
            "p_user_id": user_id,
            "p_auth_provider": auth_provider,
            "p_issuer": issuer,
            "p_subject": subject,
            "p_email": email,
            "p_email_verified": email_verified,
        })
        if str(result) != user_id:
            raise SupabaseError("Identity registration returned an unexpected user ID")

    def update_identity_app_metadata(
        self, user_id: str, app_metadata: dict[str, Any]
    ) -> None:
        if not user_id or not isinstance(app_metadata, dict):
            raise SupabaseError("Identity metadata update requires a user ID and an object")
        self._request("POST", "rpc/docqa_update_identity_app_metadata", json={
            "p_user_id": user_id,
            "p_app_metadata": app_metadata,
        })

    def write_audit(self, record: dict[str, Any]) -> None:
        if not isinstance(record, dict):
            raise SupabaseError("Invalid audit record")
        tenant_id = record.get("tenant_id")
        user_id = record.get("user_id")
        action = record.get("action")
        if not isinstance(tenant_id, str) or not tenant_id:
            raise SupabaseError("Audit record requires tenant_id")
        if not isinstance(user_id, str) or not user_id:
            raise SupabaseError("Audit record requires user_id")
        if not isinstance(action, str) or not action:
            raise SupabaseError("Audit record requires action")

        allowed_keys = {
            "tenant_id", "user_id", "action", "question", "document_name",
            "documents_retrieved", "documents_cited", "outcome",
        }
        unexpected = set(record) - allowed_keys
        if unexpected:
            raise SupabaseError("Audit record contains unsupported fields")

        for key in ("documents_retrieved", "documents_cited"):
            value = record.get(key)
            if value is not None and (
                not isinstance(value, list)
                or not all(isinstance(item, str) for item in value)
            ):
                raise SupabaseError(f"Audit record field {key} must be a string list")

        self._request(
            "POST",
            "docqa_audit_events",
            json=record,
            headers={"Prefer": "return=minimal"},
        )
