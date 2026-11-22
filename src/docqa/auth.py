"""Supabase authentication and trusted principal construction."""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
import requests

MICROSOFT_PERSONAL_TENANT_ID = "9188040d-6c67-4c5b-b112-36a304b66dad"
GRAPH_GROUP_SYNC_MAX_AGE = timedelta(hours=12)


@dataclass(frozen=True)
class Principal:
    user_id: str
    tenant_id: str
    groups: tuple[str, ...]
    is_admin: bool
    workspace_type: str = "organization"
    workspace_name: str = ""
    is_auditor: bool = False


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: str
    email: str
    provider: str
    microsoft_tenant_id: str | None
    app_metadata: dict
    microsoft_user_id: str | None = None
    auth_provider: str = "supabase"
    issuer: str = "supabase"
    subject: str | None = None
    email_verified: bool = False

    @property
    def account_type(self) -> str | None:
        if self.provider in {"google", "cognito"}:
            return "personal"
        if self.provider == "azure":
            if self.microsoft_tenant_id == MICROSOFT_PERSONAL_TENANT_ID:
                return "personal"
            if self.microsoft_tenant_id:
                return "organization"
        return None


class AuthenticationError(RuntimeError):
    pass


class IdentityProviderUnavailable(RuntimeError):
    pass


