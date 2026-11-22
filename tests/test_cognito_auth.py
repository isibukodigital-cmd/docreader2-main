import json
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from docqa.auth import (
    AuthenticationError,
    CognitoAuthVerifier,
    IdentityProviderUnavailable,
)

REGION = "eu-north-1"
POOL_ID = "eu-north-1_example"
CLIENT_ID = "test-client"
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{POOL_ID}"
SUBJECT = "123e4567-e89b-42d3-a456-426614174000"


class JsonResponse:
    def __init__(self, value):
        self.value = value

    def raise_for_status(self):
        pass

    def json(self):
        return self.value


def _tokens(subject=SUBJECT, identity_subject=None, identity_claims=None):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(
        jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key())
    )
    public_jwk.update({"kid": "test-key", "use": "sig", "alg": "RS256"})
    now = datetime.now(timezone.utc)
    shared = {
        "iss": ISSUER,
        "sub": subject,
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    access = jwt.encode(
        {
            **shared,
            "token_use": "access",
            "client_id": CLIENT_ID,
            "scope": "openid email profile",
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )
    identity = jwt.encode(
        {
            **shared,
            "sub": identity_subject or subject,
            "token_use": "id",
            "aud": CLIENT_ID,
            "email": "user@example.com",
            "email_verified": True,
            **(identity_claims or {}),
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )
    return access, identity, public_jwk


def test_cognito_verifier_checks_both_tokens_and_maps_microsoft_identity(monkeypatch):
    access, identity, jwk = _tokens(identity_claims={
        "identities": [{"providerName": "Microsoft", "userId": "microsoft-object-id"}],
        "custom:tenant_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "custom:microsoft_user_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    })
    import docqa.auth as auth_module

    monkeypatch.setattr(
        auth_module.requests,
        "get",
        lambda *_args, **_kwargs: JsonResponse({"keys": [jwk]}),
    )
    verifier = CognitoAuthVerifier(REGION, POOL_ID, CLIENT_ID)

    user = verifier.authenticate_user(access, identity)

    assert user.user_id == SUBJECT
    assert user.provider == "azure"
    assert user.microsoft_tenant_id == "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    assert user.microsoft_user_id == "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    assert user.auth_provider == "cognito"
    assert user.email_verified is True
    assert user.app_metadata == {}


def test_cognito_verifier_rejects_mismatched_token_subjects(monkeypatch):
    access, identity, jwk = _tokens(
        identity_subject="223e4567-e89b-42d3-a456-426614174000"
    )
    import docqa.auth as auth_module

    monkeypatch.setattr(
        auth_module.requests,
        "get",
        lambda *_args, **_kwargs: JsonResponse({"keys": [jwk]}),
    )

    with pytest.raises(AuthenticationError, match="different users"):
        CognitoAuthVerifier(REGION, POOL_ID, CLIENT_ID).authenticate_user(
            access, identity
        )


def test_cognito_verifier_reports_jwks_outage(monkeypatch):
    import docqa.auth as auth_module

    access, identity, _ = _tokens()
    def unavailable(*_args, **_kwargs):
        raise auth_module.requests.ConnectionError("offline")

    monkeypatch.setattr(auth_module.requests, "get", unavailable)
    verifier = CognitoAuthVerifier(REGION, POOL_ID, CLIENT_ID)

    with pytest.raises(IdentityProviderUnavailable):
        verifier.authenticate_user(access, identity)
