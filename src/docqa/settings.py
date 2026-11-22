"""Application settings and environment parsing."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from .api_schemas import TenantBranding
from .deployment import DeploymentConfig


def normalized_hostname(hostname: str) -> str:
    return hostname.rstrip(".").lower()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


@dataclass(frozen=True)
class Settings:
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    database_url: str = ""
    cognito_region: str = ""
    cognito_user_pool_id: str = ""
    cognito_client_id: str = ""
    cognito_domain: str = ""
    microsoft_graph_client_id: str = ""
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    backend: str = "extractive"
    model: str | None = None
    tenant_branding: dict[str, TenantBranding] = field(default_factory=dict)
    branding_dir: str | None = None
    profiles_dir: str | None = None
    # How the audit log stores question text: full | hash | none
    audit_questions: str = "hash"
    query_rate_per_minute: int = 30  # per user; 0 disables
    upload_rate_per_minute: int = 10  # per user; 0 disables
    max_concurrent_ingests: int = 2  # embedding is CPU/RAM heavy
    deployment: DeploymentConfig = field(default_factory=DeploymentConfig)

    @classmethod
    def from_env(cls) -> "Settings":
        from dotenv import load_dotenv

        load_dotenv()
        branding_json = os.getenv("DOCQA_TENANT_BRANDING_JSON", "{}")
        try:
            raw_branding = json.loads(branding_json)
        except json.JSONDecodeError as exc:
            raise ValueError("DOCQA_TENANT_BRANDING_JSON must be valid JSON") from exc
        if not isinstance(raw_branding, dict):
            raise ValueError("DOCQA_TENANT_BRANDING_JSON must be an object keyed by hostname")
        tenant_branding = {}
        for hostname, brand in raw_branding.items():
            if not isinstance(hostname, str) or not isinstance(brand, dict):
                raise ValueError("Each tenant branding entry must map a hostname to a JSON object")
            tenant_branding[normalized_hostname(hostname)] = TenantBranding.model_validate(brand)
        audit_questions = os.getenv("DOCQA_AUDIT_QUESTIONS", "hash").lower()
        if audit_questions not in {"full", "hash", "none"}:
            raise ValueError("DOCQA_AUDIT_QUESTIONS must be full, hash, or none")
        return cls(
            supabase_url=os.getenv("SUPABASE_URL", ""),
            supabase_anon_key=os.getenv("SUPABASE_ANON_KEY", ""),
            supabase_service_role_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""),
            database_url=os.getenv("DOCQA_DATABASE_URL", ""),
            cognito_region=os.getenv("DOCQA_COGNITO_REGION", ""),
            cognito_user_pool_id=os.getenv("DOCQA_COGNITO_USER_POOL_ID", ""),
            cognito_client_id=os.getenv("DOCQA_COGNITO_CLIENT_ID", ""),
            cognito_domain=os.getenv("DOCQA_COGNITO_DOMAIN", ""),
            microsoft_graph_client_id=os.getenv("DOCQA_MICROSOFT_GRAPH_CLIENT_ID", ""),
            embedding_model=os.getenv("DOCQA_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"),
            backend=os.getenv("DOCQA_BACKEND", "extractive").lower(),
            model=os.getenv("DOCQA_MODEL"),
            tenant_branding=tenant_branding,
            branding_dir=os.getenv("DOCQA_BRANDING_DIR"),
            profiles_dir=os.getenv("DOCQA_PROFILES_DIR"),
            audit_questions=audit_questions,
            query_rate_per_minute=_env_int("DOCQA_QUERY_RATE_PER_MINUTE", 30),
            upload_rate_per_minute=_env_int("DOCQA_UPLOAD_RATE_PER_MINUTE", 10),
            max_concurrent_ingests=max(1, _env_int("DOCQA_MAX_CONCURRENT_INGESTS", 2)),
            deployment=DeploymentConfig.from_env(),
        )
