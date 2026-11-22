"""Minimal delegated Microsoft Graph reads used during organization onboarding."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import requests


GRAPH_ROOT = "https://graph.microsoft.com/v1.0"


class GraphAPIError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class MicrosoftGraph:
    def __init__(self, access_token: str, timeout: float = 10):
        if not access_token:
            raise ValueError("A Microsoft Graph access token is required")
        self.access_token = access_token
        self.timeout = timeout

    def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        url = f"{GRAPH_ROOT}/{path.lstrip('/')}"
        return self._get_url(url, params)

    def _get_url(self, url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "graph.microsoft.com"
            or not parsed.path.startswith("/v1.0/")
        ):
            raise GraphAPIError("Microsoft Graph returned an unexpected paging URL")
        try:
            response = requests.get(
                url,
                headers={"Authorization": f"Bearer {self.access_token}"},
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status in {401, 403}:
                raise GraphAPIError(
                    "Microsoft Graph access is missing, expired, or not consented for this tenant",
                    status,
                ) from exc
            raise GraphAPIError("Microsoft Graph could not be reached", status) from exc
        try:
            payload = response.json()
        except requests.JSONDecodeError as exc:
            raise GraphAPIError("Microsoft Graph returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise GraphAPIError("Microsoft Graph returned an unexpected response")
        return payload

    def current_user(self) -> dict[str, str]:
        payload = self._get("me", {"$select": "id,displayName,userPrincipalName"})
        user_id = payload.get("id")
        if not isinstance(user_id, str) or not user_id:
            raise GraphAPIError("Microsoft Graph did not return a user identifier")
        return {
            "id": user_id.lower(),
            "display_name": payload.get("displayName")
            if isinstance(payload.get("displayName"), str)
            else "",
            "user_principal_name": payload.get("userPrincipalName")
            if isinstance(payload.get("userPrincipalName"), str)
            else "",
        }

    def organization(self) -> dict[str, Any]:
        payload = self._get(
            "organization",
            {"$select": "id,displayName,verifiedDomains"},
        )
        values = payload.get("value")
        if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict):
            raise GraphAPIError("Microsoft Graph returned an unexpected organization")
        organization = values[0]
        tenant_id = organization.get("id")
        name = organization.get("displayName")
        domains = organization.get("verifiedDomains")
        if (
            not isinstance(tenant_id, str)
            or not isinstance(name, str)
            or not isinstance(domains, list)
        ):
            raise GraphAPIError("Microsoft Graph returned incomplete organization details")
        verified_domains = sorted({
            domain["name"].casefold()
            for domain in domains
            if isinstance(domain, dict)
            and domain.get("isVerified") is True
            and isinstance(domain.get("name"), str)
        })
        return {
            "tenant_id": tenant_id.lower(),
            "name": name,
            "verified_domains": verified_domains,
        }

    def security_groups(self) -> list[dict[str, str]]:
        return [
            {"id": group_id, "display_name": display_name}
            for group_id, display_name in self._collect_groups(
                "groups",
                {
                    "$filter": "securityEnabled eq true",
                    "$select": "id,displayName,securityEnabled",
                    "$top": "100",
                },
            )
        ]

    def current_user_group_ids(self) -> set[str]:
        return {
            group_id
            for group_id, _display_name in self._collect_groups(
                "me/transitiveMemberOf/microsoft.graph.group",
                {"$select": "id", "$top": "100"},
            )
        }

    def current_user_role_template_ids(self) -> set[str]:
        payload = self._get(
            "me/memberOf/microsoft.graph.directoryRole",
            {"$select": "roleTemplateId", "$top": "100"},
        )
        role_template_ids: set[str] = set()
        while True:
            values = payload.get("value")
            if not isinstance(values, list):
                raise GraphAPIError("Microsoft Graph returned an invalid directory-role list")
            for value in values:
                if not isinstance(value, dict):
                    raise GraphAPIError("Microsoft Graph returned an invalid directory role")
                role_template_id = value.get("roleTemplateId")
                if isinstance(role_template_id, str) and role_template_id:
                    role_template_ids.add(role_template_id.casefold())
            next_link = payload.get("@odata.nextLink")
            if next_link is None:
                break
            if not isinstance(next_link, str):
                raise GraphAPIError("Microsoft Graph returned an invalid paging link")
            payload = self._get_url(next_link)
        return role_template_ids

    def _collect_groups(
        self,
        path: str,
        params: dict[str, str],
    ) -> list[tuple[str, str]]:
        payload = self._get(path, params)
        groups: list[tuple[str, str]] = []
        while True:
            values = payload.get("value")
            if not isinstance(values, list):
                raise GraphAPIError("Microsoft Graph returned an invalid group list")
            for value in values:
                if not isinstance(value, dict):
                    raise GraphAPIError("Microsoft Graph returned an invalid group")
                group_id = value.get("id")
                display_name = value.get("displayName", "")
                if not isinstance(group_id, str) or not group_id:
                    raise GraphAPIError("Microsoft Graph returned a group without an identifier")
                if not isinstance(display_name, str):
                    display_name = ""
                if path == "groups" and value.get("securityEnabled") is not True:
                    continue
                groups.append((group_id.lower(), display_name))
            next_link = payload.get("@odata.nextLink")
            if next_link is None:
                break
            if not isinstance(next_link, str):
                raise GraphAPIError("Microsoft Graph returned an invalid paging link")
            payload = self._get_url(next_link)
        return groups
