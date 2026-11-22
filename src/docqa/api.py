"""Controlled HTTP API backed by Supabase Auth and Postgres/pgvector."""
from __future__ import annotations

import hashlib
import logging
import tempfile
import threading
import time
import zipfile
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from docx.opc.exceptions import PackageNotFoundError
from pypdf.errors import PdfReadError

from .answer import (
    ExtractiveBackend,
    HFBackend,
    OllamaBackend,
    OllamaUnavailableError,
    answer,
)
from .auth import (
    AuthenticatedUser,
    AuthenticationError,
    CognitoAuthVerifier,
    IdentityProviderUnavailable,
    Principal,
    SupabaseAuthVerifier,
    graph_group_sync_is_fresh as _graph_group_sync_is_fresh,
)
from .api_dependencies import (
    MICROSOFT_ORG_ADMIN_ROLE_TEMPLATE_IDS,
    ORGANIZATION_MEMBER_LIMIT,
    PERSONAL_MEMBER_LIMIT,
    _admin,
    _admin_or_auditor,
    _authenticated_user,
    _onboarding_organization,
    _platform_admin,
    _principal,
    _verified_graph,
)
from .api_schemas import (
    MAX_ACL_GROUPS,
    AclUpdateResponse,
    AdminDashboardResponse,
    CitationResponse,
    DeleteResponse,
    DocumentAclRequest,
    DocumentResponse,
    IngestResponse,
    OffboardingReviewRequest,
    OnboardingOptionsResponse,
    OrganizationGraphCatalogResponse,
    OrganizationGroupResponse,
    OrganizationGroupSelection,
    OrganizationRegistrationResponse,
    OrganizationRequestResponse,
    OrganizationRequestSubmit,
    OrganizationReviewRequest,
    PlatformAdminAuditResponse,
    PlatformAdminGrantRequest,
    PlatformAdminResponse,
    QueryRequest,
    QueryResponse,
    TenantBranding,
    UsageResponse,
    WorkspaceDirectoryResponse,
    WorkspaceGroupRequest,
    WorkspaceGroupResponse,
    WorkspaceMemberResponse,
    WorkspaceResponse,
    WorkspaceSetupRequest,
)
from .index import SentenceTransformerEmbedder
from .ingest import (
    DOCXParseTimeoutError,
    DOCXParseWorkerError,
    PDFPageLimitError,
    PDFParseTimeoutError,
    SUPPORTED,
    ingest_folder,
)
from .limits import RateLimiter, TTLSet, check_docx_safe
from .llm import LLMUnavailableError
from .microsoft_graph import GraphAPIError
from .middleware import _UploadRequestBodyLimitMiddleware
from .postgres_store import PostgresVectorStore
from .profile import Profile, load_profile
from .supabase_store import (
    DocumentLimitError,
    DocumentStoreError,
    LastPlatformAdminError,
    OffboardingRequestConflictError,
    OrganizationRequestConflictError,
    PlatformAdminTargetNotFoundError,
    SupabaseVectorStore,
    TrialMemberLimitError,
    UnconfirmedOrganizationRequesterError,
)
from .settings import Settings, normalized_hostname as _normalized_hostname

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_UPLOAD_REQUEST_BYTES = MAX_UPLOAD_BYTES + 1024 * 1024
MAX_PDF_PAGES = 500
PDF_PARSE_TIMEOUT_SECONDS = 30
DOCX_PARSE_TIMEOUT_SECONDS = 30
MAX_INGEST_CHUNKS = 2000
TRIAL_DOCUMENT_LIMIT = 5
logger = logging.getLogger(__name__)


def _load_tenant_profiles(settings: Settings, branding: dict) -> dict[str, Profile]:
    """Map tenant_id -> Profile. Fails at startup (not per request) on any bad profile."""
    profiles: dict[str, Profile] = {}
    for brand in branding.values():
        if not brand.profile:
            continue
        if not settings.profiles_dir:
            raise ValueError("DOCQA_PROFILES_DIR is required when a tenant sets 'profile'")
        base = Path(settings.profiles_dir).resolve()
        path = (base / f"{brand.profile}.json").resolve()
        if base not in path.parents or not path.is_file():
            raise ValueError(f"Profile '{brand.profile}' not found in DOCQA_PROFILES_DIR")
        loaded = load_profile(path)
        profiles[brand.tenant_id] = replace(loaded, tenant_id=brand.tenant_id)
    return profiles


def _document_limit_detail() -> str:
    return (
        f"This trial workspace is limited to {TRIAL_DOCUMENT_LIMIT} documents. "
        "Contact the product administrator to discuss paid plans."
    )


def _audit_question(mode: str, question: str) -> str | None:
    if mode == "none":
        return None
    if mode == "hash":
        return "sha256:" + hashlib.sha256(question.encode("utf-8")).hexdigest()
    return question


def _write_audit(store, record: dict) -> None:
    try:
        store.write_audit(record)
    except DocumentStoreError:
        logger.exception(
            "Audit write failed (action=%s tenant=%s)",
            record.get("action"),
            record.get("tenant_id"),
        )


def _backend(settings: Settings):
    if settings.backend == "extractive":
        return ExtractiveBackend()
    if settings.backend == "ollama":
        return OllamaBackend(model=settings.model or "qwen2.5:3b")
    if settings.backend == "hf":
        return HFBackend(model_id=settings.model or "Qwen/Qwen2.5-3B-Instruct")
    if settings.backend == "llm":
        from .llm import build_llm_backend

        return build_llm_backend()
    raise ValueError("DOCQA_BACKEND must be extractive, ollama, hf, or llm")


