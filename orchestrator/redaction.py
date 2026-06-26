from __future__ import annotations

import os
import re
from collections.abc import Iterable

from orchestrator.secrets_crypto import SecretEncryptionError, decrypt_secret


REDACTION = "***redacted***"

KEY_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{12,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{8,}\b"),
)


def redact_text(value: object, *, extra_secrets: Iterable[str] | None = None) -> str:
    text = "" if value is None else str(value)
    for pattern in KEY_PATTERNS:
        text = pattern.sub(REDACTION, text)
    for secret in _secrets(extra_secrets):
        text = text.replace(secret, REDACTION)
    return text


def redact_json(value: object, *, extra_secrets: Iterable[str] | None = None) -> object:
    if isinstance(value, str):
        return redact_text(value, extra_secrets=extra_secrets)
    if isinstance(value, list):
        return [redact_json(item, extra_secrets=extra_secrets) for item in value]
    if isinstance(value, dict):
        return {
            str(key): redact_json(item, extra_secrets=extra_secrets)
            for key, item in value.items()
        }
    return value


def current_known_secrets(settings: object, settings_repository: object | None) -> set[str]:
    secrets: set[str] = set()
    for attr in (
        "claude_api_key",
        "openai_api_key",
        "gemini_api_key",
        "haao_api_token",
    ):
        value = getattr(settings, attr, "")
        if isinstance(value, str) and value:
            secrets.add(value)

    if settings_repository is None:
        return secrets

    for key_ref in _stored_cloud_model_key_refs(settings_repository):
        try:
            secrets.add(decrypt_secret(key_ref))
        except SecretEncryptionError:
            continue

    for encrypted_token in _stored_integration_token_refs(settings_repository):
        try:
            secrets.add(decrypt_secret(encrypted_token))
        except SecretEncryptionError:
            continue

    return secrets


def _secrets(extra_secrets: Iterable[str] | None) -> list[str]:
    candidates = [os.environ.get("HAAO_SECRET_KEY", "")]
    candidates.extend(extra_secrets or [])
    return sorted(
        {
            item
            for item in candidates
            if isinstance(item, str) and len(item) >= 8
        },
        key=len,
        reverse=True,
    )


def _stored_cloud_model_key_refs(settings_repository: object) -> list[str]:
    get_json = getattr(settings_repository, "get_json", None)
    if not callable(get_json):
        return []
    try:
        stored = get_json("cloud_models", [])
    except Exception:
        return []
    if not isinstance(stored, list):
        return []
    refs: list[str] = []
    for item in stored:
        if not isinstance(item, dict):
            continue
        key_ref = item.get("key_ref")
        if isinstance(key_ref, str) and key_ref:
            refs.append(key_ref)
    return refs


def _stored_integration_token_refs(settings_repository: object) -> list[str]:
    connection = getattr(settings_repository, "connection", None)
    execute = getattr(connection, "execute", None)
    if not callable(execute):
        return []
    try:
        rows = execute("SELECT encrypted_token FROM integrations").fetchall()
    except Exception:
        return []
    refs: list[str] = []
    for row in rows:
        try:
            encrypted = row["encrypted_token"]
        except Exception:
            continue
        if isinstance(encrypted, str) and encrypted:
            refs.append(encrypted)
    return refs
