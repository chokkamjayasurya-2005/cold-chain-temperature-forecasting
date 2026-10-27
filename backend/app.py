from contextlib import asynccontextmanager
from functools import lru_cache
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import numpy as np
import pandas as pd

from backend import database
from backend.llm import explain_prediction
from backend.schemas import ChatRequest, PredictionRequest, PredictionResponse
from backend.services import PredictionService, dashboard_metrics
from ml.common import ROOT, RISK_SOURCE, TARGET, load_cargo_types, load_dataset
from services.llm_service import GroqConfigurationError, GroqRequestError

load_dotenv(ROOT / ".env")


@asynccontextmanager
async def lifespan(application: FastAPI):
    application.state.prediction_service = PredictionService()
    database.initialize_database()
    yield


app = FastAPI(
    title="Cold Chain Temperature Forecasting",
    description="Temperature predictions, transparent risk scores, recommendations, and model explanations.",
    version="1.0.0",
    lifespan=lifespan,
)
origins = [item.strip() for item in os.getenv("CORS_ORIGINS", "http://localhost:8000").split(",") if item.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)
app.mount("/static", StaticFiles(directory=ROOT / "frontend"), name="static")


@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(ROOT / "frontend" / "index.html")


@app.get(
    "/overview",
    include_in_schema=False,
)
@app.get(
    "/prediction",
    include_in_schema=False,
)
@app.get(
    "/analytics",
    include_in_schema=False,
)
@app.get(
    "/dataset",
    include_in_schema=False,
)
def frontend_page():
    return FileResponse(ROOT / "frontend" / "index.html")


@app.get("/health")
def health():
    loaded = getattr(app.state, "prediction_service", None) is not None
    return {"status": "ok" if loaded else "not_ready", "model_loaded": loaded}


def run_prediction(inputs: PredictionRequest) -> PredictionResponse:
    service: PredictionService = app.state.prediction_service
    result = service.predict(inputs)
    database.record_prediction(inputs, result)
    return result


@app.post("/predict", response_model=PredictionResponse)
def predict(inputs: PredictionRequest):
    return run_prediction(inputs)


@app.post("/analyze")
def analyze(inputs: PredictionRequest):
    result = run_prediction(inputs)
    return {
        "prediction": result,
        "analysis": {
            "temperature_assessment": (
                f"Model prediction is {result.predicted_internal_temperature_c:.2f} C, "
                f"{abs(result.temperature_deviation_c):.2f} C "
                f"{'above' if result.temperature_deviation_c >= 0 else 'below'} the set point."
            ),
            "main_factors": result.explanation["top_factors"][:3],
            "important_limitation": result.risk_disclaimer,
        },
    }


@app.post("/batch-predict", response_model=list[PredictionResponse])
def batch_predict(inputs: list[PredictionRequest]):
    if not inputs:
        raise HTTPException(status_code=422, detail="Provide at least one container for batch prediction.")
    if len(inputs) > 1000:
        raise HTTPException(status_code=422, detail="Batch requests are limited to 1000 containers.")
    return [run_prediction(item) for item in inputs]


@app.get("/model-info")
def model_info():
    return dashboard_metrics()["model_info"]


@app.get("/options")
def options():
    return {"cargo_types": load_cargo_types()}


@app.get("/metrics")
def metrics():
    return dashboard_metrics()


@lru_cache(maxsize=1)
def _dataset_summary(high_threshold: float, critical_threshold: float) -> dict:
    data = load_dataset()
    numeric = data.select_dtypes(include="number").drop(columns=["Container_ID"], errors="ignore")
    summary = numeric.describe().T
    numeric_summary = [
        {"column": name, **{key: float(value) for key, value in values.items()}}
        for name, values in summary.iterrows()
    ]
    histograms = {}
    for key, column in (
        ("temperature", TARGET),
        ("ambient_temperature", "Ambient_External_Temp_C"),
        ("spoilage_risk", RISK_SOURCE),
    ):
        counts, edges = np.histogram(data[column].to_numpy(), bins=18)
        histograms[key] = {
            "counts": counts.tolist(),
            "edges": edges.tolist(),
        }
    sample_step = max(1, len(data) // 500)
    relationship_sample = data.iloc[::sample_step][
        ["Ambient_External_Temp_C", TARGET]
    ].head(500)
    return {
        "rows": int(len(data)),
        "column_count": int(data.shape[1]),
        "duplicate_rows": int(data.duplicated().sum()),
        "columns": [
            {
                "name": column,
                "data_type": str(data[column].dtype),
                "missing_values": int(data[column].isna().sum()),
                "unique_values": int(data[column].nunique()),
            }
            for column in data.columns
        ],
        "numeric_summary": numeric_summary,
        "sample_records": data.drop(columns=["Container_ID"]).head(10).to_dict("records"),
        "cargo_distribution": {
            str(name): int(count) for name, count in data["Cargo_Type"].value_counts().items()
        },
        "histograms": histograms,
        "feature_relationship_sample": [
            {
                "ambient_temperature_c": float(row["Ambient_External_Temp_C"]),
                "actual_temperature_c": float(row[TARGET]),
            }
            for _, row in relationship_sample.iterrows()
        ],
        "average_temperature_c": float(data[TARGET].mean()),
        "average_spoilage_risk_pct": float(data[RISK_SOURCE].mean()),
        "high_risk_count": int((data[RISK_SOURCE] >= high_threshold).sum()),
        "critical_risk_count": int((data[RISK_SOURCE] >= critical_threshold).sum()),
    }


@app.get("/dataset-summary")
def dataset_summary():
    thresholds = app.state.prediction_service.thresholds["risk_category_thresholds"]
    return _dataset_summary(thresholds["high"], thresholds["critical"])


@app.get("/history")
def history(request: Request, limit: int = Query(default=50, ge=1, le=500)):
    if "text/html" in request.headers.get("accept", ""):
        return FileResponse(ROOT / "frontend" / "index.html")
    return database.read_history(limit)


@app.delete("/history")
def delete_history():
    return {"deleted": database.clear_history()}


@app.post("/chat")
def chat(request: ChatRequest):
    try:
        answer = explain_prediction(request)
    except GroqConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except GroqRequestError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return {"answer": answer, "source": "Groq"}
