"""Authentication and authorization dependencies for API routes."""
from __future__ import annotations

from dataclasses import replace
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from .auth import (
    AuthenticatedUser,
    AuthenticationError,
    IdentityProviderUnavailable,
    Principal,
    graph_group_sync_is_fresh,
)
from .microsoft_graph import GraphAPIError, MicrosoftGraph
from .settings import normalized_hostname

PERSONAL_MEMBER_LIMIT = 10
ORGANIZATION_MEMBER_LIMIT = 15
MICROSOFT_ORG_ADMIN_ROLE_TEMPLATE_IDS = {
    "62e90394-69f5-4237-9190-012177145e10",  # Global Administrator
    "e8611ab8-c189-46e8-94e1-60213ab1f814",  # Privileged Role Administrator
    "158c047a-c907-4556-b7ef-446551a6b5f7",  # Cloud Application Administrator
    "9b895d92-2cd3-44c7-9d02-a6ac2d5ea5c3",  # Application Administrator
}


def _authenticated_identity(request: Request, token: str) -> AuthenticatedUser:
    if request.app.state.deployment.auth_provider == "cognito":
        identity_token = request.headers.get("x-docqa-identity-token")
        user = request.app.state.auth_verifier.authenticate_user(
            token, identity_token=identity_token
        )
        request.app.state.store.ensure_identity(
            user.user_id,
            user.auth_provider,
            user.issuer,
            user.subject or user.user_id,
            user.email,
            user.email_verified,
        )
    else:
        user = request.app.state.auth_verifier.authenticate_user(token)
    app_metadata = request.app.state.store.get_identity_app_metadata(user.user_id)
    return replace(user, app_metadata=app_metadata)


def _principal(request: Request, authorization: Annotated[str | None, Header()] = None) -> Principal:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="A Bearer access token is required")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="A Bearer access token is required")
    try:
        verifier = request.app.state.auth_verifier
        user = _authenticated_identity(request, token)
        principal = verifier.principal_from_user(user)
        if (
            user.provider == "azure"
            and user.account_type == "organization"
            and principal.workspace_type == "organization"
            and not graph_group_sync_is_fresh(user.app_metadata)
        ):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Microsoft group membership sync is missing or older than 12 hours. "
                    "Reconnect Microsoft Graph and sync groups to continue."
                ),
            )
        hostname = normalized_hostname(request.url.hostname or "")
        branding = request.app.state.tenant_branding.get(hostname)
        if branding and principal.tenant_id != branding.tenant_id:
            raise HTTPException(status_code=403, detail="This account does not belong to this workspace")
        if request.app.state.store.is_workspace_closed(principal.tenant_id):
            raise HTTPException(
                status_code=403,
                detail="This workspace has been closed. Contact the privacy contact for assistance.",
            )
        registration_provider = (
            user.provider if user.provider in {"google", "azure", "cognito"} else "legacy"
        )
        if registration_provider == "legacy":
            external_account_id = principal.tenant_id
        elif (
            user.provider == "azure"
            and principal.workspace_type == "organization"
            and user.microsoft_tenant_id
        ):
            external_account_id = user.microsoft_tenant_id
        elif user.provider == "azure" and principal.workspace_type == "organization":
            external_account_id = principal.tenant_id
        else:
            external_account_id = user.user_id
        member_limit = (
            PERSONAL_MEMBER_LIMIT
            if principal.workspace_type == "personal"
            else ORGANIZATION_MEMBER_LIMIT
        )
        tenant_roles = [
            role for role in user.app_metadata.get("roles", [])
            if role != "platform_admin"
        ]
        cache_key = (
            principal.tenant_id, principal.user_id, tuple(principal.groups),
            tuple(tenant_roles), principal.workspace_type,
        )
        if cache_key in request.app.state.registered:
            return principal
        request.app.state.store.register_current_member(
            principal.tenant_id,
            principal.user_id,
            registration_provider,
            external_account_id,
            principal.workspace_type,
            principal.workspace_name,
            list(principal.groups),
            tenant_roles,
            member_limit,
        )
        request.app.state.registered.add(cache_key)
        return principal
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except IdentityProviderUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _authenticated_user(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> AuthenticatedUser:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="A Supabase access token is required")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="A Supabase access token is required")
    try:
        return _authenticated_identity(request, token)
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except IdentityProviderUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _admin(principal: Annotated[Principal, Depends(_principal)]) -> Principal:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="Tenant administrator role is required")
    return principal


def _admin_or_auditor(principal: Annotated[Principal, Depends(_principal)]) -> Principal:
    if not principal.is_admin and not principal.is_auditor:
        raise HTTPException(status_code=403, detail="Tenant administrator or auditor role is required")
    return principal


def _platform_admin(
    request: Request,
    user: Annotated[AuthenticatedUser, Depends(_authenticated_user)],
) -> AuthenticatedUser:
    if not request.app.state.store.is_platform_admin(user.user_id):
        raise HTTPException(status_code=403, detail="Platform administrator role is required")
    return user


def _onboarding_organization(store, user: AuthenticatedUser) -> dict | None:
    if user.provider != "azure" or user.account_type != "organization" or not user.microsoft_tenant_id:
        return None
    return store.get_registered_organization(user.microsoft_tenant_id)


def _verified_graph(user: AuthenticatedUser, graph_access_token: str | None) -> tuple[MicrosoftGraph, dict]:
    if user.provider != "azure" or user.account_type != "organization":
        raise HTTPException(
            status_code=403,
            detail="Microsoft Graph organization setup is available only for work or school accounts.",
        )
    if not graph_access_token:
        raise HTTPException(
            status_code=403,
            detail="Connect Microsoft Graph again to continue organization setup.",
        )
    if not user.microsoft_tenant_id or not user.microsoft_user_id:
        raise HTTPException(
            status_code=403,
            detail="This Microsoft identity cannot be securely matched to its Graph account.",
        )
    graph = MicrosoftGraph(graph_access_token)
    try:
        graph_user = graph.current_user()
        organization = graph.organization()
    except GraphAPIError as exc:
        status = 403 if exc.status_code in {401, 403} else 502
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    if graph_user["id"].casefold() != user.microsoft_user_id.casefold():
        raise HTTPException(
            status_code=403,
            detail="The Microsoft Graph account does not match the signed-in Microsoft identity.",
        )
    if organization["tenant_id"].casefold() != user.microsoft_tenant_id.casefold():
        raise HTTPException(
            status_code=403,
            detail="The Microsoft Graph tenant does not match the signed-in Microsoft identity.",
        )
    return graph, organization
