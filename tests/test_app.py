import pytest
from streamlit.testing.v1 import AppTest

from ml.common import ROOT, load_cargo_types
from services.llm_service import (
    GroqConfigurationError,
    SYSTEM_PROMPT,
    generate_ai_explanation,
)


def test_streamlit_dashboard_renders():
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()

    assert not app.exception
    assert any("Cold Chain Temperature Forecasting" in item.value for item in app.title)


def test_streamlit_prediction_form_runs_prediction():
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.radio[0].set_value("Predict").run()
    app.button[0].click().run()

    assert not app.exception
    assert any("Predicted internal temperature:" in item.value for item in app.subheader)
    assert any(item.label == "Operational risk index" for item in app.metric)


def test_streamlit_prediction_form_uses_dataset_categories_without_container_id():
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.radio[0].set_value("Predict").run()

    cargo_selector = next(item for item in app.selectbox if item.label == "Cargo type")
    assert tuple(cargo_selector.options) == load_cargo_types()
    assert not any("Container ID" in item.label for item in app.text_input)


def test_streamlit_dataset_and_performance_pages_render():
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    for page in ("Dataset", "Model Performance"):
        app.radio[0].set_value(page).run()
        assert not app.exception
        assert any(item.value == page for item in app.title)


def test_streamlit_ai_assistant_shows_groq_configuration(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.radio[0].set_value("AI Assistant").run()

    assert not app.exception
    assert any("Groq API is configured." in item.value for item in app.success)


def test_groq_service_uses_responses_api(monkeypatch):
    captured = {}

    class FakeResponse:
        output_text = "Groq explanation."

    class FakeResponses:
        def create(self, **kwargs):
            captured["request"] = kwargs
            return FakeResponse()

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.responses = FakeResponses()

    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-20b")
    monkeypatch.setattr("services.llm_service.OpenAI", FakeClient)

    answer = generate_ai_explanation("Explain this prediction.", {"temperature": 4.5})

    assert answer == "Groq explanation."
    assert captured["client"]["api_key"] == "test-key"
    assert captured["client"]["base_url"] == "https://api.groq.com/openai/v1"
    assert captured["request"]["model"] == "openai/gpt-oss-20b"
    assert "Explain this prediction." in captured["request"]["input"]
    assert '"temperature": 4.5' in captured["request"]["input"]
    assert "Current Prediction Summary" in captured["request"]["input"]
    assert "not causal effects or additive contributions" in captured["request"]["input"]
    assert "do not infer missing" in SYSTEM_PROMPT


def test_groq_service_requires_api_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    with pytest.raises(GroqConfigurationError, match="GROQ_API_KEY is not set"):
        generate_ai_explanation()
