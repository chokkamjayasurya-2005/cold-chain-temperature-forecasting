import hashlib
import json
import os
import secrets
import smtplib
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend import database, email_delivery
from backend.app import app
from backend.auth import hash_password, verify_password


@contextmanager
def authenticated_client(database_path, role="ADMIN", username=None):
    previous_path = os.environ.get("DATABASE_PATH")
    username = username or f"test-{role.lower()}"
    try:
        os.environ["DATABASE_PATH"] = str(database_path)
        database.initialize_database()
        if database.get_user_by_username(username) is None:
            database.create_user(
                username,
                None,
                hash_password("AdminStrongPassword1!"),
                role,
            )
        with TestClient(app) as client:
            csrf = client.get("/auth/csrf").json()["csrf_token"]
            login = client.post(
                "/auth/login",
                json={"username": username, "password": "AdminStrongPassword1!"},
                headers={"X-CSRF-Token": csrf},
            )
            assert login.status_code == 200
            client.headers.update({"X-CSRF-Token": login.json()["csrf_token"]})
            yield client
    finally:
        if previous_path is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = previous_path


def _client(tmp_path, monkeypatch):
    database_path = tmp_path / "auth-lifecycle.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    database.initialize_database()
    return database_path, TestClient(app)


def _csrf(client):
    return client.get("/auth/csrf").json()["csrf_token"]


def _register(client, csrf, **overrides):
    payload = {
        "full_name": "Taylor Sample",
        "username": "taylor.sample",
        "email": "taylor@example.net",
        "password": "SecureP4!",
    }
    payload.update(overrides)
    return client.post(
        "/auth/register",
        json=payload,
        headers={"X-CSRF-Token": csrf},
    )


