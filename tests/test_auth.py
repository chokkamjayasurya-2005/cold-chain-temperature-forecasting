import sqlite3

from fastapi.testclient import TestClient
import pytest

from backend import database
from backend.app import app
from backend.auth import hash_password, verify_password
from tests.conftest import authenticated_client


PASSWORD = "AdminStrongPassword1!"
PREDICTION = {
    "cargo_type": "Fresh_Produce",
    "ambient_temperature_c": 24.0,
    "set_point_c": 4.0,
    "hvac_power_watts": 2500,
    "seal_integrity": 0.95,
}


def test_anonymous_access_and_csrf_protection(tmp_path, monkeypatch):
    database_path = tmp_path / "auth.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    database.initialize_database()
    database.create_user("test-admin", None, hash_password(PASSWORD), "ADMIN")

    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/metrics").status_code == 401
        assert client.get("/history").status_code == 401
        csrf = client.get("/auth/csrf").json()["csrf_token"]
        response = client.post(
            "/auth/login",
            json={"username": "test-admin", "password": PASSWORD},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 200
        assert "password_hash" not in response.json()["user"]
        assert response.json()["user"]["role"] == "ADMIN"
        assert client.post("/auth/logout", headers={"X-CSRF-Token": "invalid"}).status_code == 403
        assert client.get("/metrics").status_code == 200
        export = client.get("/admin/activity.csv")
        assert export.status_code == 200
        assert export.headers["content-type"].startswith("text/csv")
        assert "login_success" in export.text


def test_inactive_account_is_rejected_and_session_cookie_is_protected(tmp_path, monkeypatch):
    database_path = tmp_path / "inactive.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    database.initialize_database()
    database.create_user("test-admin", None, hash_password(PASSWORD), "ADMIN")
    inactive_id = database.create_user("inactive-user", None, hash_password(PASSWORD), "USER")
    database.update_user_guarded(inactive_id, {"is_active": False})

    with TestClient(app) as client:
        csrf = client.get("/auth/csrf").json()["csrf_token"]
        success = client.post(
            "/auth/login",
            json={"username": "test-admin", "password": PASSWORD},
            headers={"X-CSRF-Token": csrf},
        )
        assert success.status_code == 200
        cookie = success.headers["set-cookie"].lower()
        assert "httponly" in cookie
        assert "samesite=lax" in cookie
        assert "max-age=" not in cookie

        assert client.post(
            "/auth/logout",
            headers={"X-CSRF-Token": success.json()["csrf_token"]},
        ).status_code == 200
        csrf = client.get("/auth/csrf").json()["csrf_token"]
        inactive = client.post(
            "/auth/login",
            json={"username": "inactive-user", "password": PASSWORD},
            headers={"X-CSRF-Token": csrf},
        )
        assert inactive.status_code == 401
        assert client.get("/auth/me").status_code == 401


def test_password_policy_accepts_six_characters_and_rejects_fewer():
    assert verify_password("Abc123", hash_password("Abc123"))
    with pytest.raises(ValueError, match="at least 6 characters"):
        hash_password("Abc1!")


def test_admin_completes_forced_password_change(tmp_path, monkeypatch):
    database_path = tmp_path / "forced-admin.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    database.initialize_database()
    admin_id = database.create_user(
        "forced-admin",
        None,
        hash_password(PASSWORD),
        "ADMIN",
        must_change_password=True,
    )
    worker_id = database.create_user(
        "other-worker",
        None,
        hash_password("OtherWorkerPassword1!"),
        "USER",
    )
    worker_hash_before = database.get_user_by_id(worker_id)["password_hash"]

    with TestClient(app) as client:
        csrf = client.get("/auth/csrf").json()["csrf_token"]
        login = client.post(
            "/auth/login",
            json={"username": "forced-admin", "password": PASSWORD},
            headers={"X-CSRF-Token": csrf},
        )
        assert login.status_code == 200
        assert login.json()["user"]["must_change_password"] is True
        previous_session = client.cookies.get("cold_chain_session")
        stale_client = TestClient(app)
        stale_client.cookies.set("cold_chain_session", previous_session)
        csrf = login.json()["csrf_token"]

        invalid_new_password = client.post(
            "/auth/change-password",
            json={"current_password": "not-the-current-password", "new_password": "GoodNewPassword2!"},
            headers={"X-CSRF-Token": csrf},
        )
        assert invalid_new_password.status_code == 400
        weak_new_password = client.post(
            "/auth/change-password",
            json={"current_password": PASSWORD, "new_password": "weak-password"},
            headers={"X-CSRF-Token": csrf},
        )
        assert weak_new_password.status_code == 422
        unchanged = database.get_user_by_id(admin_id)
        assert unchanged["must_change_password"] is True
        assert verify_password(PASSWORD, unchanged["password_hash"])

        new_password = "NewP4!"
        changed = client.post(
            "/auth/change-password",
            json={
                "current_password": PASSWORD,
                "new_password": new_password,
                "target_user_id": worker_id,
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert changed.status_code == 200
        updated = database.get_user_by_id(admin_id)
        assert updated["must_change_password"] is False
        assert updated["password_hash"] != new_password
        assert verify_password(new_password, updated["password_hash"])
        assert not verify_password(PASSWORD, updated["password_hash"])
        assert database.get_user_by_id(worker_id)["password_hash"] == worker_hash_before
        assert client.get("/admin/dashboard").status_code == 200
        assert stale_client.get("/auth/me").status_code == 401
        stale_client.close()

        assert client.post(
            "/auth/logout",
            headers={"X-CSRF-Token": changed.json()["csrf_token"]},
        ).status_code == 200
        csrf = client.get("/auth/csrf").json()["csrf_token"]
        rejected_old_password = client.post(
            "/auth/login",
            json={"username": "forced-admin", "password": PASSWORD},
            headers={"X-CSRF-Token": csrf},
        )
        assert rejected_old_password.status_code == 401
        csrf = client.get("/auth/csrf").json()["csrf_token"]
        accepted_new_password = client.post(
            "/auth/login",
            json={"username": "forced-admin", "password": new_password},
            headers={"X-CSRF-Token": csrf},
        )
        assert accepted_new_password.status_code == 200
        assert accepted_new_password.json()["user"]["must_change_password"] is False


def test_local_password_recovery_updates_existing_account_once(tmp_path, monkeypatch):
    database_path = tmp_path / "recovery.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    database.initialize_database()
    user_id = database.create_user("recover-me", None, hash_password(PASSWORD), "ADMIN")
    monkeypatch.setattr("sys.argv", ["reset_user_password", "--username", "recover-me"])
    confirmations = iter(["recover-me"])
    passwords = iter(["RecoveredPassword3!", "RecoveredPassword3!"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(confirmations))
    monkeypatch.setattr("backend.reset_user_password.getpass.getpass", lambda _prompt: next(passwords))

    from backend.reset_user_password import main

    main()
    user = database.get_user_by_id(user_id)
    assert user["must_change_password"] is True
    assert user["password_hash"] != "RecoveredPassword3!"
    assert verify_password("RecoveredPassword3!", user["password_hash"])
    assert not verify_password(PASSWORD, user["password_hash"])
    assert len(database.list_users()) == 1
    rows, _ = database.list_activity_logs(
        search=None,
        action="password_recovery_reset",
        outcome=None,
        start=None,
        end=None,
        offset=0,
        limit=10,
    )
    assert len(rows) == 1
    assert rows[0]["target_id"] == str(user_id)
    assert "RecoveredPassword3!" not in str(rows[0]["safe_metadata"])


def test_user_role_cannot_access_admin_data_and_only_sees_owned_history(tmp_path):
    database_path = tmp_path / "roles.sqlite3"
    with authenticated_client(database_path) as admin:
        assert admin.post("/predict", json=PREDICTION).status_code == 200
        created = admin.post(
            "/admin/users",
            json={
                "username": "worker",
                "email": "worker@example.test",
                "password": PASSWORD,
                "role": "USER",
            },
        )
        assert created.status_code == 201
        assert created.json()["must_change_password"] is True
        with authenticated_client(database_path, role="USER", username="worker") as user:
            assert user.get("/user/dashboard").status_code == 403
            changed = user.post(
                "/auth/change-password",
                json={"current_password": PASSWORD, "new_password": "WorkerPassword2!"},
            )
            assert changed.status_code == 200
            user.headers.update({"X-CSRF-Token": changed.json()["csrf_token"]})
            assert user.get("/metrics").status_code == 403
            assert user.get("/dataset-summary").status_code == 403
            assert user.get("/admin/users").status_code == 403
            assert user.get("/admin/activity").status_code == 403
            assert user.get("/admin/activity.csv").status_code == 403
            assert user.get("/user/dashboard").status_code == 200
            assert user.get("/history").json() == []
            assert user.post("/predict", json=PREDICTION).status_code == 200
            own_history = user.get("/history").json()
            assert len(own_history) == 1
            assert own_history[0]["user_id"] == created.json()["id"]
        all_history = admin.get("/history").json()
        assert len(all_history) == 2
        assert {row["user_id"] for row in all_history} == {1, created.json()["id"]}


def test_last_active_admin_cannot_be_demoted_or_disabled(tmp_path):
    with authenticated_client(tmp_path / "last-admin.sqlite3") as admin:
        user_id = admin.get("/auth/me").json()["user"]["id"]
        demote = admin.patch(f"/admin/users/{user_id}", json={"role": "USER"})
        deactivate = admin.patch(f"/admin/users/{user_id}", json={"is_active": False})
        assert demote.status_code == 409
        assert deactivate.status_code == 409
        assert database.active_admin_count() == 1


def test_login_failures_are_rate_limited_and_audited(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "login.sqlite3"))
    database.initialize_database()
    database.create_user("test-admin", None, hash_password(PASSWORD), "ADMIN")
    with TestClient(app) as client:
        csrf = client.get("/auth/csrf").json()["csrf_token"]
        for _ in range(5):
            response = client.post(
                "/auth/login",
                json={"username": "test-admin", "password": "WrongPassword1!"},
                headers={"X-CSRF-Token": csrf},
            )
            assert response.status_code == 401
        limited = client.post(
            "/auth/login",
            json={"username": "test-admin", "password": PASSWORD},
            headers={"X-CSRF-Token": csrf},
        )
        assert limited.status_code == 429
    with sqlite3.connect(tmp_path / "login.sqlite3") as connection:
        attempts = connection.execute("SELECT COUNT(*) FROM login_attempts").fetchone()[0]
        rows = connection.execute(
            "SELECT action, safe_metadata FROM activity_logs ORDER BY id"
        ).fetchall()
    assert attempts == 5
    assert len(rows) == 6
    assert [action for action, _ in rows].count("login_failed") == 5
    assert [action for action, _ in rows].count("login_throttled") == 1
    assert all(PASSWORD not in metadata and "WrongPassword" not in metadata for _, metadata in rows)
    with sqlite3.connect(tmp_path / "login.sqlite3") as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE activity_logs SET action = 'tampered' WHERE id = 1")


def test_legacy_history_is_backed_up_and_migrated_without_data_loss(tmp_path, monkeypatch):
    database_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE prediction_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                container_id TEXT,
                input_features_json TEXT NOT NULL,
                predicted_temperature_c REAL NOT NULL,
                risk_index_pct REAL NOT NULL,
                risk_level TEXT NOT NULL,
                recommendation TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO prediction_history (
                timestamp, container_id, input_features_json,
                predicted_temperature_c, risk_index_pct, risk_level, recommendation
            ) VALUES ('2024-01-01T00:00:00+00:00', 'legacy-container', '{}', 3.5, 12, 'LOW', 'Monitor')
            """
        )
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    database.initialize_database()
    backup_path = tmp_path / "legacy.sqlite3.pre-rbac.bak"
    assert backup_path.exists()
    migrated = database.read_history()
    assert len(migrated) == 1
    assert migrated[0]["user_id"] is None
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT container_id FROM prediction_history").fetchone()[0] == "legacy-container"
    with sqlite3.connect(backup_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM prediction_history").fetchone()[0] == 1


def test_admin_bootstrap_is_one_time_and_stores_only_a_hash(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "bootstrap.sqlite3"))
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", PASSWORD)
    def provide_username(prompt: str) -> str:
        assert "username" in prompt.lower()
        return "first-admin"

    monkeypatch.setattr("builtins.input", provide_username)
    from backend.bootstrap_admin import main

    main()
    user = database.get_user_by_username("first-admin")
    assert user is not None
    assert user["role"] == "ADMIN"
    assert user["password_hash"] != PASSWORD
    with pytest.raises(SystemExit, match="active administrator already exists"):
        main()
