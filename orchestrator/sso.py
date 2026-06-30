from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from orchestrator.db.sqlite import AuditRepository, IdentityRepository, MembershipRole, SettingsRepository
from orchestrator.secrets_crypto import SecretEncryptionError, decrypt_secret, encrypt_secret

OIDC_SETTINGS_KEY = "oidc_provider"
SESSION_COOKIE_NAME = "haao_session"
SESSION_TTL_SECONDS = 8 * 60 * 60
ROLE_ORDER: dict[MembershipRole, int] = {"viewer": 0, "member": 1, "admin": 2, "owner": 3}


class OIDCConfigurationError(ValueError):
    """Raised when OIDC is absent or incomplete."""


class OIDCVerificationError(ValueError):
    """Raised when an OIDC token cannot be verified."""


@dataclass(frozen=True)
class OIDCProviderConfig:
    issuer: str
    client_id: str
    client_secret: str
    redirect_uri: str
    authorization_endpoint: str = ""
    token_endpoint: str = ""
    jwks_uri: str = ""
    jwks: dict | None = None
    scopes: list[str] | None = None
    workspace_id: str = "default"
    group_claim: str = "groups"
    role_mapping: dict[str, MembershipRole] | None = None
    default_role: MembershipRole = "member"

    @property
    def configured(self) -> bool:
        return bool(
            self.issuer.strip()
            and self.client_id.strip()
            and self.client_secret.strip()
            and self.redirect_uri.strip()
        )

    def public_dict(self) -> dict[str, object]:
        return {
            "issuer": self.issuer,
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "authorization_endpoint": self.authorization_endpoint,
            "token_endpoint": self.token_endpoint,
            "jwks_uri": self.jwks_uri,
            "scopes": self.scopes or ["openid", "email", "profile"],
            "workspace_id": self.workspace_id,
            "group_claim": self.group_claim,
            "role_mapping": self.role_mapping or {},
            "default_role": self.default_role,
            "configured": self.configured,
            "client_secret_configured": bool(self.client_secret),
        }


@dataclass(frozen=True)
class AuthSession:
    user_id: str
    workspace_id: str
    expires_at: int


class OIDCConfigRepository:
    def __init__(self, settings: SettingsRepository):
        self.settings = settings

    def get(self) -> OIDCProviderConfig | None:
        stored = self.settings.get_json(OIDC_SETTINGS_KEY, default=None)
        if not isinstance(stored, dict):
            return None
        payload = dict(stored)
        secret_ref = payload.pop("client_secret_ref", "")
        client_secret = ""
        if isinstance(secret_ref, str) and secret_ref:
            client_secret = decrypt_secret(secret_ref)
        elif isinstance(payload.get("client_secret"), str):
            client_secret = str(payload.pop("client_secret"))
        return _config_from_dict({**payload, "client_secret": client_secret})

    def set(self, payload: dict[str, object]) -> OIDCProviderConfig:
        existing = self.get()
        config_payload = dict(payload)
        raw_secret = str(config_payload.pop("client_secret", "") or "")
        if raw_secret:
            config_payload["client_secret_ref"] = encrypt_secret(raw_secret)
        elif existing is not None and existing.client_secret:
            existing_stored = self.settings.get_json(OIDC_SETTINGS_KEY, default={})
            if isinstance(existing_stored, dict) and existing_stored.get("client_secret_ref"):
                config_payload["client_secret_ref"] = existing_stored["client_secret_ref"]
        config = _config_from_dict({**config_payload, "client_secret": raw_secret or (existing.client_secret if existing else "")})
        stored = config.public_dict()
        stored.pop("configured", None)
        stored.pop("client_secret_configured", None)
        stored["client_secret_ref"] = config_payload.get("client_secret_ref", "")
        stored["jwks"] = config.jwks
        self.settings.set_json(OIDC_SETTINGS_KEY, stored)
        loaded = self.get()
        if loaded is None:
            raise OIDCConfigurationError("OIDC provider was not saved")
        return loaded


