"""HTTP request and response schemas."""
from __future__ import annotations

import re
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_ACL_GROUPS = 50


class TenantBranding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1, max_length=200)
    name: str = Field(default="Doc Reader", min_length=1, max_length=80)
    tagline: str = Field(default="DOCUMENTS, CONNECTED", max_length=120)
    login_heading: str = Field(default="Find the answer in your documents.", max_length=120)
    login_subtitle: str = Field(
        default="Ask a question in plain language. Get a clear answer with sources you can open and verify.",
        max_length=240,
    )
    footer_text: str = Field(
        default="Answers may be imperfect. Verify important information in the cited source.",
        max_length=240,
    )
    support_url: str | None = Field(default=None, max_length=500)
    logo_url: str | None = Field(default=None, max_length=300)
    primary_color: str = Field(default="#245c4b", pattern=r"^#[0-9A-Fa-f]{6}$")
    accent_color: str = Field(default="#f5f6f2", pattern=r"^#[0-9A-Fa-f]{6}$")
    hide_attribution: bool = False
    # Name (no path, no extension) of a JSON file in DOCQA_PROFILES_DIR.
    profile: str | None = Field(default=None, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")

    @field_validator("logo_url")
    @classmethod
    def logo_must_be_local_branding_asset(cls, value: str | None) -> str | None:
        if value is not None and (
            not value.startswith("/branding/") or ".." in value or "\\" in value
        ):
            raise ValueError("logo_url must reference a local /branding/ asset")
        return value

    @field_validator("support_url")
    @classmethod
    def support_link_must_use_safe_scheme(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(("https://", "mailto:")):
            raise ValueError("support_url must use https:// or mailto:")
        return value


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=2000)
    k: int | None = Field(default=None, ge=1, le=10)  # None -> tenant profile default

    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be blank")
        return value


class CitationResponse(BaseModel):
    document: str
    location: str
    quote: str


class QueryResponse(BaseModel):
    answer: str
    found: bool
    reason: str
    citations: list[CitationResponse]


class IngestResponse(BaseModel):
    document_id: str
    name: str
    chunks: int


class DeleteResponse(BaseModel):
    deleted: bool


class AclUpdateResponse(BaseModel):
    updated: bool


class OnboardingOptionsResponse(BaseModel):
    onboarding_required: bool
    account_type: str | None
    provider: str
    organization_name: str | None = None
    organization_request_status: str | None = None
    organization_setup_available: bool = False
    organization_graph_required: bool = False
    is_platform_admin: bool = False


class OrganizationGroupSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group_id: UUID
    workspace_group_name: str = Field(min_length=1, max_length=100)

    @field_validator("workspace_group_name")
    @classmethod
    def validate_group_name(cls, value: str) -> str:
        normalized = value.strip()
        if (
            not normalized
            or "," in normalized
            or normalized.casefold() in {"all", "admin", "owner", "staff"}
        ):
            raise ValueError("Mapped group name is reserved or invalid")
        return normalized


class OrganizationRequestSubmit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    groups: list[OrganizationGroupSelection] = Field(min_length=1, max_length=100)

    @field_validator("groups")
    @classmethod
    def require_unique_group_mappings(
        cls, groups: list[OrganizationGroupSelection]
    ) -> list[OrganizationGroupSelection]:
        group_ids = [str(group.group_id).lower() for group in groups]
        names = [group.workspace_group_name.casefold() for group in groups]
        if len(set(group_ids)) != len(group_ids) or len(set(names)) != len(names):
            raise ValueError("Group mappings must be unique")
        return groups


class OrganizationReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approve: bool
    review_note: str = Field(default="", max_length=500)


class OrganizationGroupResponse(BaseModel):
    id: str
    display_name: str


class OrganizationGraphCatalogResponse(BaseModel):
    tenant_id: str
    organization_name: str
    verified_domains: list[str]
    groups: list[OrganizationGroupResponse]
    request_status: str | None = None


class OrganizationRequestResponse(BaseModel):
    request_id: str
    microsoft_tenant_id: str
    name: str
    requester_email: str
    status: str
    requested_at: str
    verified_domains: list[str] = Field(default_factory=list)
    selected_groups: list[dict] = Field(default_factory=list)


class OrganizationRegistrationResponse(BaseModel):
    microsoft_tenant_id: str
    name: str
    initial_admin_emails: list[str]
    created_at: str


class PlatformAdminGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
            raise ValueError("Enter a valid email address")
        return normalized


class OffboardingReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approve: bool
    review_note: str = Field(default="", max_length=500)


class PlatformAdminResponse(BaseModel):
    user_id: str
    email: str | None = None
    granted_by: str | None = None
    created_at: str


class PlatformAdminAuditResponse(BaseModel):
    id: int
    actor_user_id: str
    actor_email: str | None = None
    target_user_id: str
    target_email: str | None = None
    action: str
    created_at: str


class WorkspaceResponse(BaseModel):
    tenant_id: str
    name: str
    workspace_type: str
    member_count: int
    member_limit: int


class UsageResponse(BaseModel):
    workspace_name: str
    workspace_type: str
    member_count: int
    member_limit: int
    document_count: int
    document_limit: int
    member_count_tracked: bool
    upgrade_available: bool
    upgrade_message: str


class DashboardEventResponse(BaseModel):
    id: int
    user_id: str
    action: str
    document_name: str | None = None
    documents_retrieved: list[str] = Field(default_factory=list)
    documents_cited: list[str] = Field(default_factory=list)
    outcome: str | None = None
    created_at: str


class AdminDashboardResponse(BaseModel):
    member_count: int
    document_count: int
    query_count: int
    error_count: int
    document_activity_count: int
    recent_events: list[DashboardEventResponse]


class DocumentResponse(BaseModel):
    document_id: str
    name: str
    acl_groups: list[str]
    uploaded_by: str | None
    created_at: str
    chunk_count: int


class DocumentAclRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    acl_groups: list[str] = Field(min_length=1, max_length=MAX_ACL_GROUPS)

    @field_validator("acl_groups")
    @classmethod
    def validate_acl_groups(cls, groups: list[str]) -> list[str]:
        normalized = [group.strip() for group in groups]
        if (any(not group or len(group) > 100 for group in normalized)
                or len(set(normalized)) != len(normalized)):
            raise ValueError("ACL groups must be unique, non-empty, and at most 100 characters")
        return normalized


class WorkspaceMemberResponse(BaseModel):
    email: str | None = None
    groups: list[str]
    roles: list[str]


class WorkspaceDirectoryResponse(BaseModel):
    groups: list[str]
    members: list[WorkspaceMemberResponse]


class WorkspaceGroupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def validate_group_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or "," in normalized or normalized.lower() == "all":
            raise ValueError("Group name must be non-empty, exclude commas, and not be 'all'")
        return normalized


class WorkspaceGroupResponse(BaseModel):
    name: str


class WorkspaceSetupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_type: str = Field(pattern=r"^(personal|organization)$")
    workspace_name: str = Field(default="", max_length=80)

    @field_validator("workspace_name")
    @classmethod
    def normalize_workspace_name(cls, value: str) -> str:
        value = value.strip()
        if value and any(ord(character) < 32 for character in value):
            raise ValueError("workspace_name contains invalid characters")
        return value
