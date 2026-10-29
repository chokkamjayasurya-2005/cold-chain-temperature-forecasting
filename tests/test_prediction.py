import os
from itertools import product

import numpy as np
from backend.app import app
from ml.common import FEATURES, TARGET, load_cargo_types, load_dataset
from ml.train import make_pipeline
from services.llm_service import GroqConfigurationError, GroqRequestError
from sklearn.linear_model import LinearRegression
from tests.conftest import authenticated_client


def test_prediction_validation_and_response(tmp_path):
    with authenticated_client(tmp_path / "history.sqlite3") as client:
        assert client.get("/health").json() == {"status": "ok", "model_loaded": True}
        payload = {
            "cargo_type": "Frozen_Seafood",
            "ambient_temperature_c": 80,
            "set_point_c": -18,
            "hvac_power_watts": 0,
            "seal_integrity": 0,
        }
        response = client.post("/predict", json=payload)
        assert response.status_code == 200
        result = response.json()
        assert isinstance(result["predicted_internal_temperature_c"], float)
        assert result["temperature_deviation_c"] == round(
            result["predicted_internal_temperature_c"] - payload["set_point_c"], 3
        )
        assert isinstance(result["estimated_risk_index_pct"], float)
        assert result["risk_level"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
        assert result["recommendation"]
        assert result["risk_disclaimer"].startswith("This transparent")
        assert result["explanation"]["top_factors"]
        assert client.get("/history").json()[0]["input_features"]["cargo_type"] == "Frozen_Seafood"
        assert "container_id" not in result


def test_web_routes_dataset_summary_and_history_clear(tmp_path):
    with authenticated_client(tmp_path / "history.sqlite3") as client:
        for route in ("/", "/overview", "/prediction", "/analytics", "/history", "/dataset"):
            page = client.get(route, headers={"accept": "text/html"})
            assert page.status_code == 200
            assert "text/html" in page.headers["content-type"]
            assert 'data-route="/prediction"' in page.text

        assert client.get("/ai-assistant").status_code == 404
        assert client.get("/").text.count('data-route="/ai-assistant"') == 0

        summary = client.get("/dataset-summary").json()
        assert summary["rows"] == 80000
        assert summary["column_count"] == 9
        assert summary["average_temperature_c"] > -20
        assert all("Container_ID" not in row for row in summary["sample_records"])

        payload = {
            "cargo_type": "Frozen_Seafood",
            "ambient_temperature_c": 25,
            "set_point_c": -18,
            "hvac_power_watts": 1000,
            "seal_integrity": 0.9,
        }
        assert client.post("/predict", json=payload).status_code == 200
        assert len(client.get("/history").json()) == 1
        assert client.delete("/history").json() == {"deleted": 1}
        assert client.get("/history").json() == []


def test_invalid_payload_and_batch_boundary(tmp_path, monkeypatch):
    with authenticated_client(tmp_path / "history.sqlite3") as client:
        base = {
            "cargo_type": "Frozen_Seafood",
            "ambient_temperature_c": 25,
            "set_point_c": -18,
            "hvac_power_watts": 1000,
            "seal_integrity": 0.9,
        }
        assert client.post("/predict", json={**base, "cargo_type": "Unknown"}).status_code == 422
        assert client.post("/predict", json={**base, "ambient_temperature_c": "cold"}).status_code == 422
        assert client.post("/predict", json={**base, "hvac_power_watts": -1}).status_code == 422
        assert client.post("/predict", json={**base, "seal_integrity": 1.1}).status_code == 422
        assert client.post("/predict", json={key: value for key, value in base.items() if key != "set_point_c"}).status_code == 422
        assert client.post("/batch-predict", json=[]).status_code == 422
        assert client.get("/model-info").json()["target"] == "Actual_Internal_Temp_C"
        assert client.get("/options").json()["cargo_types"] == list(load_cargo_types())
        assert client.get("/metrics").status_code == 200
        batch = client.post("/batch-predict", json=[base, base])
        assert batch.status_code == 200
        assert len(batch.json()) == 2
        prediction = client.post("/predict", json=base).json()
        captured_chat = {}

        def capture_chat(request):
            captured_chat["request"] = request
            return "Groq explanation."

        monkeypatch.setattr("backend.app.explain_prediction", capture_chat)
        chat = client.post("/chat", json={
            "question": "Explain this prediction",
            "conversation": [
                {"role": "user", "content": "What is the risk index?"},
                {"role": "assistant", "content": "It is a configurable operational estimate."},
            ],
            "container_context": prediction,
        })
        assert chat.status_code == 200
        assert chat.json() == {"answer": "Groq explanation.", "source": "Groq"}
        assert [message.content for message in captured_chat["request"].conversation] == [
            "What is the risk index?",
            "It is a configurable operational estimate.",
        ]
        assert client.post("/chat", json={
            "question": "a" * 1001,
            "container_context": prediction,
        }).status_code == 422
        assert client.get("/").status_code == 200
        assert client.get("/static/app.js").status_code == 200


def test_prediction_handles_dataset_categories_and_numeric_boundaries(tmp_path):
    os.environ["DATABASE_PATH"] = str(tmp_path / "history.sqlite3")
    dataset = load_dataset()
    feature_values = {
        "ambient_temperature_c": [
            float(dataset["Ambient_External_Temp_C"].min()),
            float(dataset["Ambient_External_Temp_C"].median()),
            float(dataset["Ambient_External_Temp_C"].max()),
        ],
        "set_point_c": [
            float(dataset["Internal_Set_Point_C"].min()),
            float(dataset["Internal_Set_Point_C"].median()),
            float(dataset["Internal_Set_Point_C"].max()),
        ],
        "hvac_power_watts": [
            float(dataset["HVAC_Power_Consumption_Watts"].min()),
            float(dataset["HVAC_Power_Consumption_Watts"].median()),
            float(dataset["HVAC_Power_Consumption_Watts"].max()),
        ],
        "seal_integrity": [
            float(dataset["Seal_Integrity_Index"].min()),
            float(dataset["Seal_Integrity_Index"].median()),
            float(dataset["Seal_Integrity_Index"].max()),
        ],
    }
    tested = 0
    with authenticated_client(tmp_path / "history.sqlite3") as client:
        for cargo_type in load_cargo_types():
            for ambient, set_point, hvac, seal in product(
                (feature_values["ambient_temperature_c"][0], feature_values["ambient_temperature_c"][2]),
                (feature_values["set_point_c"][0], feature_values["set_point_c"][2]),
                (feature_values["hvac_power_watts"][0], feature_values["hvac_power_watts"][2]),
                (feature_values["seal_integrity"][0], feature_values["seal_integrity"][2]),
            ):
                response = client.post(
                    "/predict",
                    json={
                        "cargo_type": cargo_type,
                        "ambient_temperature_c": ambient,
                        "set_point_c": set_point,
                        "hvac_power_watts": hvac,
                        "seal_integrity": seal,
                    },
                )
                assert response.status_code == 200, response.text
                result = response.json()
                assert np.isfinite(result["predicted_internal_temperature_c"])
                assert 0 <= result["estimated_risk_index_pct"] <= 100
                assert result["risk_level"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
                assert result["recommendation"]
                tested += 1

            typical = client.post(
                "/predict",
                json={
                    "cargo_type": cargo_type,
                    "ambient_temperature_c": feature_values["ambient_temperature_c"][1],
                    "set_point_c": feature_values["set_point_c"][1],
                    "hvac_power_watts": feature_values["hvac_power_watts"][1],
                    "seal_integrity": feature_values["seal_integrity"][1],
                },
            )
            assert typical.status_code == 200, typical.text
            tested += 1
    assert tested == len(load_cargo_types()) * 17


def test_chat_reports_groq_configuration_and_request_failures(monkeypatch, tmp_path):
    with authenticated_client(tmp_path / "history.sqlite3") as client:
        prediction = client.post(
            "/predict",
            json={
                "cargo_type": load_cargo_types()[0],
                "ambient_temperature_c": 25,
                "set_point_c": 4,
                "hvac_power_watts": 1000,
                "seal_integrity": 0.9,
            },
        ).json()
        payload = {"question": "Explain this prediction.", "container_context": prediction}

        def missing_key(_request):
            raise GroqConfigurationError("GROQ_API_KEY is not set.")

        def failed_request(_request):
            raise GroqRequestError("Groq API request failed.")

        monkeypatch.setattr("backend.app.explain_prediction", missing_key)
        assert client.post("/chat", json=payload).status_code == 503

        monkeypatch.setattr("backend.app.explain_prediction", failed_request)
        assert client.post("/chat", json=payload).status_code == 502


def test_preprocessing_imputes_missing_values():
    frame = load_dataset().head(100)
    x = frame[FEATURES].copy()
    y = frame[TARGET]
    x.loc[x.index[0], "Cargo_Type"] = None
    x.loc[x.index[1], "HVAC_Power_Consumption_Watts"] = float("nan")
    pipeline = make_pipeline(LinearRegression(), scale_numeric=True)
    pipeline.fit(x, y)
    assert pipeline.predict(x.iloc[:3]).shape == (3,)
    unknown_cargo = x.iloc[:1].copy()
    unknown_cargo.loc[:, "Cargo_Type"] = "Unknown_Cargo"
    assert np.isfinite(pipeline.predict(unknown_cargo)).all()
