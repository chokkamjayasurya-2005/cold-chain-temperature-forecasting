from pathlib import Path
import os
from contextlib import contextmanager

from fastapi.testclient import TestClient

TEST_RESULTS = {}
REPORT_GROUPS = {
    "Dataset Tests": ("test_data.py",),
    "Preprocessing Tests": ("test_preprocessing_imputes_missing_values",),
    "Model Training Tests": (
        "test_model_training_pipeline",
        "test_all_regression_models_train_and_predict",
    ),
    "Model Prediction Tests": ("test_prediction_validation_and_response",),
    "Saved Model Tests": ("test_saved_model_prediction",),
    "Risk Engine Tests": ("test_risk.py",),
    "Recommendation Tests": (
        "test_recommendation_escalates_for_deviation_or_seal",
        "test_temperature_deviation_categories",
    ),
    "Input Validation Tests": ("test_invalid_payload_and_batch_boundary",),
    "Authentication and RBAC Tests": ("test_auth.py", "test_auth_lifecycle.py"),
    "Streamlit Tests": ("test_streamlit_dashboard_renders", "test_streamlit_ai_assistant"),
    "Groq API Adapter Tests": (
        "test_invalid_payload_and_batch_boundary",
        "test_streamlit_ai_assistant",
    ),
    "End-to-End Test": ("test_end_to_end_prediction_flow",),
}


@contextmanager
def authenticated_client(
    database_path: Path,
    role: str = "ADMIN",
    username: str | None = None,
):
    from backend import database
    from backend.app import app
    from backend.auth import hash_password

    previous_database_path = os.environ.get("DATABASE_PATH")
    try:
        os.environ["DATABASE_PATH"] = str(database_path)
        database.initialize_database()
        username = username or f"test-{role.lower()}"
        if database.get_user_by_username(username) is None:
            database.create_user(
                username=username,
                email=None,
                password_hash=hash_password("AdminStrongPassword1!"),
                role=role,
            )
        with TestClient(app) as client:
            csrf = client.get("/auth/csrf").json()["csrf_token"]
            response = client.post(
                "/auth/login",
                json={"username": username, "password": "AdminStrongPassword1!"},
                headers={"X-CSRF-Token": csrf},
            )
            assert response.status_code == 200
            client.headers.update({"X-CSRF-Token": response.json()["csrf_token"]})
            yield client
    finally:
        if previous_database_path is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = previous_database_path


def pytest_runtest_logreport(report):
    if report.when == "call" or report.outcome == "failed":
        TEST_RESULTS[report.nodeid] = report.outcome


def pytest_sessionfinish(session, exitstatus):
    items = session.items
    lines = [
        "Cold Chain Temperature Forecasting",
        "Test Report",
        "================================",
        "",
    ]
    for category, selectors in REPORT_GROUPS.items():
        matching = [
            item.nodeid for item in items
            if any(selector in item.nodeid for selector in selectors)
        ]
        passed = bool(matching) and all(TEST_RESULTS.get(nodeid) == "passed" for nodeid in matching)
        lines.append(f"{category}: {'PASS' if passed else 'FAIL'}")

    passed_count = sum(result == "passed" for result in TEST_RESULTS.values())
    failed_count = len(items) - passed_count
    lines.extend([
        "",
        f"Total Tests: {len(items)}",
        f"Passed: {passed_count}",
        f"Failed: {failed_count}",
        "",
        f"Final Status: {'PASS' if exitstatus == 0 else 'FAIL'}",
        "",
    ])
    report_path = Path(__file__).resolve().parents[1] / "artifacts" / "test_report.txt"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")
