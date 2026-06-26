from __future__ import annotations

import base64
import json

import pytest

from orchestrator.secrets_crypto import SecretEncryptionError, decrypt_secret, encrypt_secret


LEGACY_V1_SECRET = (
    "eyJjaXBoZXJ0ZXh0IjogImZydmFmMkdxcUVucmJCZjJfaXRBbFFjcGZBPT0iLCAibWFjIjog"
    "InBjb3BCRXhaMzJDd21ONi0ySk4yM1MyZ1ZxT3RBTlZnSGVPYmZNMzlza0k9IiwgIm5vbmNl"
    "IjogIjVReG1vWTRzcmRZcXdBeHpNV1dUb1E9PSIsICJzYWx0IjogIktEZlU0R2JTMDhSTjdJ"
    "ZncxTU1faEE9PSIsICJ2IjogImhhYW8ta2V5LXYxIn0="
)


def test_encrypt_secret_writes_v2_and_round_trips(monkeypatch) -> None:
    monkeypatch.setenv("HAAO_SECRET_KEY", "master-secret")

    encrypted = encrypt_secret("nonstandard-provider-token")
    payload = json.loads(base64.urlsafe_b64decode(encrypted.encode("ascii")).decode("utf-8"))

    assert payload["v"] == "haao-key-v2"
    assert decrypt_secret(encrypted) == "nonstandard-provider-token"


def test_decrypt_secret_reads_legacy_v1(monkeypatch) -> None:
    monkeypatch.setenv("HAAO_SECRET_KEY", "master-secret")

    assert decrypt_secret(LEGACY_V1_SECRET) == "legacy-secret-value"


def test_decrypt_secret_rejects_wrong_key(monkeypatch) -> None:
    monkeypatch.setenv("HAAO_SECRET_KEY", "master-secret")
    encrypted = encrypt_secret("nonstandard-provider-token")
    monkeypatch.setenv("HAAO_SECRET_KEY", "wrong-secret")

    with pytest.raises(SecretEncryptionError):
        decrypt_secret(encrypted)


def test_decrypt_secret_rejects_tampered_v2_ciphertext(monkeypatch) -> None:
    monkeypatch.setenv("HAAO_SECRET_KEY", "master-secret")
    encrypted = encrypt_secret("nonstandard-provider-token")
    payload = json.loads(base64.urlsafe_b64decode(encrypted.encode("ascii")).decode("utf-8"))
    ciphertext = bytearray(base64.urlsafe_b64decode(payload["ciphertext"].encode("ascii")))
    ciphertext[0] ^= 1
    payload["ciphertext"] = base64.urlsafe_b64encode(bytes(ciphertext)).decode("ascii")
    tampered = base64.urlsafe_b64encode(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).decode("ascii")

    with pytest.raises(SecretEncryptionError):
        decrypt_secret(tampered)
