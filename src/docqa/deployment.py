"""Deployment configuration and replaceable service interfaces."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol

SUPPORTED_DEPLOYMENT_MODELS = ("shared-hosted", "dedicated-customer", "local-preview")
SUPPORTED_AUTH_PROVIDERS = ("supabase", "cognito")
SUPPORTED_DOCUMENT_STORES = ("supabase", "postgres")


@dataclass(frozen=True)
class DeploymentConfig:
    model: str = "shared-hosted"
    auth_provider: str = "supabase"
    document_store: str = "supabase"

    def __post_init__(self) -> None:
        if self.model not in SUPPORTED_DEPLOYMENT_MODELS:
            raise ValueError(
                f"DOCQA_DEPLOYMENT_MODEL must be one of {SUPPORTED_DEPLOYMENT_MODELS}"
            )
        if self.auth_provider not in SUPPORTED_AUTH_PROVIDERS:
            raise ValueError(
                f"DOCQA_AUTH_PROVIDER must be one of {SUPPORTED_AUTH_PROVIDERS}"
            )
        if self.document_store not in SUPPORTED_DOCUMENT_STORES:
            raise ValueError(
                f"DOCQA_DOCUMENT_STORE must be one of {SUPPORTED_DOCUMENT_STORES}"
            )

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "DeploymentConfig":
        values = env if env is not None else os.environ
        return cls(
            model=values.get("DOCQA_DEPLOYMENT_MODEL", "shared-hosted").strip().lower(),
            auth_provider=values.get("DOCQA_AUTH_PROVIDER", "supabase").strip().lower(),
            document_store=values.get("DOCQA_DOCUMENT_STORE", "supabase").strip().lower(),
        )


class AuthVerifier(Protocol):
    def authenticate_user(self, token: str) -> Any: ...

    def principal_from_user(self, user: Any) -> Any: ...


class DocumentStore(Protocol):
    def ensure_identity(
        self,
        user_id: str,
        auth_provider: str,
        issuer: str,
        subject: str,
        email: str,
        email_verified: bool,
    ) -> None: ...

    def search(self, query: str, k: int = 4, user_groups: list[str] | None = None,
               tenant_id: str | None = None, pool: int = 30) -> list[Any]: ...

    def upsert_document(self, tenant_id: str, name: str, acl_groups: list[str],
                        uploaded_by: str, chunks: list[dict[str, Any]],
                        document_limit: int | None = None) -> str: ...

    def delete_document(self, tenant_id: str, document_id: str) -> bool: ...

    def list_documents(self, tenant_id: str) -> list[dict[str, Any]]: ...

    def export_workspace(self, tenant_id: str) -> dict[str, Any]: ...

    def create_workspace_offboarding_request(
        self, tenant_id: str, requester_user_id: str
    ) -> dict[str, Any]: ...

    def list_workspace_offboarding_requests(
        self, actor_user_id: str
    ) -> list[dict[str, Any]]: ...

    def review_workspace_offboarding_request(
        self,
        request_id: str,
        actor_user_id: str,
        approve: bool,
        review_note: str = "",
    ) -> dict[str, Any]: ...

    def is_workspace_closed(self, tenant_id: str) -> bool: ...

    def update_document_acl(self, tenant_id: str, document_id: str,
                            acl_groups: list[str]) -> bool: ...

    def write_audit(self, record: dict[str, Any]) -> None: ...