def test_registration_unique_login_logout_and_user_rbac(tmp_path, monkeypatch):
    database_path, client = _client(tmp_path, monkeypatch)
    with client:
        csrf = _csrf(client)
        invalid_email = _register(client, csrf, username="bad-email", email="not-an-email")
        assert invalid_email.status_code == 422
        short_password = _register(client, csrf, username="short-pass", password="Ab1!")
        assert short_password.status_code == 422

        created = _register(client, csrf, role="ADMIN")
        assert created.status_code == 201
        assert created.json()["user"]["role"] == "USER"
        assert created.json()["user"]["full_name"] == "Taylor Sample"
        assert "password_hash" not in created.text
        assert "SecureP4!" not in created.text

        duplicate_username = _register(
            client,
            csrf,
            email="another@example.net",
        )
        assert duplicate_username.status_code == 409
        duplicate_email = _register(
            client,
            csrf,
            username="another-user",
        )
        assert duplicate_email.status_code == 409

        unknown = client.post(
            "/auth/login",
            json={"username": "missing@example.net", "password": "SecureP4!"},
            headers={"X-CSRF-Token": csrf},
        )
        assert unknown.status_code == 401
        wrong_password = client.post(
            "/auth/login",
            json={"username": "taylor.sample", "password": "WrongP4!"},
            headers={"X-CSRF-Token": csrf},
        )
        assert wrong_password.status_code == 401
        login = client.post(
            "/auth/login",
            json={
                "username": "TAYLOR@EXAMPLE.NET",
                "password": "SecureP4!",
                "remember_me": True,
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert login.status_code == 200
        assert login.json()["user"]["role"] == "USER"
        assert "password_hash" not in login.text
        assert "max-age=2592000" in login.headers["set-cookie"].lower()
        csrf = login.json()["csrf_token"]

        assert client.get("/user/dashboard").status_code == 200
        assert client.get("/admin/dashboard").status_code == 403
        assert client.get("/admin/users").status_code == 403

        user = database.get_user_by_username("taylor.sample")
        other_id = database.create_user(
            "another-user",
            "another@example.net",
            hash_password("OtherP4!"),
            "USER",
        )
        assert client.get(f"/admin/users/{other_id}").status_code == 403
        with sqlite3.connect(database_path) as connection:
            for user_id, timestamp in ((user["id"], "2026-01-01T00:00:00+00:00"), (other_id, "2026-01-02T00:00:00+00:00")):
                connection.execute(
                    """
                    INSERT INTO prediction_history (
                        timestamp, input_features_json, predicted_temperature_c,
                        risk_index_pct, risk_level, recommendation, user_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        timestamp,
                        json.dumps({"cargo_type": "Fresh_Produce", "set_point_c": 4}),
                        5.0,
                        12.0,
                        "LOW",
                        "Monitor conditions.",
                        user_id,
                    ),
                )
        history = client.get("/history").json()
        assert len(history) == 1
        assert history[0]["user_id"] == user["id"]

        assert client.post(
            "/auth/logout",
            headers={"X-CSRF-Token": csrf},
        ).status_code == 200
        assert client.get("/auth/me").status_code == 401


def test_password_reset_single_use_expiry_and_non_enumerating_response(tmp_path, monkeypatch):
    _database_path, client = _client(tmp_path, monkeypatch)
    user_id = database.create_user(
        "reset-user",
        "reset@example.net",
        hash_password("OldPass4!"),
        "USER",
    )
    delivered = []
    monkeypatch.setattr(email_delivery, "email_delivery_configured", lambda: True)
    monkeypatch.setattr(
        email_delivery,
        "send_password_reset",
        lambda email, token: delivered.append((email, token)),
    )
    with client:
        csrf = _csrf(client)
        unknown = client.post(
            "/auth/password-reset/request",
            json={"email": "unknown@example.net"},
            headers={"X-CSRF-Token": csrf},
        )
        response = client.post(
            "/auth/password-reset/request",
            json={"email": "reset@example.net"},
            headers={"X-CSRF-Token": csrf},
        )
        assert unknown.status_code == response.status_code == 202
        assert unknown.json() == response.json()
        assert len(delivered) == 1
        reset_token = delivered[0][1]
        token_hash = hashlib.sha256(reset_token.encode("utf-8")).hexdigest()
        with sqlite3.connect(database.database_path()) as connection:
            stored_hash, expires_at, used_at = connection.execute(
                "SELECT token_hash, expires_at, used_at FROM password_reset_tokens"
            ).fetchone()
        assert stored_hash == token_hash
        assert stored_hash != reset_token
        assert used_at is None
        assert datetime.fromisoformat(expires_at) > datetime.now(timezone.utc)
        assert reset_token not in response.text

        completed = client.post(
            "/auth/password-reset/complete",
            json={"token": reset_token, "new_password": "NewPass4!"},
            headers={"X-CSRF-Token": csrf},
        )
        assert completed.status_code == 200
        updated = database.get_user_by_id(user_id)
        assert verify_password("NewPass4!", updated["password_hash"])
        assert not verify_password("OldPass4!", updated["password_hash"])
        assert updated["password_hash"] != "NewPass4!"
        reused = client.post(
            "/auth/password-reset/complete",
            json={"token": reset_token, "new_password": "OtherNew4!"},
            headers={"X-CSRF-Token": csrf},
        )
        assert reused.status_code == 400

        expired_token = secrets.token_urlsafe(32)
        database.create_password_reset_token(
            user_id,
            hashlib.sha256(expired_token.encode("utf-8")).hexdigest(),
            (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        )
        expired = client.post(
            "/auth/password-reset/complete",
            json={"token": expired_token, "new_password": "Another4!"},
            headers={"X-CSRF-Token": csrf},
        )
        assert expired.status_code == 400

        assert client.post(
            "/auth/login",
            json={"username": "reset@example.net", "password": "OldPass4!"},
            headers={"X-CSRF-Token": csrf},
        ).status_code == 401
        login = client.post(
            "/auth/login",
            json={"username": "reset@example.net", "password": "NewPass4!"},
            headers={"X-CSRF-Token": csrf},
        )
        assert login.status_code == 200


def test_reset_email_delivery_failure_does_not_claim_success(tmp_path, monkeypatch):
    _database_path, client = _client(tmp_path, monkeypatch)
    database.create_user(
        "mail-fail",
        "mail-fail@example.net",
        hash_password("OldPass4!"),
        "USER",
    )
    monkeypatch.setattr(email_delivery, "email_delivery_configured", lambda: True)

    def fail_delivery(_email, _token):
        raise smtplib.SMTPException("private transport detail")

    monkeypatch.setattr(email_delivery, "send_password_reset", fail_delivery)
    with client:
        response = client.post(
            "/auth/password-reset/request",
            json={"email": "mail-fail@example.net"},
            headers={"X-CSRF-Token": _csrf(client)},
        )
        assert response.status_code == 202
        assert "only if email delivery is configured and succeeds" in response.json()["message"]
        assert "private transport detail" not in response.text
        with sqlite3.connect(database.database_path()) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM password_reset_tokens WHERE used_at IS NULL"
            ).fetchone()[0] == 0


def test_reset_request_without_email_configuration_discloses_no_account_state(tmp_path, monkeypatch):
    _database_path, client = _client(tmp_path, monkeypatch)
    database.create_user(
        "mail-unconfigured",
        "configured@example.net",
        hash_password("OldPass4!"),
        "USER",
    )
    monkeypatch.setattr(email_delivery, "email_delivery_configured", lambda: False)
    with client:
        csrf = _csrf(client)
        known = client.post(
            "/auth/password-reset/request",
            json={"email": "configured@example.net"},
            headers={"X-CSRF-Token": csrf},
        )
        unknown = client.post(
            "/auth/password-reset/request",
            json={"email": "absent@example.net"},
            headers={"X-CSRF-Token": csrf},
        )
        assert known.status_code == unknown.status_code == 202
        assert known.json() == unknown.json()
        assert "only if email delivery is configured and succeeds" in known.json()["message"]
        with sqlite3.connect(database.database_path()) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM password_reset_tokens"
            ).fetchone()[0] == 0


def test_reset_email_uses_fragment_token_and_local_mailpit_configuration(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SMTP_PORT", "1025")
    monkeypatch.setenv("SMTP_STARTTLS", "false")
    monkeypatch.setenv("SMTP_USERNAME", "")
    monkeypatch.setenv("SMTP_PASSWORD", "")
    monkeypatch.setenv("SMTP_FROM", "cold-chain@example.test")
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:8000")
    sent_messages = []

    class Mailpit:
        def __init__(self, _host, _port, timeout):
            assert timeout == 10

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def send_message(self, message):
            sent_messages.append(message)

    monkeypatch.setattr(email_delivery.smtplib, "SMTP", Mailpit)
    token = "random-reset-token-with-enough-length"
    email_delivery.send_password_reset("person@example.net", token)
    message = sent_messages[0]
    assert message["To"] == "person@example.net"
    assert f"http://127.0.0.1:8000/reset-password#token={token}" in message.get_content()
    assert f"?token={token}" not in message.get_content()


def test_existing_admin_role_can_access_admin_apis(tmp_path, monkeypatch):
    _database_path, client = _client(tmp_path, monkeypatch)
    database.create_user("test-admin", None, hash_password("AdminP4!"), "ADMIN")
    with client:
        csrf = _csrf(client)
        response = client.post(
            "/auth/login",
            json={"username": "test-admin", "password": "AdminP4!"},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 200
        assert response.json()["user"]["role"] == "ADMIN"
        assert client.get("/admin/dashboard").status_code == 200
        assert client.get("/metrics").status_code == 200
        assert client.get("/dataset-summary").status_code == 200
        assert client.get("/admin/users").status_code == 200
        assert client.get("/admin/activity").status_code == 200


def test_admin_search_user_details_edits_and_password_change_relogin(tmp_path):
    database_path = tmp_path / "admin-user-management.sqlite3"
    with authenticated_client(database_path) as admin:
        admin_id = admin.get("/auth/me").json()["user"]["id"]
        created = admin.post(
            "/admin/users",
            json={
                "username": "profile-target",
                "email": "target@example.net",
                "password": "TempPass4!",
                "role": "USER",
            },
        )
        assert created.status_code == 201
        target_id = created.json()["id"]
        duplicate_owner = admin.post(
            "/admin/users",
            json={
                "username": "email-owner",
                "email": "profile@example.net",
                "password": "TempPass4!",
                "role": "USER",
            },
        )
        assert duplicate_owner.status_code == 201

        by_email = admin.get("/admin/users", params={"search": " TARGET@EXAMPLE.NET "})
        by_id = admin.get("/admin/users", params={"search": str(target_id)})
        assert [user["id"] for user in by_email.json()] == [target_id]
        assert [user["id"] for user in by_id.json()] == [target_id]
        assert "Cold Chain" in admin.get(f"/user-details/{target_id}").text
        detail = admin.get(f"/admin/users/{target_id}")
        assert detail.status_code == 200
        assert "password_hash" not in detail.json()
        assert "TempPass4!" not in detail.text
        reset_token = secrets.token_urlsafe(32)
        database.create_password_reset_token(
            target_id,
            hashlib.sha256(reset_token.encode("utf-8")).hexdigest(),
            (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
        )
        temporary_password = "AdminIssuedTemp4!"
        reset = admin.post(
            f"/admin/users/{target_id}/reset-password",
            json={"new_password": temporary_password},
        )
        assert reset.status_code == 200
        assert temporary_password not in reset.text
        target = database.get_user_by_id(target_id)
        assert target["role"] == "USER"
        assert target["must_change_password"] is True
        assert verify_password(temporary_password, target["password_hash"])
        with sqlite3.connect(database_path) as connection:
            assert connection.execute(
                "SELECT used_at FROM password_reset_tokens WHERE user_id = ?",
                (target_id,),
            ).fetchone()[0] is not None

        updated = admin.patch(
            f"/admin/users/{target_id}",
            json={"full_name": "Updated Näme", "email": "updated@example.net"},
        )
        assert updated.status_code == 200
        assert admin.get(f"/admin/users/{target_id}").json()["full_name"] == "Updated Näme"
        by_name = admin.get("/admin/users", params={"search": "UPDATED NÄME"})
        assert [user["id"] for user in by_name.json()] == [target_id]
        assert admin.patch(
            f"/admin/users/{target_id}",
            json={"email": "profile@example.net"},
        ).status_code == 409
        assert admin.patch(
            f"/admin/users/{admin_id}",
            json={"role": "USER"},
        ).status_code == 409
        assert admin.patch(
            f"/admin/users/{admin_id}",
            json={"is_active": False},
        ).status_code == 409

        own = database.get_user_by_username("test-admin")
        previous_role = own["role"]
        previous_active = own["is_active"]
        old_password = "AdminStrongPassword1!"
        new_password = "AdminChangedP4!"
        changed = admin.post(
            "/auth/change-password",
            json={"current_password": old_password, "new_password": new_password},
        )
        assert changed.status_code == 200
        assert changed.json().get("csrf_token")
        own = database.get_user_by_id(admin_id)
        assert own["role"] == previous_role == "ADMIN"
        assert own["is_active"] == previous_active is True
        assert own["must_change_password"] is False
        assert verify_password(new_password, own["password_hash"])
        assert not verify_password(old_password, own["password_hash"])
        admin.headers.update({"X-CSRF-Token": changed.json()["csrf_token"]})
        assert admin.get("/admin/dashboard").status_code == 200
        assert admin.post(
            "/auth/logout",
            headers={"X-CSRF-Token": changed.json()["csrf_token"]},
        ).status_code == 200

        csrf = admin.get("/auth/csrf").json()["csrf_token"]
        rejected_old = admin.post(
            "/auth/login",
            json={"username": "test-admin", "password": old_password},
            headers={"X-CSRF-Token": csrf},
        )
        assert rejected_old.status_code == 401
        csrf = admin.get("/auth/csrf").json()["csrf_token"]
        accepted_new = admin.post(
            "/auth/login",
            json={"username": "test-admin", "password": new_password},
            headers={"X-CSRF-Token": csrf},
        )
        assert accepted_new.status_code == 200
        assert accepted_new.json()["user"]["role"] == "ADMIN"
        assert accepted_new.json()["user"]["is_active"] is True


def test_user_password_change_preserves_role_and_history(tmp_path):
    database_path = tmp_path / "user-password-change.sqlite3"
    with authenticated_client(database_path, role="USER") as client:
        prediction = client.post(
            "/predict",
            json={
                "cargo_type": "Fresh_Produce",
                "ambient_temperature_c": 24,
                "set_point_c": 4,
                "hvac_power_watts": 2500,
                "seal_integrity": 0.95,
            },
        )
        assert prediction.status_code == 200
        user = client.get("/auth/me").json()["user"]
        previous_password = "AdminStrongPassword1!"
        new_password = "UserChangedP4!"
        changed = client.post(
            "/auth/change-password",
            json={"current_password": previous_password, "new_password": new_password},
        )
        assert changed.status_code == 200
        persisted = database.get_user_by_id(user["id"])
        assert persisted["role"] == "USER"
        assert persisted["is_active"] is True
        assert len(client.get("/history").json()) == 1
        client.headers.update({"X-CSRF-Token": changed.json()["csrf_token"]})
        assert client.get("/admin/dashboard").status_code == 403
        assert client.post(
            "/auth/logout",
            headers={"X-CSRF-Token": changed.json()["csrf_token"]},
        ).status_code == 200
        csrf = client.get("/auth/csrf").json()["csrf_token"]
        assert client.post(
            "/auth/login",
            json={"username": "test-user", "password": previous_password},
            headers={"X-CSRF-Token": csrf},
        ).status_code == 401
        csrf = client.get("/auth/csrf").json()["csrf_token"]
        login = client.post(
            "/auth/login",
            json={"username": "test-user", "password": new_password},
            headers={"X-CSRF-Token": csrf},
        )
        assert login.status_code == 200
        assert login.json()["user"]["role"] == "USER"
        assert len(client.get("/history").json()) == 1