class SupabaseAuthVerifier:
    """Validate access tokens through Supabase Auth and trust app_metadata only."""

    def __init__(self, url: str, anon_key: str, timeout: float = 10):
        if not url or not anon_key:
            raise ValueError("SUPABASE_URL and SUPABASE_ANON_KEY are required")
        self.url = url.rstrip("/")
        self.anon_key = anon_key
        self.timeout = timeout

    def authenticate_user(self, token: str) -> AuthenticatedUser:
        try:
            response = requests.get(
                f"{self.url}/auth/v1/user",
                headers={"apikey": self.anon_key, "Authorization": f"Bearer {token}"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise IdentityProviderUnavailable("Supabase Auth could not be reached") from exc
        if response.status_code in (401, 403):
            raise AuthenticationError("Access token is invalid or expired")
        if response.status_code >= 500:
            raise IdentityProviderUnavailable("Supabase Auth is unavailable")
        try:
            response.raise_for_status()
            user = response.json()
        except (requests.RequestException, requests.JSONDecodeError) as exc:
            raise IdentityProviderUnavailable("Supabase Auth returned an invalid response") from exc

        metadata = user.get("app_metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        identities = user.get("identities", [])
        if not isinstance(identities, list):
            identities = []
        identity_providers = {
            identity.get("provider")
            for identity in identities
            if isinstance(identity, dict) and isinstance(identity.get("provider"), str)
        }
        provider = (
            "azure" if "azure" in identity_providers
            else "google" if "google" in identity_providers
            else metadata.get("provider", "")
        )
        microsoft_tenant_id = None
        microsoft_user_id = None
        if provider == "azure":
            for identity in identities:
                if not isinstance(identity, dict) or identity.get("provider") != "azure":
                    continue
                identity_id = identity.get("identity_id")
                if isinstance(identity_id, str) and identity_id:
                    microsoft_user_id = identity_id.lower()
                identity_data = identity.get("identity_data")
                if not isinstance(identity_data, dict):
                    continue
                object_id = identity_data.get("oid")
                if isinstance(object_id, str) and object_id:
                    microsoft_user_id = object_id.lower()
                tenant_id = identity_data.get("tid") or identity_data.get("tenant_id")
                issuer = identity_data.get("iss")
                if not tenant_id and isinstance(issuer, str):
                    parts = issuer.rstrip("/").split("/")
                    if len(parts) >= 2:
                        tenant_id = parts[-2] if parts[-1] == "v2.0" else parts[-1]
                if isinstance(tenant_id, str):
                    try:
                        microsoft_tenant_id = str(UUID(tenant_id)).lower()
                    except ValueError:
                        continue
                    else:
                        break
        user_id = user.get("id")
        if not isinstance(user_id, str):
            raise AuthenticationError("Supabase Auth returned an invalid user")
        email = user.get("email")
        return AuthenticatedUser(
            user_id=user_id,
            email=email if isinstance(email, str) else "",
            provider=provider if isinstance(provider, str) else "",
            microsoft_tenant_id=microsoft_tenant_id,
            app_metadata=metadata,
            microsoft_user_id=microsoft_user_id,
            auth_provider="supabase",
            issuer="supabase",
            subject=user_id,
            email_verified=bool(user.get("email_confirmed_at")),
        )

    def authenticate(self, token: str) -> Principal:
        return self.principal_from_user(self.authenticate_user(token))

    @staticmethod
    def principal_from_user(user: AuthenticatedUser) -> Principal:
        metadata = user.app_metadata
        tenant_id = metadata.get("tenant_id")
        groups = metadata.get("groups", [])
        roles = metadata.get("roles", [])
        if (not isinstance(tenant_id, str) or not tenant_id.strip()
                or not isinstance(groups, list) or not all(isinstance(g, str) for g in groups)
                or not isinstance(roles, list) or not all(isinstance(r, str) for r in roles)
                or not user.user_id):
            raise AuthenticationError("Trusted tenant, group, or user claims are invalid")
        trusted_groups = {group for group in groups if group and group != "all"}
        if "admin" in roles:
            trusted_groups.add("admin")
        return Principal(
            user_id=user.user_id,
            tenant_id=tenant_id,
            groups=tuple(sorted(trusted_groups)),
            is_admin="admin" in roles,
            workspace_type=(
                metadata["workspace_type"]
                if metadata.get("workspace_type") in {"personal", "organization"}
                else "organization"
            ),
            workspace_name=(
                metadata["workspace_name"]
                if isinstance(metadata.get("workspace_name"), str)
                else ""
            ),
            is_auditor="auditor" in roles,
        )


class CognitoAuthVerifier:
    """Verify Cognito access and identity tokens against the configured user pool."""

    def __init__(
        self,
        region: str,
        user_pool_id: str,
        client_id: str,
        timeout: float = 5,
    ):
        if not region or not user_pool_id or not client_id:
            raise ValueError(
                "DOCQA_COGNITO_REGION, DOCQA_COGNITO_USER_POOL_ID, and "
                "DOCQA_COGNITO_CLIENT_ID are required"
            )
        if not user_pool_id.startswith(f"{region}_"):
            raise ValueError("Cognito user pool ID does not match DOCQA_COGNITO_REGION")
        self.region = region
        self.user_pool_id = user_pool_id
        self.client_id = client_id
        self.issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
        self.jwks_url = f"{self.issuer}/.well-known/jwks.json"
        self.timeout = timeout
        self._jwks: dict[str, dict] = {}
        self._jwks_expiry = 0.0
        self._jwks_lock = threading.Lock()

    def _load_jwks(self, force: bool = False) -> dict[str, dict]:
        with self._jwks_lock:
            if self._jwks and not force and time.monotonic() < self._jwks_expiry:
                return self._jwks
            try:
                response = requests.get(self.jwks_url, timeout=self.timeout)
                response.raise_for_status()
                data = response.json()
            except (requests.RequestException, requests.JSONDecodeError) as exc:
                raise IdentityProviderUnavailable("Cognito signing keys are unavailable") from exc
            keys = data.get("keys") if isinstance(data, dict) else None
            if not isinstance(keys, list):
                raise IdentityProviderUnavailable("Cognito returned invalid signing keys")
            jwks = {
                key["kid"]: key
                for key in keys
                if (
                    isinstance(key, dict)
                    and isinstance(key.get("kid"), str)
                    and key.get("kty") == "RSA"
                    and key.get("use", "sig") == "sig"
                )
            }
            if not jwks:
                raise IdentityProviderUnavailable("Cognito returned no usable signing keys")
            self._jwks = jwks
            self._jwks_expiry = time.monotonic() + 3600
            return self._jwks

    def _decode(self, token: str, token_use: str) -> dict:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as exc:
            raise AuthenticationError("Access token is invalid or expired") from exc
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise AuthenticationError("Access token is invalid or expired")

        key_data = self._load_jwks().get(header["kid"])
        if key_data is None:
            key_data = self._load_jwks(force=True).get(header["kid"])
        if key_data is None:
            raise AuthenticationError("Access token is invalid or expired")
        try:
            public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key_data))
            claims = jwt.decode(
                token,
                public_key,
                algorithms=["RS256"],
                issuer=self.issuer,
                audience=self.client_id if token_use == "id" else None,
                options={
                    "require": ["exp", "iat", "sub", "token_use"],
                    "verify_aud": token_use == "id",
                },
            )
        except jwt.InvalidTokenError as exc:
            raise AuthenticationError("Access token is invalid or expired") from exc
        if claims.get("token_use") != token_use:
            raise AuthenticationError("Access token is invalid or expired")
        if token_use == "access" and claims.get("client_id") != self.client_id:
            raise AuthenticationError("Access token is invalid or expired")
        return claims

    @staticmethod
    def _identity_provider(claims: dict) -> tuple[str, str | None, str | None]:
        identities = claims.get("identities", [])
        if isinstance(identities, str):
            try:
                identities = json.loads(identities)
            except json.JSONDecodeError:
                identities = []
        if not isinstance(identities, list):
            identities = []
        identity = next((item for item in identities if isinstance(item, dict)), {})
        name = str(identity.get("providerName", "")).casefold()
        if name == "google":
            provider = "google"
        elif name in {"microsoft", "azure", "microsoftentra"}:
            provider = "azure"
        else:
            provider = "cognito"

        tenant = claims.get("custom:tenant_id") or claims.get("tid")
        try:
            tenant_id = str(UUID(tenant)).lower() if isinstance(tenant, str) else None
        except ValueError:
            tenant_id = None
        microsoft_user_id = (
            claims.get("custom:microsoft_user_id")
            or claims.get("oid")
            or identity.get("userId")
        )
        if not isinstance(microsoft_user_id, str):
            microsoft_user_id = None
        return provider, tenant_id, microsoft_user_id

    def authenticate_user(
        self,
        access_token: str,
        identity_token: str | None = None,
    ) -> AuthenticatedUser:
        if not identity_token:
            raise AuthenticationError("Cognito identity token is required")
        access_claims = self._decode(access_token, "access")
        identity_claims = self._decode(identity_token, "id")
        subject = access_claims.get("sub")
        if subject != identity_claims.get("sub"):
            raise AuthenticationError("Cognito tokens belong to different users")
        if not isinstance(subject, str):
            raise AuthenticationError("Cognito returned an invalid user")
        try:
            user_id = str(UUID(subject))
        except ValueError as exc:
            raise AuthenticationError("Cognito returned an invalid user") from exc

        provider, microsoft_tenant_id, microsoft_user_id = self._identity_provider(
            identity_claims
        )
        email = identity_claims.get("email")
        verified = identity_claims.get("email_verified") is True
        return AuthenticatedUser(
            user_id=user_id,
            email=email if isinstance(email, str) else "",
            provider=provider,
            microsoft_tenant_id=microsoft_tenant_id,
            app_metadata={},
            microsoft_user_id=microsoft_user_id,
            auth_provider="cognito",
            issuer=self.issuer,
            subject=subject,
            email_verified=verified,
        )

    @staticmethod
    def principal_from_user(user: AuthenticatedUser) -> Principal:
        return SupabaseAuthVerifier.principal_from_user(user)


def graph_group_sync_is_fresh(
    app_metadata: dict,
    now: datetime | None = None,
) -> bool:
    value = app_metadata.get("graph_groups_synced_at")
    if not isinstance(value, str):
        return False
    try:
        synced_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    if synced_at.tzinfo is None:
        return False
    age = (now or datetime.now(timezone.utc)) - synced_at.astimezone(timezone.utc)
    return timedelta(0) <= age <= GRAPH_GROUP_SYNC_MAX_AGE