class OIDCService:
    def __init__(
        self,
        *,
        identity: IdentityRepository,
        audit: AuditRepository,
        config: OIDCProviderConfig,
        http_client: httpx.Client | None = None,
    ):
        self.identity = identity
        self.audit = audit
        self.config = config
        self.http = http_client or httpx.Client(timeout=10.0)

    def authorization_url(self, *, workspace_id: str | None = None) -> str:
        self._require_configured()
        endpoint = self.config.authorization_endpoint or _issuer_url(self.config.issuer, "/authorize")
        state = sign_state_token({"workspace_id": workspace_id or self.config.workspace_id})
        params = {
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.config.scopes or ["openid", "email", "profile"]),
            "state": state,
            "nonce": secrets.token_urlsafe(16),
        }
        return f"{endpoint}?{urlencode(params)}"

    def complete_login(
        self,
        *,
        code: str | None = None,
        id_token: str | None = None,
        state: str | None = None,
        workspace_id: str | None = None,
        ip: str | None = None,
    ) -> dict[str, object]:
        self._require_configured()
        target_workspace = workspace_id or self.config.workspace_id
        if state:
            target_workspace = verify_state_token(state).get("workspace_id") or target_workspace
        token = id_token or self._exchange_code_for_id_token(code)
        claims = verify_id_token(token, self.config, http_client=self.http)
        user_id = oidc_user_id(self.config.issuer, str(claims["sub"]))
        self.identity.create_user(
            user_id=user_id,
            email=str(claims.get("email") or ""),
            display_name=str(claims.get("name") or claims.get("preferred_username") or ""),
        )
        role = role_from_claims(claims, self.config)
        membership = self.identity.set_membership(
            user_id=user_id,
            workspace_id=target_workspace,
            role=role,
        )
        session_token = issue_session_token(user_id=user_id, workspace_id=target_workspace)
        self.audit.append(
            actor_id=user_id,
            workspace_id=target_workspace,
            action="auth.login",
            target="oidc",
            payload={"issuer": self.config.issuer, "role": membership.role},
            ip=ip,
        )
        return {
            "session_token": session_token,
            "expires_at": decode_session_token(session_token).expires_at,
            "user": self.identity.get_user(user_id).to_dict(),
            "membership": membership.to_dict(),
        }

    def _exchange_code_for_id_token(self, code: str | None) -> str:
        if not code:
            raise OIDCVerificationError("Missing authorization code")
        endpoint = self.config.token_endpoint or _issuer_url(self.config.issuer, "/token")
        response = self.http.post(
            endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.config.redirect_uri,
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
            },
        )
        response.raise_for_status()
        payload = response.json()
        token = payload.get("id_token")
        if not isinstance(token, str) or not token:
            raise OIDCVerificationError("OIDC token response did not include id_token")
        return token

    def _require_configured(self) -> None:
        if not self.config.configured:
            raise OIDCConfigurationError("OIDC provider is not configured")


def verify_id_token(
    id_token: str,
    config: OIDCProviderConfig,
    *,
    http_client: httpx.Client | None = None,
) -> dict[str, Any]:
    header, claims, signed, signature = _decode_jwt(id_token)
    alg = str(header.get("alg") or "")
    if alg == "HS256":
        expected = hmac.new(config.client_secret.encode("utf-8"), signed, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, signature):
            raise OIDCVerificationError("Invalid id_token signature")
    elif alg == "RS256":
        _verify_rs256(header, signed, signature, config, http_client=http_client)
    else:
        raise OIDCVerificationError(f"Unsupported id_token algorithm: {alg}")
    _validate_claims(claims, config)
    return claims


def role_from_claims(claims: dict[str, Any], config: OIDCProviderConfig) -> MembershipRole:
    mapping = config.role_mapping or {}
    groups = claims.get(config.group_claim)
    values = groups if isinstance(groups, list) else [groups]
    best_role = config.default_role
    for value in values:
        role = mapping.get(str(value))
        if role and ROLE_ORDER[role] > ROLE_ORDER[best_role]:
            best_role = role
    return best_role


