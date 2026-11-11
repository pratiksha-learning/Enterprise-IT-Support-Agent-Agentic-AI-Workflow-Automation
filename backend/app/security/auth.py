"""Authentication: turns a bearer token into a ``Principal``.

* ``MockTokenService`` - HS256 tokens for local demo personas (AUTH_MODE=mock only).
* ``EntraTokenValidator`` - validates Microsoft Entra ID v2 access tokens (RS256 via JWKS),
  checking signature, issuer, audience, expiry and mapping app roles.

Authentication never decides *what* a caller may do - that is ``rbac``'s job.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import jwt

from app.errors import AuthenticationError
from app.security.principal import Principal
from app.security.rbac import ENTRA_ROLE_MAP

MOCK_ISSUER = "urn:it-support-agent:mock-idp"
MOCK_AUDIENCE = "urn:it-support-agent:api"


class MockDirectory:
    """Local stand-in for Entra ID users (also used by the mock identity provider)."""

    def __init__(self, path: Path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.users: dict[str, dict] = {u["id"]: u for u in data["users"]}

    def get(self, user_id: str) -> dict | None:
        return self.users.get(user_id)

    def list(self) -> list[dict]:
        return list(self.users.values())


class MockTokenService:
    def __init__(self, secret: str, directory: MockDirectory, ttl_minutes: int = 120):
        if len(secret) < 32:
            raise ValueError("MOCK_JWT_SECRET must be at least 32 characters")
        self._secret = secret
        self._directory = directory
        self._ttl = ttl_minutes * 60

    def issue(self, user_id: str) -> tuple[str, int, Principal]:
        user = self._directory.get(user_id)
        if not user or not user.get("enabled", True):
            raise AuthenticationError("Unknown or disabled demo user", code="UNKNOWN_USER")
        now = int(time.time())
        claims = {
            "iss": MOCK_ISSUER, "aud": MOCK_AUDIENCE, "iat": now, "nbf": now, "exp": now + self._ttl,
            "oid": user["id"], "preferred_username": user["upn"], "name": user["display_name"],
            "roles": user.get("roles", []), "groups": user.get("groups", []),
        }
        token = jwt.encode(claims, self._secret, algorithm="HS256")
        return token, self._ttl, self._to_principal(claims)

    def validate(self, token: str) -> Principal:
        try:
            claims = jwt.decode(token, self._secret, algorithms=["HS256"], audience=MOCK_AUDIENCE,
                                issuer=MOCK_ISSUER, options={"require": ["exp", "iat", "oid"]})
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("Token expired", code="TOKEN_EXPIRED") from exc
        except jwt.PyJWTError as exc:
            raise AuthenticationError("Invalid token", code="INVALID_TOKEN") from exc
        user = self._directory.get(claims["oid"])
        if not user or not user.get("enabled", True):
            raise AuthenticationError("User disabled", code="USER_DISABLED")
        return self._to_principal(claims)

    @staticmethod
    def _to_principal(claims: dict) -> Principal:
        return Principal(id=claims["oid"], upn=claims["preferred_username"], display_name=claims["name"],
                         roles=frozenset(claims.get("roles", [])), groups=frozenset(claims.get("groups", [])))


class EntraTokenValidator:
    """Validates Entra ID access tokens issued for this API (v2 endpoint)."""

    def __init__(self, tenant_id: str, audience: str, allowed_callback_app_ids: set[str] | None = None,
                 jwks_client: "jwt.PyJWKClient | None" = None):
        self.tenant_id = tenant_id
        self.audience = audience
        self.issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
        self.allowed_callback_app_ids = allowed_callback_app_ids or set()
        self._jwks = jwks_client or jwt.PyJWKClient(
            f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys", cache_keys=True, lifespan=3600)

    def validate(self, token: str) -> Principal:
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(token, signing_key.key, algorithms=["RS256"], audience=self.audience,
                                issuer=self.issuer, options={"require": ["exp", "iat", "oid", "tid"]})
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("Token expired", code="TOKEN_EXPIRED") from exc
        except jwt.PyJWTError as exc:
            raise AuthenticationError("Invalid token", code="INVALID_TOKEN") from exc
        if claims.get("tid") != self.tenant_id:
            raise AuthenticationError("Token from unexpected tenant", code="INVALID_TENANT")
        roles = frozenset(ENTRA_ROLE_MAP[r] for r in claims.get("roles", []) if r in ENTRA_ROLE_MAP)
        app_id = claims.get("azp") or claims.get("appid")
        is_app_only = claims.get("idtyp") == "app" or ("scp" not in claims and "upn" not in claims
                                                       and "preferred_username" not in claims)
        if is_app_only:
            # Only explicitly allow-listed service principals (the approval flow) are accepted.
            if app_id not in self.allowed_callback_app_ids:
                raise AuthenticationError("Application not allowed", code="APP_NOT_ALLOWED")
            return Principal(id=claims["oid"], upn=app_id or "service", display_name="service",
                             roles=roles, auth_type="service", app_id=app_id)
        if "access_as_user" not in str(claims.get("scp", "")).split():
            raise AuthenticationError("Missing required scope", code="INSUFFICIENT_SCOPE")
        return Principal(id=claims["oid"], upn=claims.get("preferred_username") or claims.get("upn", ""),
                         display_name=claims.get("name", ""), roles=roles or frozenset({"employee"}),
                         groups=frozenset(claims.get("groups", [])), app_id=app_id)
