from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


SECRET_ENV_VAR = "HAAO_SECRET_KEY"
LEGACY_ENCRYPTION_VERSION = "haao-key-v1"
ENCRYPTION_VERSION = "haao-key-v2"
PBKDF2_ITERATIONS = 200_000


class SecretEncryptionError(ValueError):
    """Raised when an encrypted secret cannot be stored or opened."""


def encrypt_secret(value: str) -> str:
    secret = _master_secret()
    if not secret:
        raise SecretEncryptionError(f"{SECRET_ENV_VAR} must be set before storing encrypted secrets")
    return _encrypt(value, secret)


def decrypt_secret(value: str) -> str:
    secret = _master_secret()
    if not secret:
        raise SecretEncryptionError(f"{SECRET_ENV_VAR} must be set to use encrypted secrets")
    return _decrypt(value, secret)


def _master_secret() -> str:
    return os.environ.get(SECRET_ENV_VAR, "").strip()


def _encrypt(api_key: str, secret: str) -> str:
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = _derive_key(secret, salt)
    encrypted = AESGCM(key).encrypt(nonce, api_key.encode("utf-8"), None)
    ciphertext, tag = encrypted[:-16], encrypted[-16:]
    payload = {
        "v": ENCRYPTION_VERSION,
        "salt": _b64(salt),
        "nonce": _b64(nonce),
        "ciphertext": _b64(ciphertext),
        "tag": _b64(tag),
    }
    return base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True).encode("utf-8")).decode("ascii")


def _decrypt(key_ref: str, secret: str) -> str:
    try:
        payload = json.loads(base64.urlsafe_b64decode(key_ref.encode("ascii")).decode("utf-8"))
        version = payload.get("v")
        if version == ENCRYPTION_VERSION:
            return _decrypt_v2(payload, secret)
        if version == LEGACY_ENCRYPTION_VERSION:
            return _decrypt_v1(payload, secret)
        raise ValueError("unsupported key version")
    except SecretEncryptionError:
        raise
    except Exception as exc:
        raise SecretEncryptionError("Stored encrypted secret is invalid") from exc


def _decrypt_v2(payload: dict, secret: str) -> str:
    try:
        salt = _unb64(payload["salt"])
        nonce = _unb64(payload["nonce"])
        ciphertext = _unb64(payload["ciphertext"])
        tag = _unb64(payload["tag"])
        key = _derive_key(secret, salt)
        plaintext = AESGCM(key).decrypt(nonce, ciphertext + tag, None)
        return plaintext.decode("utf-8")
    except (InvalidTag, UnicodeDecodeError) as exc:
        raise SecretEncryptionError("Stored encrypted secret could not be decrypted") from exc
    except Exception as exc:
        raise SecretEncryptionError("Stored encrypted secret is invalid") from exc


def _decrypt_v1(payload: dict, secret: str) -> str:
    try:
        if payload.get("v") != LEGACY_ENCRYPTION_VERSION:
            raise ValueError("unsupported key version")
        salt = _unb64(payload["salt"])
        nonce = _unb64(payload["nonce"])
        ciphertext = _unb64(payload["ciphertext"])
        expected_mac = _unb64(payload["mac"])
    except Exception as exc:
        raise SecretEncryptionError("Stored encrypted secret is invalid") from exc

    key = _derive_key(secret, salt)
    actual_mac = hmac.new(key, salt + nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(actual_mac, expected_mac):
        raise SecretEncryptionError("Stored encrypted secret could not be decrypted")
    return _xor_with_stream(ciphertext, key, nonce).decode("utf-8")


def _derive_key(secret: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256",
        secret.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
        dklen=32,
    )


def _xor_with_stream(data: bytes, key: bytes, nonce: bytes) -> bytes:
    output = bytearray()
    counter = 0
    while len(output) < len(data):
        block = hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        output.extend(block)
        counter += 1
    return bytes(value ^ stream for value, stream in zip(data, output))


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data.encode("ascii"))