def issue_session_token(*, user_id: str, workspace_id: str, ttl_seconds: int = SESSION_TTL_SECONDS) -> str:
    now = int(time.time())
    payload = {
        "typ": "haao-session",
        "sub": user_id,
        "workspace_id": workspace_id,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return _sign_payload("hses", payload)


def decode_session_token(token: str) -> AuthSession:
    payload = _verify_signed_payload("hses", token)
    if payload.get("typ") != "haao-session":
        raise OIDCVerificationError("Invalid session token type")
    exp = int(payload.get("exp") or 0)
    if exp <= int(time.time()):
        raise OIDCVerificationError("Session token expired")
    return AuthSession(
        user_id=str(payload.get("sub") or ""),
        workspace_id=str(payload.get("workspace_id") or "default"),
        expires_at=exp,
    )


def sign_state_token(payload: dict[str, object], ttl_seconds: int = 10 * 60) -> str:
    now = int(time.time())
    return _sign_payload("hstate", {**payload, "typ": "oidc-state", "iat": now, "exp": now + ttl_seconds})


def verify_state_token(token: str) -> dict[str, str]:
    payload = _verify_signed_payload("hstate", token)
    if payload.get("typ") != "oidc-state":
        raise OIDCVerificationError("Invalid OIDC state")
    if int(payload.get("exp") or 0) <= int(time.time()):
        raise OIDCVerificationError("OIDC state expired")
    return {key: str(value) for key, value in payload.items() if isinstance(value, str)}


def oidc_user_id(issuer: str, sub: str) -> str:
    digest = hashlib.sha256(f"{issuer}|{sub}".encode("utf-8")).hexdigest()[:24]
    return f"oidc-{digest}"


def _config_from_dict(payload: dict[str, object]) -> OIDCProviderConfig:
    mapping = payload.get("role_mapping") if isinstance(payload.get("role_mapping"), dict) else {}
    clean_mapping = {
        str(group): _clean_role(role)
        for group, role in dict(mapping).items()
        if _clean_role(role) is not None
    }
    scopes = payload.get("scopes")
    clean_scopes = [str(item) for item in scopes] if isinstance(scopes, list) else ["openid", "email", "profile"]
    return OIDCProviderConfig(
        issuer=str(payload.get("issuer") or "").rstrip("/"),
        client_id=str(payload.get("client_id") or ""),
        client_secret=str(payload.get("client_secret") or ""),
        redirect_uri=str(payload.get("redirect_uri") or ""),
        authorization_endpoint=str(payload.get("authorization_endpoint") or ""),
        token_endpoint=str(payload.get("token_endpoint") or ""),
        jwks_uri=str(payload.get("jwks_uri") or ""),
        jwks=payload.get("jwks") if isinstance(payload.get("jwks"), dict) else None,
        scopes=clean_scopes,
        workspace_id=str(payload.get("workspace_id") or "default"),
        group_claim=str(payload.get("group_claim") or "groups"),
        role_mapping=clean_mapping,
        default_role=_clean_role(payload.get("default_role")) or "member",
    )


def _clean_role(value: object) -> MembershipRole | None:
    role = str(value or "")
    return role if role in ROLE_ORDER else None


def _decode_jwt(token: str) -> tuple[dict[str, Any], dict[str, Any], bytes, bytes]:
    parts = token.split(".")
    if len(parts) != 3:
        raise OIDCVerificationError("Invalid JWT shape")
    header = _loads_b64_json(parts[0])
    claims = _loads_b64_json(parts[1])
    signature = _b64decode(parts[2])
    return header, claims, f"{parts[0]}.{parts[1]}".encode("ascii"), signature


def _validate_claims(claims: dict[str, Any], config: OIDCProviderConfig) -> None:
    if claims.get("iss") != config.issuer:
        raise OIDCVerificationError("Invalid id_token issuer")
    aud = claims.get("aud")
    audiences = aud if isinstance(aud, list) else [aud]
    if config.client_id not in audiences:
        raise OIDCVerificationError("Invalid id_token audience")
    if not claims.get("sub"):
        raise OIDCVerificationError("Missing id_token subject")
    now = int(time.time())
    if int(claims.get("exp") or 0) <= now:
        raise OIDCVerificationError("id_token expired")
    nbf = claims.get("nbf")
    if nbf is not None and int(nbf) > now:
        raise OIDCVerificationError("id_token not yet valid")


def _verify_rs256(
    header: dict[str, Any],
    signed: bytes,
    signature: bytes,
    config: OIDCProviderConfig,
    *,
    http_client: httpx.Client | None,
) -> None:
    key = _select_jwk(header, config, http_client=http_client)
    numbers = rsa.RSAPublicNumbers(
        e=int.from_bytes(_b64decode(str(key["e"])), "big"),
        n=int.from_bytes(_b64decode(str(key["n"])), "big"),
    )
    public_key = numbers.public_key()
    try:
        public_key.verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as exc:
        raise OIDCVerificationError("Invalid id_token signature") from exc


def _select_jwk(
    header: dict[str, Any],
    config: OIDCProviderConfig,
    *,
    http_client: httpx.Client | None,
) -> dict[str, str]:
    jwks = config.jwks or _fetch_jwks(config, http_client=http_client)
    keys = jwks.get("keys") if isinstance(jwks, dict) else None
    if not isinstance(keys, list):
        raise OIDCVerificationError("OIDC JWKS is missing keys")
    kid = header.get("kid")
    for key in keys:
        if isinstance(key, dict) and (kid is None or key.get("kid") == kid) and key.get("kty") == "RSA":
            return {str(k): str(v) for k, v in key.items()}
    raise OIDCVerificationError("No matching OIDC signing key")


def _fetch_jwks(config: OIDCProviderConfig, *, http_client: httpx.Client | None) -> dict:
    uri = config.jwks_uri or _issuer_url(config.issuer, "/.well-known/jwks.json")
    client = http_client or httpx.Client(timeout=10.0)
    try:
        response = client.get(uri)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise OIDCVerificationError(f"Could not fetch OIDC JWKS: {exc}") from exc
    payload = response.json()
    if not isinstance(payload, dict):
        raise OIDCVerificationError("OIDC JWKS response was not an object")
    return payload


def _issuer_url(issuer: str, suffix: str) -> str:
    return issuer.rstrip("/") + suffix


def _sign_payload(prefix: str, payload: dict[str, object]) -> str:
    body = _b64encode(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    signature = hmac.new(_session_secret(), body.encode("ascii"), hashlib.sha256).digest()
    return f"{prefix}.{body}.{_b64encode(signature)}"


def _verify_signed_payload(prefix: str, token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != prefix:
        raise OIDCVerificationError("Invalid signed token shape")
    expected = hmac.new(_session_secret(), parts[1].encode("ascii"), hashlib.sha256).digest()
    actual = _b64decode(parts[2])
    if not hmac.compare_digest(expected, actual):
        raise OIDCVerificationError("Invalid signed token signature")
    payload = json.loads(_b64decode(parts[1]).decode("utf-8"))
    if not isinstance(payload, dict):
        raise OIDCVerificationError("Invalid signed token payload")
    return payload


def _session_secret() -> bytes:
    secret = os.environ.get("HAAO_SECRET_KEY", "").strip()
    if not secret:
        raise SecretEncryptionError("HAAO_SECRET_KEY must be set before issuing sessions")
    return secret.encode("utf-8")


def _loads_b64_json(raw: str) -> dict[str, Any]:
    payload = json.loads(_b64decode(raw).decode("utf-8"))
    if not isinstance(payload, dict):
        raise OIDCVerificationError("JWT section was not an object")
    return payload


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(raw: str) -> bytes:
    padded = raw + "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))
