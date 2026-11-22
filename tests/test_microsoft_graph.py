import pytest
import requests

from docqa.microsoft_graph import GraphAPIError, MicrosoftGraph


class Response:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.content = b"response"

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.HTTPError("request failed")
            error.response = self
            raise error

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def test_organization_returns_only_verified_domains(monkeypatch):
    def fake_get(url, **kwargs):
        assert url.endswith("/organization")
        return Response({
            "value": [{
                "id": "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA",
                "displayName": "Acme",
                "verifiedDomains": [
                    {"name": "acme.example", "isVerified": True},
                    {"name": "unverified.example", "isVerified": False},
                ],
            }]
        })

    monkeypatch.setattr("docqa.microsoft_graph.requests.get", fake_get)
    graph = MicrosoftGraph("graph-token")

    assert graph.organization() == {
        "tenant_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "name": "Acme",
        "verified_domains": ["acme.example"],
    }


def test_security_groups_are_paged_and_filtered(monkeypatch):
    urls = []

    def fake_get(url, **kwargs):
        urls.append(url)
        if len(urls) == 1:
            return Response({
                "value": [
                    {
                        "id": "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA",
                        "displayName": "Finance",
                        "securityEnabled": True,
                    },
                    {
                        "id": "BBBBBBBB-BBBB-4BBB-8BBB-BBBBBBBBBBBB",
                        "displayName": "Mail",
                        "securityEnabled": False,
                    },
                ],
                "@odata.nextLink": (
                    "https://graph.microsoft.com/v1.0/groups?$skiptoken=next"
                ),
            })
        return Response({
            "value": [{
                "id": "CCCCCCCC-CCCC-4CCC-8CCC-CCCCCCCCCCCC",
                "displayName": "Operations",
                "securityEnabled": True,
            }]
        })

    monkeypatch.setattr("docqa.microsoft_graph.requests.get", fake_get)
    groups = MicrosoftGraph("graph-token").security_groups()

    assert groups == [
        {"id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "display_name": "Finance"},
        {"id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc", "display_name": "Operations"},
    ]
    assert len(urls) == 2


def test_graph_rejects_non_graph_paging_url(monkeypatch):
    def fake_get(url, **kwargs):
        return Response({
            "value": [],
            "@odata.nextLink": "https://attacker.example/groups?page=2",
        })

    monkeypatch.setattr("docqa.microsoft_graph.requests.get", fake_get)

    with pytest.raises(GraphAPIError, match="unexpected paging URL"):
        MicrosoftGraph("graph-token").security_groups()


def test_graph_permission_errors_are_actionable(monkeypatch):
    def fake_get(url, **kwargs):
        return Response({"error": "forbidden"}, status_code=403)

    monkeypatch.setattr("docqa.microsoft_graph.requests.get", fake_get)

    with pytest.raises(GraphAPIError, match="not consented") as error:
        MicrosoftGraph("graph-token").organization()

    assert error.value.status_code == 403


def test_current_user_roles_returns_stable_role_template_ids(monkeypatch):
    def fake_get(url, **kwargs):
        assert url.endswith("/me/memberOf/microsoft.graph.directoryRole")
        return Response({
            "value": [
                {"roleTemplateId": "62E90394-69F5-4237-9190-012177145E10"},
                {"roleTemplateId": "9B895D92-2CD3-44C7-9D02-A6AC2D5EA5C3"},
            ]
        })

    monkeypatch.setattr("docqa.microsoft_graph.requests.get", fake_get)

    assert MicrosoftGraph("graph-token").current_user_role_template_ids() == {
        "62e90394-69f5-4237-9190-012177145e10",
        "9b895d92-2cd3-44c7-9d02-a6ac2d5ea5c3",
    }


def test_current_user_groups_include_transitive_memberships(monkeypatch):
    def fake_get(url, **kwargs):
        assert url.endswith("/me/transitiveMemberOf/microsoft.graph.group")
        return Response({
            "value": [{
                "id": "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA",
                "displayName": "Nested Finance",
            }]
        })

    monkeypatch.setattr("docqa.microsoft_graph.requests.get", fake_get)

    assert MicrosoftGraph("graph-token").current_user_group_ids() == {
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    }
