import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from backend.schemas import PredictionResponse, PredictionRequest

ROOT = Path(__file__).resolve().parents[1]


def database_path() -> Path:
    configured = Path(os.getenv("DATABASE_PATH", "artifacts/prediction_history.sqlite3"))
    return configured if configured.is_absolute() else ROOT / configured


def _connect() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database() -> None:
    path = database_path()
    if path.is_file():
        with sqlite3.connect(path) as existing:
            tables = {
                row[0]
                for row in existing.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            prediction_columns = {
                row[1] for row in existing.execute("PRAGMA table_info(prediction_history)")
            }
            user_columns = {
                row[1] for row in existing.execute("PRAGMA table_info(users)")
            }
        if "prediction_history" in tables and "user_id" not in prediction_columns:
            backup = path.with_suffix(path.suffix + ".pre-rbac.bak")
            if not backup.exists():
                with sqlite3.connect(path) as source, sqlite3.connect(backup) as destination:
                    source.backup(destination)
        if "users" in tables and "full_name" not in user_columns:
            backup = path.with_suffix(path.suffix + ".pre-auth.bak")
            if not backup.exists():
                with sqlite3.connect(path) as source, sqlite3.connect(backup) as destination:
                    source.backup(destination)
    with _connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS prediction_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                input_features_json TEXT NOT NULL,
                predicted_temperature_c REAL NOT NULL,
                risk_index_pct REAL NOT NULL,
                risk_level TEXT NOT NULL,
                recommendation TEXT NOT NULL,
                user_id INTEGER REFERENCES users(id)
            )
            """
        )
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(prediction_history)")
        }
        if "user_id" not in columns:
            connection.execute(
                "ALTER TABLE prediction_history ADD COLUMN user_id INTEGER REFERENCES users(id)"
            )
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                email TEXT,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('ADMIN', 'USER')),
                is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
                must_change_password INTEGER NOT NULL DEFAULT 0 CHECK (must_change_password IN (0, 1)),
                created_at TEXT NOT NULL,
                last_login_at TEXT,
                password_changed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_user_id INTEGER REFERENCES users(id),
                action TEXT NOT NULL,
                target_type TEXT,
                target_id TEXT,
                outcome TEXT NOT NULL CHECK (outcome IN ('success', 'failure')),
                safe_metadata TEXT NOT NULL DEFAULT '{}',
                timestamp TEXT NOT NULL
            );
            CREATE TRIGGER IF NOT EXISTS activity_logs_no_update
                BEFORE UPDATE ON activity_logs
                BEGIN
                    SELECT RAISE(ABORT, 'activity logs are append-only');
                END;
            CREATE TRIGGER IF NOT EXISTS activity_logs_no_delete
                BEFORE DELETE ON activity_logs
                BEGIN
                    SELECT RAISE(ABORT, 'activity logs are append-only');
                END;
            CREATE TABLE IF NOT EXISTS login_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username_key TEXT NOT NULL,
                address_key TEXT NOT NULL,
                attempted_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                used_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_prediction_user_timestamp
                ON prediction_history(user_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_activity_timestamp
                ON activity_logs(timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_activity_actor_timestamp
                ON activity_logs(actor_user_id, timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_login_attempts_key_time
                ON login_attempts(username_key, address_key, attempted_at);
            """
        )
        user_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(users)")
        }
        if "full_name" not in user_columns:
            connection.execute(
                "ALTER TABLE users ADD COLUMN full_name TEXT NOT NULL DEFAULT ''"
            )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_unique
            ON users(lower(trim(email)))
            WHERE email IS NOT NULL AND trim(email) != ''
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_password_reset_user_expiry
            ON password_reset_tokens(user_id, expires_at)
            """
        )


def record_prediction(
    inputs: PredictionRequest,
    result: PredictionResponse,
    user_id: int,
) -> None:
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO prediction_history (
                timestamp, input_features_json,
                predicted_temperature_c, risk_index_pct, risk_level, recommendation, user_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                json.dumps(inputs.model_dump(mode="json")),
                result.predicted_internal_temperature_c,
                result.estimated_risk_index_pct,
                result.risk_level,
                result.recommendation,
                user_id,
            ),
        )


def read_history(limit: int = 50, user_id: int | None = None) -> list[dict]:
    with _connect() as connection:
        query = """
            SELECT id, timestamp, input_features_json,
                   predicted_temperature_c, risk_index_pct, risk_level, recommendation,
                   user_id
            FROM prediction_history
        """
        parameters: tuple[Any, ...] = ()
        if user_id is not None:
            query += " WHERE user_id = ?"
            parameters = (user_id,)
        query += " ORDER BY id DESC LIMIT ?"
        parameters += (limit,)
        rows = connection.execute(
            query,
            parameters,
        ).fetchall()
    return [
        {
            "id": row["id"],
            "timestamp": row["timestamp"],
            "input_features": json.loads(row["input_features_json"]),
            "predicted_internal_temperature_c": row["predicted_temperature_c"],
            "estimated_risk_index_pct": row["risk_index_pct"],
            "risk_level": row["risk_level"],
            "recommendation": row["recommendation"],
            "user_id": row["user_id"],
        }
        for row in rows
    ]


def clear_history(user_id: int | None = None) -> int:
    with _connect() as connection:
        if user_id is None:
            cursor = connection.execute("DELETE FROM prediction_history")
        else:
            cursor = connection.execute(
                "DELETE FROM prediction_history WHERE user_id = ?",
                (user_id,),
            )
        return cursor.rowcount


def _public_user(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "full_name": row["full_name"],
        "username": row["username"],
        "email": row["email"],
        "role": row["role"],
        "is_active": bool(row["is_active"]),
        "must_change_password": bool(row["must_change_password"]),
        "created_at": row["created_at"],
        "last_login_at": row["last_login_at"],
        "password_changed_at": row["password_changed_at"],
    }


def get_user_by_id(user_id: int) -> dict | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    return {**_public_user(row), "password_hash": row["password_hash"]} if row else None


def get_user_by_username(username: str) -> dict | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
            (username.strip(),),
        ).fetchone()
    return {**_public_user(row), "password_hash": row["password_hash"]} if row else None


def get_user_by_email(email: str) -> dict | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM users WHERE email = ? COLLATE NOCASE",
            (email.strip(),),
        ).fetchone()
    return {**_public_user(row), "password_hash": row["password_hash"]} if row else None


def get_user_by_login_identifier(identifier: str) -> dict | None:
    normalized = identifier.strip()
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT * FROM users
            WHERE username = ? COLLATE NOCASE OR email = ? COLLATE NOCASE
            """,
            (normalized, normalized),
        ).fetchone()
    return {**_public_user(row), "password_hash": row["password_hash"]} if row else None