def create_app(settings: Settings | None = None, auth_verifier=None, store=None,
               embedder=None, backend=None) -> FastAPI:
    config = settings or Settings.from_env()
    if config.backend not in {"extractive", "ollama", "hf", "llm"}:
        raise ValueError("DOCQA_BACKEND must be extractive, ollama, hf, or llm")

    def initialize_services(application: FastAPI) -> None:
        if application.state.auth_verifier is None:
            if config.deployment.auth_provider == "supabase":
                application.state.auth_verifier = SupabaseAuthVerifier(
                    config.supabase_url, config.supabase_anon_key
                )
            elif config.deployment.auth_provider == "cognito":
                application.state.auth_verifier = CognitoAuthVerifier(
                    config.cognito_region,
                    config.cognito_user_pool_id,
                    config.cognito_client_id,
                )
            else:
                raise ValueError("Unsupported authentication provider")
        if application.state.embedder is None:
            application.state.embedder = SentenceTransformerEmbedder(config.embedding_model)
        if application.state.store is None:
            if config.deployment.document_store == "supabase":
                application.state.store = SupabaseVectorStore(
                    config.supabase_url,
                    config.supabase_service_role_key,
                    application.state.embedder,
                )
            elif config.deployment.document_store == "postgres":
                application.state.store = PostgresVectorStore(
                    config.database_url, application.state.embedder
                )
            else:
                raise ValueError("Unsupported document store")
        if application.state.backend is None:
            application.state.backend = _backend(config)

    def probe_dependencies(application: FastAPI, refresh: bool = True) -> tuple[bool, int, int]:
        now = time.monotonic()
        with application.state.database_probe_lock:
            cache_age = now - application.state.database_probe_at
            if not refresh and cache_age < 60:
                return (
                    application.state.ready,
                    application.state.database_status,
                    application.state.database_latency_ms,
                )
            initialized = all((
                application.state.auth_verifier is not None,
                application.state.store is not None,
                application.state.embedder is not None,
                application.state.backend is not None,
            ))
            database_status = 0
            database_latency_ms = 0
            if initialized:
                started = time.perf_counter()
                try:
                    application.state.store.trial_usage("__healthcheck__")
                    query = getattr(application.state.embedder, "encode_query", None)
                    if callable(query):
                        vector = query("__healthcheck__")
                        if getattr(vector, "shape", None) is not None:
                            pass
                    database_status = 1
                except Exception:
                    database_status = 0
                database_latency_ms = int((time.perf_counter() - started) * 1000)
            application.state.database_status = database_status
            application.state.database_latency_ms = database_latency_ms
            application.state.database_probe_at = now
            application.state.ready = bool(initialized and database_status)
        return (
            application.state.ready,
            application.state.database_status,
            application.state.database_latency_ms,
        )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        try:
            initialize_services(application)
            probe_dependencies(application)
            yield
        finally:
            application.state.ready = False
            close = getattr(application.state.store, "close", None)
            if callable(close):
                close()

    docs_enabled = config.deployment.model == "local-preview"
    app = FastAPI(
        title="Private Document Q&A API",
        version="0.2.0",
        openapi_url="/openapi.json" if docs_enabled else None,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        lifespan=lifespan,
    )
    app.add_middleware(
        _UploadRequestBodyLimitMiddleware,
        max_body_bytes=MAX_UPLOAD_REQUEST_BYTES,
    )
    app.state.settings = config
    app.state.deployment = config.deployment
    app.state.tenant_branding = {
        _normalized_hostname(hostname): (
            brand if isinstance(brand, TenantBranding) else TenantBranding.model_validate(brand)
        )
        for hostname, brand in config.tenant_branding.items()
    }
    app.state.profiles = _load_tenant_profiles(config, app.state.tenant_branding)
    app.state.registered = TTLSet(ttl=60.0)
    app.state.query_limiter = RateLimiter(config.query_rate_per_minute)
    app.state.upload_limiter = RateLimiter(config.upload_rate_per_minute)
    app.state.ingest_slots = threading.BoundedSemaphore(config.max_concurrent_ingests)
    app.state.auth_verifier = auth_verifier
    app.state.store = store
    app.state.embedder = embedder
    app.state.backend = backend
    app.state.ready = False
    app.state.database_status = 0
    app.state.database_latency_ms = 0
    app.state.database_probe_at = 0.0
    app.state.database_probe_lock = threading.Lock()
    app.state.started_at = time.monotonic()
    app.state.app_metrics = {
        "lock": threading.Lock(),
        "requests": 0,
        "errors": 0,
        "total_latency_ms": 0,
        "max_latency_ms": 0,
    }

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        started = time.perf_counter()
        incoming_request_id = request.headers.get("x-request-id", "")
        request_id = incoming_request_id.strip()[:64] if incoming_request_id.strip() else str(uuid4())
        request_id = request_id or str(uuid4())
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        except Exception:
            latency_ms = int((time.perf_counter() - started) * 1000)
            metrics = app.state.app_metrics
            with metrics["lock"]:
                metrics["requests"] += 1
                metrics["errors"] += 1
                metrics["total_latency_ms"] += latency_ms
                metrics["max_latency_ms"] = max(metrics["max_latency_ms"], latency_ms)
            raise
        latency_ms = int((time.perf_counter() - started) * 1000)
        metrics = app.state.app_metrics
        with metrics["lock"]:
            metrics["requests"] += 1
            if response.status_code >= 500:
                metrics["errors"] += 1
            metrics["total_latency_ms"] += latency_ms
            metrics["max_latency_ms"] = max(metrics["max_latency_ms"], latency_ms)
        response.headers["X-Request-ID"] = request_id
        connect_sources = "'self'"
        supabase_origin = (
            urlsplit(config.supabase_url).scheme
            + "://"
            + urlsplit(config.supabase_url).netloc
        )
        if supabase_origin.startswith("https://"):
            connect_sources += f" {supabase_origin}"
        cognito_domain = urlsplit(config.cognito_domain)
        if cognito_domain.scheme == "https" and cognito_domain.netloc:
            connect_sources += f" {cognito_domain.scheme}://{cognito_domain.netloc}"
        if config.deployment.auth_provider == "cognito":
            connect_sources += " https://login.microsoftonline.com"
        if request.url.path in {"/docs", "/redoc", "/docs/oauth2-redirect"}:
            # FastAPI's generated docs use CDN assets and an inline initializer.
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "img-src 'self' data: https://fastapi.tiangolo.com; "
                f"connect-src {connect_sources}; frame-ancestors 'none'; base-uri 'self'; "
                "form-action 'self'; object-src 'none'"
            )
        else:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                f"connect-src {connect_sources}; frame-ancestors 'none'; base-uri 'self'; "
                "form-action 'self'; object-src 'none'"
            )
        if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.url.path.startswith("/v1/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Vary"] = "Authorization"
        return response

    static_dir = Path(__file__).parent / "static"
    branding_dir = Path(config.branding_dir) if config.branding_dir else Path(__file__).resolve().parents[2] / "pics"
    app.mount("/assets", StaticFiles(directory=static_dir), name="assets")
    if branding_dir.is_dir():
        app.mount("/branding", StaticFiles(directory=branding_dir), name="branding")
    elif config.branding_dir:
        raise ValueError(f"DOCQA_BRANDING_DIR does not exist: {branding_dir}")

    @app.get("/", include_in_schema=False)
    def chat_ui():
        return FileResponse(static_dir / "chat.html")

    @app.get("/privacy", include_in_schema=False)
    def privacy_notice():
        return FileResponse(static_dir / "privacy.html")

    @app.get("/terms", include_in_schema=False)
    def terms_of_service():
        return FileResponse(static_dir / "terms.html")

    @app.get("/subprocessors", include_in_schema=False)
    def subprocessors():
        return FileResponse(static_dir / "subprocessors.html")

    @app.get("/v1/branding")
    def branding(request: Request):
        hostname = _normalized_hostname(request.url.hostname or "")
        selected = request.app.state.tenant_branding.get(hostname)
        if selected is None:
            return TenantBranding(
                tenant_id="default",
                name="Doc Reader",
            ).model_dump(exclude={"tenant_id"})
        return selected.model_dump(exclude={"tenant_id"})

    @app.get("/v1/auth/config")
    def auth_config(request: Request):
        if config.deployment.auth_provider == "supabase":
            if not config.supabase_url or not config.supabase_anon_key:
                raise HTTPException(status_code=503, detail="Supabase Auth is not configured")
            return {
                "supabase_url": config.supabase_url.rstrip("/"),
                "supabase_anon_key": config.supabase_anon_key,
            }
        if config.deployment.auth_provider == "cognito":
            domain = urlsplit(config.cognito_domain)
            if (
                not config.cognito_client_id
                or domain.scheme != "https"
                or not domain.netloc
                or domain.path
            ):
                raise HTTPException(status_code=503, detail="Cognito Auth is not configured")
            callback_url = str(request.base_url).rstrip("/") + "/"
            return {
                "auth_provider": "cognito",
                "cognito_domain": config.cognito_domain.rstrip("/"),
                "cognito_client_id": config.cognito_client_id,
                "cognito_callback_url": callback_url,
                "cognito_logout_url": callback_url,
                "microsoft_graph_client_id": config.microsoft_graph_client_id,
            }
        raise HTTPException(status_code=503, detail="Authentication provider is not configured")

    @app.get("/v1/deployment", include_in_schema=False)
    def deployment():
        return {
            "model": config.deployment.model,
            "auth_provider": config.deployment.auth_provider,
            "document_store": config.deployment.document_store,
        }

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready():
        ready_state, _database_status, _latency = probe_dependencies(app, refresh=False)
        if not ready_state:
            raise HTTPException(status_code=503, detail="Application dependencies are not ready")
        return {"status": "ready"}

    @app.get("/metrics", include_in_schema=False)
    def metrics(request: Request):
        app_metrics = app.state.app_metrics
        with app_metrics["lock"]:
            requests_total = app_metrics["requests"]
            errors_total = app_metrics["errors"]
            total_latency_ms = app_metrics["total_latency_ms"]
            max_latency_ms = app_metrics["max_latency_ms"]

        _ready_state, database_status, database_latency_ms = probe_dependencies(app, refresh=False)

        provider_status = 1
        provider_backend = config.backend
        if provider_backend == "llm":
            provider_status = 0 if getattr(app.state.backend, "last_errors", []) else 1

        mean_latency_ms = (
            int(total_latency_ms / requests_total) if requests_total else 0
        )
        body = "\n".join([
            "# HELP docqa_app_ready Application readiness.",
            "# TYPE docqa_app_ready gauge",
            f"docqa_app_ready {1 if app.state.ready else 0}",
            "# HELP docqa_http_requests_total Total HTTP requests.",
            "# TYPE docqa_http_requests_total counter",
            f"docqa_http_requests_total {requests_total}",
            "# HELP docqa_http_errors_total HTTP 5xx and unhandled errors.",
            "# TYPE docqa_http_errors_total counter",
            f"docqa_http_errors_total {errors_total}",
            "# HELP docqa_http_latency_mean_ms Mean HTTP request latency.",
            "# TYPE docqa_http_latency_mean_ms gauge",
            f"docqa_http_latency_mean_ms {mean_latency_ms}",
            "# HELP docqa_http_latency_max_ms Maximum observed HTTP request latency.",
            "# TYPE docqa_http_latency_max_ms gauge",
            f"docqa_http_latency_max_ms {max_latency_ms}",
            "# HELP docqa_database_ready Database health probe status.",
            "# TYPE docqa_database_ready gauge",
            f"docqa_database_ready {database_status}",
            "# HELP docqa_database_latency_ms Database health probe latency.",
            "# TYPE docqa_database_latency_ms gauge",
            f"docqa_database_latency_ms {database_latency_ms}",
            "# HELP docqa_provider_ready Provider/backend health status.",
            "# TYPE docqa_provider_ready gauge",
            f"docqa_provider_ready {provider_status}",
            "",
        ])
        return PlainTextResponse(
            body,
            media_type="text/plain; version=0.0.4",
            headers={"Cache-Control": "no-store"},
        )

    @app.get(
        "/v1/platform/organizations",
        response_model=list[OrganizationRegistrationResponse],
    )
    def platform_organizations(
        request: Request,
        _user: Annotated[AuthenticatedUser, Depends(_platform_admin)],
    ):
        return request.app.state.store.list_registered_organizations()

    @app.get("/v1/platform/offboarding-requests")
    def platform_offboarding_requests(
        request: Request,
        actor: Annotated[AuthenticatedUser, Depends(_platform_admin)],
    ):
        return request.app.state.store.list_workspace_offboarding_requests(actor.user_id)

    @app.post("/v1/platform/offboarding-requests/{request_id}/review")
    def review_workspace_offboarding_request(
        request_id: UUID,
        payload: OffboardingReviewRequest,
        request: Request,
        actor: Annotated[AuthenticatedUser, Depends(_platform_admin)],
    ):
        try:
            return request.app.state.store.review_workspace_offboarding_request(
                str(request_id),
                actor.user_id,
                payload.approve,
                payload.review_note.strip(),
            )
        except OffboardingRequestConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.delete("/v1/platform/organizations/{microsoft_tenant_id}")
    def remove_platform_organization(
        microsoft_tenant_id: UUID,
        request: Request,
        _user: Annotated[AuthenticatedUser, Depends(_platform_admin)],
    ):
        if not request.app.state.store.remove_registered_organization(str(microsoft_tenant_id)):
            raise HTTPException(status_code=404, detail="Organization registration was not found")
        return {"removed": True}

    @app.get("/v1/platform/admins", response_model=list[PlatformAdminResponse])
    def platform_admins(
        request: Request,
        _user: Annotated[AuthenticatedUser, Depends(_platform_admin)],
    ):
        return request.app.state.store.list_platform_admins()

    @app.get(
        "/v1/platform/admin-audit",
        response_model=list[PlatformAdminAuditResponse],
    )
    def platform_admin_audit(
        request: Request,
        _user: Annotated[AuthenticatedUser, Depends(_platform_admin)],
    ):
        return request.app.state.store.list_platform_admin_audit()

    @app.post(
        "/v1/platform/admins",
        response_model=PlatformAdminResponse,
        status_code=201,
    )
    def grant_platform_admin(
        payload: PlatformAdminGrantRequest,
        request: Request,
        actor: Annotated[AuthenticatedUser, Depends(_platform_admin)],
    ):
        try:
            return request.app.state.store.grant_platform_admin(actor.user_id, payload.email)
        except PlatformAdminTargetNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.delete("/v1/platform/admins/{target_user_id}")
    def revoke_platform_admin(
        target_user_id: UUID,
        request: Request,
        actor: Annotated[AuthenticatedUser, Depends(_platform_admin)],
    ):
        try:
            removed = request.app.state.store.revoke_platform_admin(
                actor.user_id,
                str(target_user_id),
            )
        except LastPlatformAdminError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not removed:
            raise HTTPException(status_code=404, detail="Platform administrator was not found")
        return {"removed": True}

    @app.get(
        "/v1/organization/graph/catalog",
        response_model=OrganizationGraphCatalogResponse,
    )
    def organization_graph_catalog(
        request: Request,
        user: Annotated[AuthenticatedUser, Depends(_authenticated_user)],
        graph_access_token: Annotated[str | None, Header(alias="X-Microsoft-Graph-Token")] = None,
    ):
        graph, organization = _verified_graph(user, graph_access_token)
        if request.app.state.store.get_registered_organization(organization["tenant_id"]):
            raise HTTPException(
                status_code=409,
                detail="This Microsoft organization is already registered.",
            )
        try:
            groups = graph.security_groups()
        except GraphAPIError as exc:
            status = 403 if exc.status_code in {401, 403} else 502
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        request_status = request.app.state.store.get_organization_request_status(
            organization["tenant_id"]
        )
        return OrganizationGraphCatalogResponse(
            tenant_id=organization["tenant_id"],
            organization_name=organization["name"],
            verified_domains=organization["verified_domains"],
            groups=[
                OrganizationGroupResponse(id=group["id"], display_name=group["display_name"])
                for group in groups
            ],
            request_status=request_status["status"] if request_status else None,
        )

    @app.post(
        "/v1/organization/requests",
        response_model=OrganizationRequestResponse,
        status_code=201,
    )
    def submit_organization_request(
        payload: OrganizationRequestSubmit,
        request: Request,
        user: Annotated[AuthenticatedUser, Depends(_authenticated_user)],
        graph_access_token: Annotated[str | None, Header(alias="X-Microsoft-Graph-Token")] = None,
    ):
        graph, organization = _verified_graph(user, graph_access_token)
        if request.app.state.store.get_registered_organization(organization["tenant_id"]):
            raise HTTPException(
                status_code=409,
                detail="This Microsoft organization is already registered.",
            )
        try:
            graph_role_template_ids = graph.current_user_role_template_ids()
        except GraphAPIError as exc:
            status = 403 if exc.status_code in {401, 403} else 502
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        if not graph_role_template_ids.intersection(MICROSOFT_ORG_ADMIN_ROLE_TEMPLATE_IDS):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Organization requests must be submitted by a Microsoft Entra "
                    "Global Administrator, Privileged Role Administrator, Cloud "
                    "Application Administrator, or Application Administrator."
                ),
            )
        try:
            graph_groups = graph.security_groups()
        except GraphAPIError as exc:
            status = 403 if exc.status_code in {401, 403} else 502
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        available_groups = {group["id"]: group["display_name"] for group in graph_groups}
        if any(str(group.group_id).lower() not in available_groups for group in payload.groups):
            raise HTTPException(
                status_code=400,
                detail="One or more selected groups are not security groups in this Microsoft tenant.",
            )
        selected_groups = [
            {
                "group_id": str(group.group_id).lower(),
                "display_name": available_groups[str(group.group_id).lower()],
                "workspace_group_name": group.workspace_group_name,
            }
            for group in payload.groups
        ]
        try:
            submitted = request.app.state.store.create_organization_request(
                user.user_id,
                organization["tenant_id"],
                organization["name"],
                organization["verified_domains"],
                selected_groups,
            )
        except OrganizationRequestConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except UnconfirmedOrganizationRequesterError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return submitted

    @app.get(
        "/v1/platform/organization-requests",
        response_model=list[OrganizationRequestResponse],
    )
    def platform_organization_requests(
        request: Request,
        actor: Annotated[AuthenticatedUser, Depends(_platform_admin)],
    ):
        return request.app.state.store.list_organization_requests(actor.user_id)

    @app.post(
        "/v1/platform/organization-requests/{request_id}/review",
        response_model=OrganizationRequestResponse,
    )
    def review_organization_request(
        request_id: UUID,
        payload: OrganizationReviewRequest,
        request: Request,
        actor: Annotated[AuthenticatedUser, Depends(_platform_admin)],
    ):
        try:
            reviewed = request.app.state.store.review_organization_request(
                str(request_id),
                actor.user_id,
                payload.approve,
                payload.review_note.strip(),
            )
        except OrganizationRequestConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return reviewed

    @app.post("/v1/organization/graph/sync")
    def sync_organization_graph_membership(
        request: Request,
        user: Annotated[AuthenticatedUser, Depends(_authenticated_user)],
        graph_access_token: Annotated[str | None, Header(alias="X-Microsoft-Graph-Token")] = None,
    ):
        graph, organization = _verified_graph(user, graph_access_token)
        workspace_tenant_id = user.app_metadata.get("tenant_id")
        if (
            not isinstance(workspace_tenant_id, str)
            or user.app_metadata.get("workspace_type") != "organization"
            or not request.app.state.store.get_registered_organization(organization["tenant_id"])
        ):
            raise HTTPException(
                status_code=409,
                detail="An approved organization workspace is required before group synchronization.",
            )
        mappings = request.app.state.store.get_registered_organization_groups(
            organization["tenant_id"]
        )
        try:
            current_group_ids = graph.current_user_group_ids()
        except GraphAPIError as exc:
            status = 401 if exc.status_code == 401 else 403 if exc.status_code == 403 else 502
            raise HTTPException(status_code=status, detail=str(exc)) from exc

        graph_groups = {
            mapping["workspace_group_name"]
            for mapping in mappings
            if mapping["graph_group_id"].lower() in current_group_ids
        }
        prior_graph_groups = user.app_metadata.get("graph_groups", [])
        if not isinstance(prior_graph_groups, list) or not all(
            isinstance(group, str) for group in prior_graph_groups
        ):
            raise HTTPException(status_code=409, detail="Saved Microsoft group metadata is invalid.")
        current_groups = user.app_metadata.get("groups", [])
        if not isinstance(current_groups, list) or not all(
            isinstance(group, str) for group in current_groups
        ):
            raise HTTPException(status_code=409, detail="Saved workspace group metadata is invalid.")
        roles = user.app_metadata.get("roles", [])
        if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
            raise HTTPException(status_code=409, detail="Saved workspace role metadata is invalid.")
        retained_groups = set(current_groups) - set(prior_graph_groups)
        synced_groups = sorted(retained_groups | graph_groups)
        updated_metadata = {
            **user.app_metadata,
            "groups": synced_groups,
            "graph_groups": sorted(graph_groups),
            "graph_groups_synced_at": datetime.now(timezone.utc).isoformat(),
        }
        request.app.state.store.update_identity_app_metadata(user.user_id, updated_metadata)
        try:
            request.app.state.store.register_current_member(
                workspace_tenant_id,
                user.user_id,
                user.provider,
                organization["tenant_id"],
                "organization",
                str(user.app_metadata.get("workspace_name") or organization["name"]),
                synced_groups,
                [role for role in roles if role != "platform_admin"],
                ORGANIZATION_MEMBER_LIMIT,
            )
        except TrialMemberLimitError as exc:
            raise HTTPException(
                status_code=402,
                detail="This organization preview has reached its member limit.",
            ) from exc
        return {"groups": sorted(graph_groups)}

    @app.get("/v1/onboarding/options", response_model=OnboardingOptionsResponse)
    def onboarding_options(
        request: Request,
        user: Annotated[AuthenticatedUser, Depends(_authenticated_user)],
    ):
        organization = _onboarding_organization(request.app.state.store, user)
        account_type = user.account_type
        request_status = None
        organization_graph_required = False
        if account_type == "organization" and organization is None:
            account_type = "personal"
            if user.microsoft_tenant_id:
                request_status = request.app.state.store.get_organization_request_status(
                    user.microsoft_tenant_id
                )
        elif organization and user.microsoft_tenant_id:
            organization_graph_required = bool(
                request.app.state.store.get_registered_organization_groups(
                    user.microsoft_tenant_id
                )
            )
        return OnboardingOptionsResponse(
            onboarding_required=not bool(user.app_metadata.get("tenant_id")),
            account_type=account_type,
            provider=user.provider,
            organization_name=organization["name"] if organization else None,
            organization_request_status=request_status["status"] if request_status else None,
            organization_setup_available=(
                user.provider == "azure"
                and user.account_type == "organization"
                and organization is None
            ),
            organization_graph_required=organization_graph_required,
            is_platform_admin=request.app.state.store.is_platform_admin(user.user_id),
        )

    @app.post("/v1/onboarding", response_model=WorkspaceResponse, status_code=201)
    def onboard_workspace(
        payload: WorkspaceSetupRequest,
        request: Request,
        user: Annotated[AuthenticatedUser, Depends(_authenticated_user)],
        graph_access_token: Annotated[str | None, Header(alias="X-Microsoft-Graph-Token")] = None,
    ):
        if user.app_metadata.get("tenant_id"):
            raise HTTPException(status_code=409, detail="This account already has a workspace")
        organization = _onboarding_organization(request.app.state.store, user)
        account_type = user.account_type
        if account_type == "organization" and organization is None:
            account_type = "personal"
        if account_type is None:
            raise HTTPException(
                status_code=403,
                detail="We could not verify whether this Microsoft account is personal or belongs to an organization.",
            )
        if payload.account_type != account_type:
            raise HTTPException(
                status_code=403,
                detail="The selected workspace type does not match the account's approved onboarding plan.",
            )

        group_mappings: list[dict] = []
        graph_member_groups: set[str] = set()
        graph_sync_at: str | None = None
        is_initial_organization_admin = False
        if account_type == "organization":
            group_mappings = request.app.state.store.get_registered_organization_groups(
                user.microsoft_tenant_id
            )
            initial_admin_emails = organization.get("initial_admin_emails", [])
            is_initial_organization_admin = (
                isinstance(initial_admin_emails, list)
                and user.email.casefold() in {
                    email.casefold() for email in initial_admin_emails if isinstance(email, str)
                }
            )
            if graph_access_token:
                graph, _graph_organization = _verified_graph(user, graph_access_token)
                try:
                    graph_member_ids = graph.current_user_group_ids()
                except GraphAPIError as exc:
                    status = 403 if exc.status_code in {401, 403} else 502
                    raise HTTPException(status_code=status, detail=str(exc)) from exc
                graph_sync_at = datetime.now(timezone.utc).isoformat()
                graph_member_groups = {
                    mapping["workspace_group_name"]
                    for mapping in group_mappings
                    if mapping["graph_group_id"].lower() in graph_member_ids
                }
            if group_mappings and not is_initial_organization_admin and not graph_access_token:
                raise HTTPException(
                    status_code=403,
                    detail="Connect Microsoft Graph to verify membership in an approved organization group.",
                )
            if group_mappings and not is_initial_organization_admin and not graph_member_groups:
                raise HTTPException(
                    status_code=403,
                    detail="You are not a member of an Entra security group approved for this workspace.",
                )

        if account_type == "personal":
            external_account_id = user.user_id
            workspace_name = payload.workspace_name or "Personal workspace"
            member_limit = PERSONAL_MEMBER_LIMIT
        else:
            external_account_id = user.microsoft_tenant_id
            workspace_name = organization["name"]
            member_limit = ORGANIZATION_MEMBER_LIMIT

        provisioned = request.app.state.store.provision_trial_workspace(
            user.user_id,
            user.provider,
            external_account_id,
            payload.account_type,
            workspace_name,
            member_limit,
        )
        tenant_id = str(provisioned["tenant_id"])
        if account_type == "organization":
            group_mappings = request.app.state.store.get_registered_organization_groups(
                user.microsoft_tenant_id
            )
            for mapping in group_mappings:
                request.app.state.store.create_workspace_group(
                    tenant_id,
                    mapping["workspace_group_name"],
                    user.user_id,
                )
        granted_groups = provisioned["member_groups"]
        if account_type == "organization" and group_mappings:
            granted_groups = sorted((set(granted_groups) - {"staff"}) | graph_member_groups)
        granted_roles = provisioned["member_roles"]
        updated_metadata = {
            **user.app_metadata,
            "tenant_id": tenant_id,
            "groups": granted_groups,
            "roles": granted_roles,
            "workspace_type": provisioned["workspace_type"],
            "workspace_name": provisioned["workspace_name"],
        }
        if account_type == "organization" and graph_sync_at:
            updated_metadata["graph_groups"] = sorted(graph_member_groups)
            updated_metadata["graph_groups_synced_at"] = graph_sync_at
        else:
            updated_metadata.pop("graph_groups", None)
            updated_metadata.pop("graph_groups_synced_at", None)
        if account_type == "organization" and group_mappings:
            request.app.state.store.register_current_member(
                tenant_id,
                user.user_id,
                user.provider,
                external_account_id,
                "organization",
                provisioned["workspace_name"],
                granted_groups,
                [role for role in granted_roles if role != "platform_admin"],
                member_limit,
            )
        request.app.state.store.update_identity_app_metadata(user.user_id, updated_metadata)
        _write_audit(request.app.state.store, {
            "tenant_id": tenant_id,
            "user_id": user.user_id,
            "action": "workspace_onboarding",
            "outcome": "ok",
        })
        return WorkspaceResponse(
            tenant_id=tenant_id,
            name=provisioned["workspace_name"],
            workspace_type=provisioned["workspace_type"],
            member_count=int(provisioned["member_count"]),
            member_limit=int(provisioned["member_limit"]),
        )

    @app.get("/v1/me")
    def me(
        request: Request,
        principal: Annotated[Principal, Depends(_principal)],
        user: Annotated[AuthenticatedUser, Depends(_authenticated_user)],
    ):
        return {
            "user_id": principal.user_id,
            "tenant_id": principal.tenant_id,
            "groups": list(principal.groups),
            "is_admin": principal.is_admin,
            "is_auditor": principal.is_auditor,
            "workspace_type": principal.workspace_type,
            "workspace_name": principal.workspace_name,
            "organization_graph_sync_available": (
                user.provider == "azure"
                and principal.workspace_type == "organization"
                and bool(
                    user.microsoft_tenant_id
                    and request.app.state.store.get_registered_organization_groups(
                        user.microsoft_tenant_id
                    )
                )
            ),
            "is_platform_admin": request.app.state.store.is_platform_admin(principal.user_id),
        }

    @app.get("/v1/admin/dashboard", response_model=AdminDashboardResponse)
    def admin_dashboard(request: Request,
                        principal: Annotated[Principal, Depends(_admin_or_auditor)]):
        dashboard = request.app.state.store.admin_dashboard(principal.tenant_id)
        return AdminDashboardResponse.model_validate(dashboard)

    @app.get("/v1/workspace/export")
    def export_workspace(
        request: Request,
        principal: Annotated[Principal, Depends(_admin)],
    ):
        export = request.app.state.store.export_workspace(principal.tenant_id)
        return JSONResponse(
            content=export,
            headers={"Content-Disposition": 'attachment; filename="workspace-export.json"'},
        )

    @app.post("/v1/workspace/offboarding")
    def request_workspace_offboarding(
        request: Request,
        principal: Annotated[Principal, Depends(_admin)],
    ):
        try:
            return request.app.state.store.create_workspace_offboarding_request(
                principal.tenant_id,
                principal.user_id,
            )
        except OffboardingRequestConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/v1/usage", response_model=UsageResponse)
    def usage(request: Request, principal: Annotated[Principal, Depends(_principal)]):
        persisted = request.app.state.store.trial_usage(principal.tenant_id)
        documents = request.app.state.store.list_documents(principal.tenant_id)
        if persisted:
            workspace_name = persisted["workspace_name"]
            workspace_type = persisted["workspace_type"]
            member_count = int(persisted["member_count"])
            member_limit = int(persisted["member_limit"])
            tracked = True
        else:
            workspace_name = principal.workspace_name or "Workspace"
            workspace_type = principal.workspace_type
            member_count = 0
            member_limit = (
                PERSONAL_MEMBER_LIMIT
                if workspace_type == "personal"
                else ORGANIZATION_MEMBER_LIMIT
            )
            tracked = False
        return UsageResponse(
            workspace_name=workspace_name,
            workspace_type=workspace_type,
            member_count=member_count,
            member_limit=member_limit,
            document_count=len(documents),
            document_limit=TRIAL_DOCUMENT_LIMIT,
            member_count_tracked=tracked,
            upgrade_available=False,
            upgrade_message="Contact the product administrator to discuss paid plans.",
        )

    @app.post("/v1/query", response_model=QueryResponse)
    def query(payload: QueryRequest, request: Request,
              principal: Annotated[Principal, Depends(_principal)]):
        if not request.app.state.query_limiter.allow(principal.user_id):
            raise HTTPException(
                status_code=429,
                detail="Too many questions. Please wait a moment.",
                headers={"Retry-After": "60"},
            )
        profile = request.app.state.profiles.get(
            principal.tenant_id, Profile(tenant_id=principal.tenant_id)
        )
        try:
            result = answer(
                payload.question,
                request.app.state.store,
                request.app.state.backend,
                k=payload.k,
                user_groups=list(principal.groups),
                user=principal.user_id,
                profile=profile,
                tenant_id=principal.tenant_id,
            )
        except (LLMUnavailableError, OllamaUnavailableError, DocumentStoreError):
            _write_audit(request.app.state.store, {
                "tenant_id": principal.tenant_id,
                "user_id": principal.user_id,
                "action": "query_error",
                "outcome": "error",
            })
            raise
        _write_audit(request.app.state.store, {
            "tenant_id": principal.tenant_id,
            "user_id": principal.user_id,
            "action": "query",
            "question": _audit_question(request.app.state.settings.audit_questions, payload.question),
            "documents_retrieved": sorted({hit.chunk.doc for hit in result.hits}),
            "documents_cited": sorted({citation["doc"] for citation in result.citations}),
            "outcome": result.reason,
        })
        return QueryResponse(
            answer=result.text,
            found=result.found,
            reason=result.reason,
            citations=[
                CitationResponse(
                    document=citation["doc"],
                    location=citation["loc"],
                    quote=citation.get("quote", ""),
                )
                for citation in result.citations
            ],
        )

    @app.get("/v1/documents", response_model=list[DocumentResponse])
    def list_documents(request: Request, principal: Annotated[Principal, Depends(_admin)]):
        rows = request.app.state.store.list_documents(principal.tenant_id)
        try:
            return [DocumentResponse.model_validate(row) for row in rows]
        except (ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=503,
                detail="The document service returned an invalid library response",
            ) from exc

    @app.get("/v1/workspace/directory", response_model=WorkspaceDirectoryResponse)
    def workspace_directory(request: Request,
                            principal: Annotated[Principal, Depends(_admin)]):
        try:
            directory = request.app.state.store.workspace_directory(principal.tenant_id)
            return WorkspaceDirectoryResponse.model_validate(directory)
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=503,
                detail="The document service returned an invalid workspace directory",
            ) from exc

    @app.post("/v1/workspace/groups", response_model=WorkspaceGroupResponse, status_code=201)
    def create_workspace_group(
        payload: WorkspaceGroupRequest,
        request: Request,
        principal: Annotated[Principal, Depends(_admin)],
    ):
        name = request.app.state.store.create_workspace_group(
            principal.tenant_id, payload.name, principal.user_id
        )
        _write_audit(request.app.state.store, {
            "tenant_id": principal.tenant_id,
            "user_id": principal.user_id,
            "action": "workspace_group_create",
            "outcome": "ok",
        })
        return WorkspaceGroupResponse(name=name)

    @app.patch("/v1/documents/{document_id}/acl", response_model=AclUpdateResponse)
    def update_document_acl(
        document_id: str,
        payload: DocumentAclRequest,
        request: Request,
        principal: Annotated[Principal, Depends(_admin)],
    ):
        try:
            canonical_id = str(UUID(document_id))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="document_id must be a UUID") from exc
        updated = request.app.state.store.update_document_acl(
            principal.tenant_id, canonical_id, payload.acl_groups
        )
        _write_audit(request.app.state.store, {
            "tenant_id": principal.tenant_id,
            "user_id": principal.user_id,
            "action": "acl_update",
            "outcome": "ok" if updated else "not_found",
        })
        return AclUpdateResponse(updated=updated)

    @app.post("/v1/documents", response_model=IngestResponse, status_code=201)
    def ingest_document(
        request: Request,
        principal: Annotated[Principal, Depends(_admin)],
        file: Annotated[UploadFile, File()],
        acl_groups: Annotated[str, Form()],
    ):
        name = Path((file.filename or "").replace("\\", "/")).name
        suffix = Path(name).suffix.lower()
        if (not name or name in {".", ".."} or len(name) > 255
                or any(ord(character) < 32 for character in name) or suffix not in SUPPORTED):
            raise HTTPException(status_code=415, detail="Upload a PDF, DOCX, TXT, or Markdown file")
        existing_documents = request.app.state.store.list_documents(principal.tenant_id)
        if (len(existing_documents) >= TRIAL_DOCUMENT_LIMIT
                and all(document.get("name") != name for document in existing_documents)):
            raise HTTPException(status_code=402, detail=_document_limit_detail())
        if not request.app.state.upload_limiter.allow(principal.user_id):
            raise HTTPException(
                status_code=429,
                detail="Too many uploads. Please wait a moment.",
                headers={"Retry-After": "60"},
            )
        content = file.file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="File exceeds the 20 MiB upload limit")
        groups = sorted({group.strip() for group in acl_groups.split(",") if group.strip()})
        if (not groups or len(groups) > MAX_ACL_GROUPS
                or any(len(group) > 100 for group in groups)):
            raise HTTPException(
                status_code=422,
                detail=f"Provide between 1 and {MAX_ACL_GROUPS} ACL groups, each at most 100 characters",
            )

        if suffix == ".docx":
            try:
                check_docx_safe(content)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=f"Could not parse the uploaded document: {exc}") from exc
        if not request.app.state.ingest_slots.acquire(timeout=30):
            raise HTTPException(status_code=503, detail="The server is busy processing other uploads. Try again shortly.")
        try:
            return _ingest(request, principal, name, groups, content)
        finally:
            request.app.state.ingest_slots.release()

    def _ingest(request: Request, principal: Principal, name: str, groups: list[str], content: bytes):
        with tempfile.TemporaryDirectory(prefix="docqa-ingest-") as directory:
            path = Path(directory) / name
            path.write_bytes(content)
            issues: list[str] = []
            try:
                chunks = ingest_folder(
                    directory,
                    acl={name: groups},
                    default_acl=["admin"],
                    issues=issues,
                    max_pdf_pages=MAX_PDF_PAGES,
                    pdf_parse_timeout=PDF_PARSE_TIMEOUT_SECONDS,
                    docx_parse_timeout=DOCX_PARSE_TIMEOUT_SECONDS,
                )
            except PDFPageLimitError as exc:
                raise HTTPException(
                    status_code=413,
                    detail=f"PDF exceeds the {MAX_PDF_PAGES}-page limit",
                ) from exc
            except PDFParseTimeoutError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"PDF parsing exceeded the {PDF_PARSE_TIMEOUT_SECONDS}-second limit",
                ) from exc
            except DOCXParseTimeoutError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"DOCX parsing exceeded the {DOCX_PARSE_TIMEOUT_SECONDS}-second limit",
                ) from exc
            except DOCXParseWorkerError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"Could not parse the uploaded DOCX ({type(exc).__name__})",
                ) from exc
            except (OSError, ValueError, zipfile.BadZipFile, PackageNotFoundError, PdfReadError) as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"Could not parse the uploaded document ({type(exc).__name__})",
                ) from exc
        if issues:
            raise HTTPException(status_code=422, detail={"ingestion_issues": issues})
        if not chunks:
            raise HTTPException(status_code=422, detail="The file contains no extractable text")
        if len(chunks) > MAX_INGEST_CHUNKS:
            raise HTTPException(status_code=413, detail="Document exceeds the 2,000 chunk ingestion limit")

        embeddings = request.app.state.embedder.encode_docs([chunk.index_text for chunk in chunks])
        if embeddings.shape != (len(chunks), 384):
            raise HTTPException(status_code=500, detail="Configured embedder does not produce 384 dimensions")
        rows = [
            {
                "location": chunk.loc,
                "content": chunk.text,
                "embedding": [round(float(value), 6) for value in embeddings[i]],
            }
            for i, chunk in enumerate(chunks)
        ]
        try:
            document_id = request.app.state.store.upsert_document(
                principal.tenant_id, name, groups, principal.user_id, rows,
                document_limit=TRIAL_DOCUMENT_LIMIT,
            )
        except DocumentLimitError as exc:
            raise HTTPException(status_code=402, detail=_document_limit_detail()) from exc
        _write_audit(request.app.state.store, {
            "tenant_id": principal.tenant_id,
            "user_id": principal.user_id,
            "action": "ingest",
            "document_name": name,
            "outcome": "ok",
        })
        return IngestResponse(document_id=document_id, name=name, chunks=len(chunks))

    @app.delete("/v1/documents/{document_id}", response_model=DeleteResponse)
    def delete_document(document_id: str, request: Request,
                        principal: Annotated[Principal, Depends(_admin)]):
        try:
            canonical_id = str(UUID(document_id))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="document_id must be a UUID") from exc
        deleted = request.app.state.store.delete_document(principal.tenant_id, canonical_id)
        _write_audit(request.app.state.store, {
            "tenant_id": principal.tenant_id,
            "user_id": principal.user_id,
            "action": "delete",
            "outcome": "ok" if deleted else "not_found",
        })
        return DeleteResponse(deleted=deleted)

    @app.exception_handler(DocumentStoreError)
    async def document_store_error_handler(_request: Request, exc: DocumentStoreError):
        return JSONResponse(
            status_code=503,
            content={"detail": "The document service is temporarily unavailable"},
        )

    @app.exception_handler(LLMUnavailableError)
    async def llm_unavailable_handler(_request: Request, exc: LLMUnavailableError):
        return JSONResponse(
            status_code=503,
            content={"detail": "The answer service is temporarily unavailable. Please try again."},
        )

    @app.exception_handler(OllamaUnavailableError)
    async def ollama_unavailable_handler(_request: Request, exc: OllamaUnavailableError):
        return JSONResponse(
            status_code=503,
            content={"detail": "The local answer service is temporarily unavailable. Please try again."},
        )

    @app.exception_handler(TrialMemberLimitError)
    async def trial_member_limit_handler(_request: Request, exc: TrialMemberLimitError):
        member_limit = exc.member_limit or ORGANIZATION_MEMBER_LIMIT
        account_label = "personal workspace" if member_limit == PERSONAL_MEMBER_LIMIT else "organization"
        return JSONResponse(
            status_code=402,
            content={
                "detail": (
                    f"This {account_label} preview is limited to {member_limit} users. "
                    "Contact the product administrator to discuss paid plans."
                )
            },
        )

    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("docqa.api:app", host="127.0.0.1", port=8000)
