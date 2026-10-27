import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

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
    return connection


def initialize_database() -> None:
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
                recommendation TEXT NOT NULL
            )
            """
        )


def record_prediction(inputs: PredictionRequest, result: PredictionResponse) -> None:
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO prediction_history (
                timestamp, input_features_json,
                predicted_temperature_c, risk_index_pct, risk_level, recommendation
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                json.dumps(inputs.model_dump(mode="json")),
                result.predicted_internal_temperature_c,
                result.estimated_risk_index_pct,
                result.risk_level,
                result.recommendation,
            ),
        )


def read_history(limit: int = 50) -> list[dict]:
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT id, timestamp, input_features_json,
                   predicted_temperature_c, risk_index_pct, risk_level, recommendation
            FROM prediction_history ORDER BY id DESC LIMIT ?
            """,
            (limit,),
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
        }
        for row in rows
    ]


def clear_history() -> int:
    with _connect() as connection:
        cursor = connection.execute("DELETE FROM prediction_history")
        return cursor.rowcount