def list_users(search: str | None = None) -> list[dict]:
    normalized_search = search.strip().casefold() if search else ""
    with _connect() as connection:
        connection.create_function(
            "casefold",
            1,
            lambda value: str(value or "").casefold(),
        )
        rows = connection.execute(
            """
            SELECT * FROM users
            WHERE ? = ''
                OR casefold(username) LIKE '%' || ? || '%'
                OR casefold(full_name) LIKE '%' || ? || '%'
                OR casefold(coalesce(email, '')) LIKE '%' || ? || '%'
                OR CAST(id AS TEXT) LIKE '%' || ? || '%'
            ORDER BY created_at DESC, id DESC
            """,
            (
                normalized_search,
                normalized_search,
                normalized_search,
                normalized_search,
                normalized_search,
            ),
        ).fetchall()
    return [_public_user(row) for row in rows]


def create_user(
    username: str,
    email: str | None,
    password_hash: str,
    role: str,
    must_change_password: bool = False,
    full_name: str = "",
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO users (
                username, email, password_hash, role, created_at,
                full_name,
                password_changed_at, must_change_password
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                username.strip(),
                email.strip() if email else None,
                password_hash,
                role,
                now,
                full_name.strip(),
                now,
                int(must_change_password),
            ),
        )
        return int(cursor.lastrowid)


def record_login(user_id: int) -> None:
    with _connect() as connection:
        connection.execute(
            "UPDATE users SET last_login_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), user_id),
        )


def update_user(user_id: int, updates: dict[str, Any]) -> None:
    allowed = {"full_name", "email", "role", "is_active", "must_change_password"}
    if not updates or not set(updates).issubset(allowed):
        raise ValueError("Invalid user update.")
    assignments = ", ".join(f"{key} = ?" for key in updates)
    values = [
        int(value) if key in {"is_active", "must_change_password"} else value
        for key, value in updates.items()
    ]
    with _connect() as connection:
        connection.execute(
            f"UPDATE users SET {assignments} WHERE id = ?",
            (*values, user_id),
        )


