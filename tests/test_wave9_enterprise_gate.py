from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestrator.config import get_settings
from orchestrator.db.sqlite import AuditRepository, IdentityRepository, SettingsRepository, connect
from orchestrator.main import app
from orchestrator.sso import OIDCConfigRepository, issue_session_token


@pytest.fixture(autouse=True)
def clear_settings_cache_after_test():
    yield
    get_settings.cache_clear()


def test_oidc_login_maps_user_membership_session_logout_and_audit(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "haao.sqlite3"
    _configure_env(monkeypatch, db_path)
    client = TestClient(app)
    admin_headers = {"Authorization": "Bearer team-token"}

    configured = client.put(
        "/api/auth/oidc",
        headers=admin_headers,
        json={
            "issuer": "https://idp.example.test",
            "client_id": "haao-client",
            "client_secret": "oidc-secret",
            "redirect_uri": "http://localhost/auth/oidc/callback",
            "authorization_endpoint": "https://idp.example.test/authorize",
            "token_endpoint": "https://idp.example.test/token",
            "workspace_id": "default",
            "group_claim": "groups",
            "role_mapping": {"haao-admins": "admin"},
            "default_role": "member",
        },
    )
    assert configured.status_code == 200
    assert configured.json()["provider"]["client_secret_configured"] is True

    id_token = _hs256_jwt(
        {
            "iss": "https://idp.example.test",
            "aud": "haao-client",
            "sub": "user-123",
            "email": "dev@example.test",
            "name": "Dev User",
            "groups": ["haao-admins"],
            "exp": int(time.time()) + 600,
        },
        "oidc-secret",
    )
    callback = client.post("/api/auth/oidc/callback", json={"id_token": id_token})
    assert callback.status_code == 200
    payload = callback.json()
    assert payload["user"]["email"] == "dev@example.test"
    assert payload["membership"]["role"] == "admin"
    session_headers = {"X-HAAO-Session": payload["session_token"]}
    usage = client.get("/api/workspace/usage", headers=session_headers)
    assert usage.status_code == 200
    assert usage.json()["seats_used"] == 1

    logout = client.post("/api/auth/logout", headers=session_headers)
    assert logout.status_code == 200
    assert logout.json() == {"ok": True}
    actions = [event.action for event in AuditRepository(connect(db_path)).list(workspace_id="default")]
    assert "auth.login" in actions
    assert "auth.logout" in actions

    member_token = _hs256_jwt(
        {
            "iss": "https://idp.example.test",
            "aud": "haao-client",
            "sub": "user-456",
            "email": "member@example.test",
            "exp": int(time.time()) + 600,
        },
        "oidc-secret",
    )
    member_login = client.post("/api/auth/oidc/callback", json={"id_token": member_token})
    assert member_login.status_code == 200
    assert member_login.json()["membership"]["role"] == "member"

    expired = issue_session_token(user_id=payload["user"]["id"], workspace_id="default", ttl_seconds=-1)
    expired_response = client.get("/api/workspace/usage", headers={"X-HAAO-Session": expired})
    assert expired_response.status_code == 401


def test_oidc_absent_fallback_stays_unchanged(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "haao.sqlite3"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.delenv("HAAO_API_TOKEN", raising=False)
    monkeypatch.setenv("HAAO_SECRET_KEY", "test-secret")
    get_settings.cache_clear()

    client = TestClient(app)
    login = client.get("/api/auth/oidc/login")
    assert login.status_code == 404
    usage = client.get("/api/workspace/usage")
    assert usage.status_code == 200
    assert usage.json() == {"seats_used": 0, "seat_limit": None, "plan": "self-host"}


def test_stray_membership_does_not_disable_implicit_owner_mode(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "haao.sqlite3"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.delenv("HAAO_API_TOKEN", raising=False)
    monkeypatch.setenv("HAAO_SECRET_KEY", "test-secret")
    get_settings.cache_clear()

    connection = connect(db_path)
    IdentityRepository(connection).set_membership(user_id="sso-test-user", workspace_id="default", role="viewer")

    client = TestClient(app)
    usage = client.get("/api/workspace/usage")
    admin_write = client.post(
        "/api/config/integrations",
        json={"provider": "github", "token": "pat", "scopes": ["repo"], "label": "GitHub"},
    )

    assert usage.status_code == 200
    assert usage.json()["seats_used"] == 1
    assert admin_write.status_code == 200


def test_oidc_configured_requires_login_and_accepts_session(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "haao.sqlite3"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.delenv("HAAO_API_TOKEN", raising=False)
    monkeypatch.setenv("HAAO_SECRET_KEY", "test-secret")
    get_settings.cache_clear()

    connection = connect(db_path)
    settings_repository = SettingsRepository(connection)
    OIDCConfigRepository(settings_repository).set(
        {
            "issuer": "https://idp.example.test",
            "client_id": "haao-client",
            "client_secret": "oidc-secret",
            "redirect_uri": "http://localhost/auth/oidc/callback",
            "workspace_id": "default",
        }
    )
    IdentityRepository(connection).set_membership(user_id="oidc-user", workspace_id="default", role="owner")

    client = TestClient(app)
    missing = client.get("/api/workspace/usage")
    session_token = issue_session_token(user_id="oidc-user", workspace_id="default")
    allowed = client.get("/api/workspace/usage", headers={"X-HAAO-Session": session_token})

    assert missing.status_code == 401
    assert missing.json()["reason"] == "login_required"
    assert missing.headers["www-authenticate"] == 'Bearer realm="haao"'
    assert allowed.status_code == 200
    assert allowed.json()["seats_used"] == 1


def test_retention_policy_round_trip_purge_idempotent_audited_and_null_keep(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "haao.sqlite3"
    _configure_env(monkeypatch, db_path)
    connection = connect(db_path)
    IdentityRepository(connection).set_membership(user_id="admin", workspace_id="default", role="admin")
    old_attachment = tmp_path / "old.txt"
    old_attachment.write_text("secret attachment", encoding="utf-8")
    _seed_retention_rows(connection, old_attachment)
    client = TestClient(app)

    policy = client.put(
        "/api/retention",
        headers=_headers("admin"),
        json={
            "run_events_days": 1,
            "ticket_logs_days": 1,
            "diffs_days": 1,
            "prompts_days": 1,
            "attachments_days": 1,
        },
    )
    assert policy.status_code == 200
    assert policy.json()["policy"]["run_events_days"] == 1
    assert client.get("/api/retention", headers=_headers("admin")).json()["policy"] == policy.json()["policy"]

    purged = client.post("/api/retention/purge", headers=_headers("admin"))
    assert purged.status_code == 200
    counts = purged.json()["counts"]
    assert counts["run_events_deleted"] == 1
    assert counts["ticket_logs_deleted"] == 1
    assert counts["ticket_diffs_redacted"] == 1
    assert counts["requirement_prompts_redacted"] == 1
    assert counts["chat_messages_redacted"] == 1
    assert counts["attachments_deleted"] == 1
    assert not old_attachment.exists()

    second = client.post("/api/retention/purge", headers=_headers("admin"))
    assert second.status_code == 200
    assert all(value == 0 for value in second.json()["counts"].values())
    actions = [event.action for event in AuditRepository(connect(db_path)).list(workspace_id="default")]
    assert actions.count("retention.purge") == 2

    connection = connect(db_path)
    assert connection.execute("SELECT COUNT(*) AS n FROM run_events").fetchone()["n"] == 1
    assert connection.execute("SELECT COUNT(*) AS n FROM ticket_logs").fetchone()["n"] == 1
    assert "redacted by retention policy" in connection.execute(
        "SELECT ticket_json FROM tickets WHERE id = 'T-old'"
    ).fetchone()["ticket_json"]

    _seed_keep_rows(connection)
    keep = client.put(
        "/api/retention",
        headers=_headers("admin"),
        json={
            "run_events_days": None,
            "ticket_logs_days": None,
            "diffs_days": None,
            "prompts_days": None,
            "attachments_days": None,
        },
    )
    assert keep.status_code == 200
    keep_purge = client.post("/api/retention/purge", headers=_headers("admin"))
    assert keep_purge.status_code == 200
    assert all(value == 0 for value in keep_purge.json()["counts"].values())
    assert connection.execute("SELECT COUNT(*) AS n FROM run_events WHERE id = 50").fetchone()["n"] == 1


def test_seat_usage_and_limit_enforcement_self_host_unaffected(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "haao.sqlite3"
    _configure_env(monkeypatch, db_path)
    connection = connect(db_path)
    identity = IdentityRepository(connection)
    identity.create_workspace(workspace_id="team", name="Team", seat_limit=1, plan="team")
    identity.set_membership(user_id="owner", workspace_id="team", role="owner")
    client = TestClient(app)

    usage = client.get("/api/workspace/usage?workspace=team", headers=_headers("owner", workspace="team"))
    assert usage.status_code == 200
    assert usage.json() == {"seats_used": 1, "seat_limit": 1, "plan": "team"}

    blocked = client.post(
        "/api/memberships",
        headers=_headers("owner", workspace="team"),
        json={"user_id": "second", "workspace_id": "team", "role": "member"},
    )
    assert blocked.status_code == 402
    assert "seat limit reached" in blocked.json()["detail"]

    updated_owner = client.post(
        "/api/memberships",
        headers=_headers("owner", workspace="team"),
        json={"user_id": "owner", "workspace_id": "team", "role": "admin"},
    )
    assert updated_owner.status_code == 200
    assert updated_owner.json()["membership"]["role"] == "admin"

    identity.set_membership(user_id="local-a", workspace_id="default", role="owner")
    identity.set_membership(user_id="local-b", workspace_id="default", role="member")
    default_usage = client.get("/api/workspace/usage", headers=_headers("local-a"))
    assert default_usage.status_code == 200
    assert default_usage.json()["seats_used"] == 2
    assert default_usage.json()["seat_limit"] is None


def _configure_env(monkeypatch, db_path: Path) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("HAAO_API_TOKEN", "team-token")
    monkeypatch.setenv("HAAO_SECRET_KEY", "test-secret")
    get_settings.cache_clear()


def _headers(user_id: str, *, workspace: str = "default") -> dict[str, str]:
    return {
        "Authorization": "Bearer team-token",
        "X-HAAO-User-Id": user_id,
        "X-HAAO-Workspace-Id": workspace,
    }


def _hs256_jwt(claims: dict, secret: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = f"{_b64_json(header)}.{_b64_json(claims)}"
    signature = hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{_b64(signature)}"


def _b64_json(payload: dict) -> str:
    return _b64(json.dumps(payload, sort_keys=True).encode("utf-8"))


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _seed_retention_rows(connection, old_attachment: Path) -> None:
    connection.execute(
        """
        INSERT INTO run_events (id, project_id, event_type, ts, payload_json)
        VALUES (1, 'default', 'model_call', '2020-01-01T00:00:00+00:00', '{}')
        """
    )
    connection.execute(
        """
        INSERT INTO run_events (id, project_id, event_type, ts, payload_json)
        VALUES (2, 'default', 'model_call', '2999-01-01T00:00:00+00:00', '{}')
        """
    )
    connection.execute(
        """
        INSERT INTO ticket_logs (ticket_id, project_id, ts, level, message)
        VALUES ('T-old', 'default', '2020-01-01T00:00:00+00:00', 'info', 'old')
        """
    )
    connection.execute(
        """
        INSERT INTO ticket_logs (ticket_id, project_id, ts, level, message)
        VALUES ('T-new', 'default', '2999-01-01T00:00:00+00:00', 'info', 'new')
        """
    )
    connection.execute(
        """
        INSERT INTO tickets (id, project_id, status, ticket_json, created_at, updated_at)
        VALUES (?, 'default', 'done', ?, '2020-01-01T00:00:00+00:00', '2020-01-01T00:00:00+00:00')
        """,
        ("T-old", json.dumps({"id": "T-old", "status": "done", "result": {"diff": "secret diff"}})),
    )
    connection.execute(
        """
        INSERT INTO requirements (id, project_id, status, requirement_json, created_at, updated_at)
        VALUES (?, 'default', 'backlog', ?, '2020-01-01T00:00:00+00:00', '2020-01-01T00:00:00+00:00')
        """,
        ("R-old", json.dumps({"id": "R-old", "status": "backlog", "prompt": "secret prompt"})),
    )
    connection.execute(
        """
        INSERT INTO chat_messages (id, project_id, role, text, segment_id, created_at)
        VALUES ('CM-old', 'default', 'user', 'secret chat', 'CS-old', '2020-01-01T00:00:00+00:00')
        """
    )
    connection.execute(
        """
        INSERT INTO chat_attachments (id, project_id, filename, mime, size, kind, stored_path, created_at)
        VALUES ('CA-old', 'default', 'old.txt', 'text/plain', 10, 'file', ?, '2020-01-01T00:00:00+00:00')
        """,
        (str(old_attachment),),
    )
    connection.commit()


def _seed_keep_rows(connection) -> None:
    connection.execute(
        """
        INSERT INTO run_events (id, project_id, event_type, ts, payload_json)
        VALUES (50, 'default', 'model_call', '2020-01-01T00:00:00+00:00', '{}')
        """
    )
    connection.commit()
