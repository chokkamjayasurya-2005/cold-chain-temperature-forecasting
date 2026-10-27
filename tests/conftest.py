from pathlib import Path


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
    "Streamlit Tests": ("test_streamlit_dashboard_renders", "test_streamlit_ai_assistant"),
    "Groq API Adapter Tests": (
        "test_invalid_payload_and_batch_boundary",
        "test_streamlit_ai_assistant",
    ),
    "End-to-End Test": ("test_end_to_end_prediction_flow",),
}


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