def update_user_guarded(user_id: int, updates: dict[str, Any]) -> bool:
    allowed = {"full_name", "email", "role", "is_active", "must_change_password"}
    if not updates or not set(updates).issubset(allowed):
        raise ValueError("Invalid user update.")
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute(
            "SELECT role, is_active FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if current is None:
            return False
        next_role = updates.get("role", current["role"])
        next_active = bool(updates.get("is_active", current["is_active"]))
        if current["role"] == "ADMIN" and current["is_active"] and (
            next_role != "ADMIN" or not next_active
        ):
            active_admins = connection.execute(
                "SELECT COUNT(*) FROM users WHERE role = 'ADMIN' AND is_active = 1"
            ).fetchone()[0]
            if active_admins <= 1:
                raise ValueError("The last active administrator cannot be disabled or demoted.")
        assignments = ", ".join(f"{key} = ?" for key in updates)
        values = [
            int(value) if key in {"is_active", "must_change_password"} else value
            for key, value in updates.items()
        ]
        connection.execute(
            f"UPDATE users SET {assignments} WHERE id = ?",
            (*values, user_id),
        )
    return True


def update_password(
    user_id: int,
    password_hash: str,
    must_change_password: bool = False,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE users SET password_hash = ?, password_changed_at = ?,
                must_change_password = ?
            WHERE id = ?
            """,
            (password_hash, now, int(must_change_password), user_id),
        )
        connection.execute(
            """
            UPDATE password_reset_tokens SET used_at = ?
            WHERE user_id = ? AND used_at IS NULL
            """,
            (now, user_id),
        )


def create_password_reset_token(
    user_id: int,
    token_hash: str,
    expires_at: str,
) -> None:
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE password_reset_tokens
            SET used_at = ?
            WHERE user_id = ? AND used_at IS NULL
            """,
            (datetime.now(timezone.utc).isoformat(), user_id),
        )
        connection.execute(
            """
            INSERT INTO password_reset_tokens (user_id, token_hash, expires_at)
            VALUES (?, ?, ?)
            """,
            (user_id, token_hash, expires_at),
        )


def invalidate_password_reset_token(token_hash: str) -> None:
    with _connect() as connection:
        connection.execute(
            """
            UPDATE password_reset_tokens SET used_at = ?
            WHERE token_hash = ? AND used_at IS NULL
            """,
            (datetime.now(timezone.utc).isoformat(), token_hash),
        )


def consume_password_reset_token(token_hash: str, new_password_hash: str) -> int | None:
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        token = connection.execute(
            """
            SELECT id, user_id FROM password_reset_tokens
            WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?
            """,
            (token_hash, now),
        ).fetchone()
        if token is None:
            return None
        connection.execute(
            """
            UPDATE users SET password_hash = ?, password_changed_at = ?,
                must_change_password = 0
            WHERE id = ?
            """,
            (new_password_hash, now, token["user_id"]),
        )
        connection.execute(
            "UPDATE password_reset_tokens SET used_at = ? WHERE id = ?",
            (now, token["id"]),
        )
        connection.execute(
            """
            UPDATE password_reset_tokens SET used_at = ?
            WHERE user_id = ? AND id != ? AND used_at IS NULL
            """,
            (now, token["user_id"], token["id"]),
        )
        return int(token["user_id"])


def active_admin_count() -> int:
    with _connect() as connection:
        return int(connection.execute(
            "SELECT COUNT(*) FROM users WHERE role = 'ADMIN' AND is_active = 1"
        ).fetchone()[0])


def log_activity(
    actor_user_id: int | None,
    action: str,
    target_type: str | None = None,
    target_id: str | int | None = None,
    outcome: str = "success",
    safe_metadata: dict[str, Any] | None = None,
) -> None:
    metadata = safe_metadata or {}
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO activity_logs (
                actor_user_id, action, target_type, target_id, outcome,
                safe_metadata, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                actor_user_id,
                action,
                target_type,
                str(target_id) if target_id is not None else None,
                outcome,
                json.dumps(metadata, ensure_ascii=True),
                now,
            ),
        )


def begin_login_attempt(username: str, address: str) -> bool:
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(minutes=15)).isoformat()
    username_key = username.strip().casefold()
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "DELETE FROM login_attempts WHERE attempted_at < ?",
            (cutoff,),
        )
        count = connection.execute(
            """
            SELECT COUNT(*) FROM login_attempts
            WHERE username_key = ? AND address_key = ? AND attempted_at >= ?
            """,
            (username_key, address, cutoff),
        ).fetchone()[0]
        if int(count) >= 5:
            return True
        connection.execute(
            """
            INSERT INTO login_attempts (username_key, address_key, attempted_at)
            VALUES (?, ?, ?)
            """,
            (username_key, address, now.isoformat()),
        )
    return False


def clear_login_attempts(username: str, address: str) -> None:
    with _connect() as connection:
        connection.execute(
            "DELETE FROM login_attempts WHERE username_key = ? AND address_key = ?",
            (username.strip().casefold(), address),
        )


def dashboard_metrics_for_user(user_id: int | None) -> dict:
    where_clause = "" if user_id is None else "WHERE user_id = ?"
    parameters: tuple[Any, ...] = () if user_id is None else (user_id,)
    today = datetime.now(timezone.utc).date().isoformat()
    with _connect() as connection:
        totals = connection.execute(
            f"""
            SELECT COUNT(*) AS total,
                COALESCE(SUM(CASE WHEN timestamp >= ? THEN 1 ELSE 0 END), 0) AS today,
                COALESCE(SUM(CASE WHEN risk_level = 'HIGH' THEN 1 ELSE 0 END), 0) AS high,
                COALESCE(SUM(CASE WHEN risk_level = 'CRITICAL' THEN 1 ELSE 0 END), 0) AS critical,
                AVG(predicted_temperature_c) AS average_temperature,
                AVG(risk_index_pct) AS average_risk
            FROM prediction_history {where_clause}
            """,
            (today, *parameters),
        ).fetchone()
        risk = connection.execute(
            f"""
            SELECT risk_level, COUNT(*) AS count
            FROM prediction_history {where_clause}
            GROUP BY risk_level
            """,
            parameters,
        ).fetchall()
        users = None
        activity = None
        if user_id is None:
            users = connection.execute(
                """
                SELECT COUNT(*) AS total,
                    COALESCE(SUM(is_active), 0) AS active,
                    COALESCE(SUM(CASE WHEN is_active = 0 THEN 1 ELSE 0 END), 0) AS inactive
                FROM users
                """
            ).fetchone()
            activity = connection.execute(
                """
                SELECT action, COUNT(*) AS count
                FROM activity_logs
                WHERE timestamp >= ?
                GROUP BY action
                ORDER BY count DESC
                LIMIT 8
                """,
                (today,),
            ).fetchall()
    return {
        "total_predictions": int(totals["total"]),
        "predictions_today": int(totals["today"]),
        "high_risk_predictions": int(totals["high"]),
        "critical_risk_predictions": int(totals["critical"]),
        "average_predicted_temperature_c": totals["average_temperature"],
        "average_risk_index_pct": totals["average_risk"],
        "risk_distribution": {row["risk_level"]: int(row["count"]) for row in risk},
        "user_counts": (
            {
                "total": int(users["total"]),
                "active": int(users["active"]),
                "inactive": int(users["inactive"]),
            }
            if users is not None
            else None
        ),
        "recent_activity_counts": (
            [{"action": row["action"], "count": int(row["count"])} for row in activity]
            if activity is not None
            else None
        ),
    }


def list_activity_logs(
    search: str | None,
    action: str | None,
    outcome: str | None,
    start: str | None,
    end: str | None,
    offset: int,
    limit: int,
) -> tuple[list[dict], int]:
    clauses: list[str] = []
    parameters: list[Any] = []
    if search:
        clauses.append("(u.username LIKE ? OR CAST(a.actor_user_id AS TEXT) LIKE ?)")
        parameters.extend((f"%{search}%", f"%{search}%"))
    if action:
        clauses.append("a.action = ?")
        parameters.append(action)
    if outcome:
        clauses.append("a.outcome = ?")
        parameters.append(outcome)
    if start:
        clauses.append("a.timestamp >= ?")
        parameters.append(start)
    if end:
        clauses.append("a.timestamp <= ?")
        parameters.append(end)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect() as connection:
        total = connection.execute(
            f"""
            SELECT COUNT(*) FROM activity_logs a
            LEFT JOIN users u ON a.actor_user_id = u.id {where}
            """,
            parameters,
        ).fetchone()[0]
        rows = connection.execute(
            f"""
            SELECT a.id, a.actor_user_id, u.username, a.action, a.target_type,
                a.target_id, a.outcome, a.safe_metadata, a.timestamp
            FROM activity_logs a
            LEFT JOIN users u ON a.actor_user_id = u.id {where}
            ORDER BY a.timestamp DESC, a.id DESC LIMIT ? OFFSET ?
            """,
            (*parameters, limit, offset),
        ).fetchall()
    result = [
        {
            "id": row["id"],
            "actor_user_id": row["actor_user_id"],
            "username": row["username"],
            "action": row["action"],
            "target_type": row["target_type"],
            "target_id": row["target_id"],
            "outcome": row["outcome"],
            "safe_metadata": json.loads(row["safe_metadata"]),
            "timestamp": row["timestamp"],
        }
        for row in rows
    ]
    return result, int(total)
