# Cold Chain Temperature Forecasting & Spoilage Risk Analysis

A local cold-chain application with a multipage FastAPI web dashboard and a Streamlit dashboard. Both use a trained machine-learning model to predict container temperature, explain important model inputs, and present clearly qualified operational risk indicators.

## Problem statement

The application estimates `Actual_Internal_Temp_C` from container telemetry. It also reports deviation from the set point, a transparent risk index, and configurable logistics guidance. It is a demonstration, not certified operational or regulatory advice.

## Dataset analysis

The supplied CSV contains **80,000 rows and 9 columns**. It has no missing values or duplicate rows, and no timestamp column. For public sharing, the `Container_ID` values have been replaced with synthetic sequential identifiers; they remain unique but carry no identifying or predictive information. `Cargo_Type` has three categories:

- `Biopharma_Vaccines`: 26,703 records
- `Fresh_Produce`: 26,674 records
- `Frozen_Seafood`: 26,623 records

The target is `Actual_Internal_Temp_C`. Model inputs are `Cargo_Type`, `Ambient_External_Temp_C`, `Internal_Set_Point_C`, `HVAC_Power_Consumption_Watts`, and `Seal_Integrity_Index`. The synthetic identifier is excluded. `Forecasted_4Hr_Spoilage_Risk_Pct` and `Logistics_Action_Recommendation` are also excluded because they are downstream estimates/actions and may leak information.

The training workflow writes a detailed data-quality report and EDA visualizations to `artifacts/metrics/` and `artifacts/eda/`. IQR screening reports 2,873 seal-integrity values and 626 dataset-risk values outside their respective fences; these valid observations are retained rather than silently clipped or removed.

## Machine learning

The training pipeline uses a `ColumnTransformer` and `Pipeline`, with median numeric imputation, most-frequent categorical imputation, one-hot encoding, and scaling for linear regression. A seeded, cargo-stratified 80% / 10% / 10% train/validation/test split is used because there is no time column. The pipeline compares Linear Regression, Random Forest, Gradient Boosting, Extra Trees, and HistGradientBoosting, then performs a bounded randomized search on the strongest tree candidate.

Measured results from the current saved training run:

| Measure | Result |
| --- | ---: |
| Selected model | Linear Regression |
| Held-out test MAE | 0.16138 °C |
| Held-out test RMSE | 0.20195 °C |
| Held-out test R² | 0.99959 |

The model can be retrained with `python -m ml.train`. The app loads the saved model at startup and does not retrain during prediction. The actual model, preprocessing pipeline, comparison metrics, permutation importance, and evaluation samples are saved under `artifacts/`.

## Spoilage risk and recommendations

The dataset does not provide independently verified spoilage outcomes. Its risk field is not treated as ground truth. The prediction workflow instead derives a configurable 0–100 **operational risk index** from model temperature deviation and seal integrity. This is not a calibrated spoilage probability or a validated scientific threshold. Risk bands and recommendation boundaries are configurable in `config/risk_thresholds.json`.

## Explainability

The app displays permutation feature importance and one-feature-at-a-time changes to actual fitted-model predictions relative to a representative training baseline. These comparisons are descriptive, not causal, and are not additive explanations.

## Web dashboard

The FastAPI application serves a separate page for each area of the dashboard:

- `/` — operational overview and recent predictions
- `/prediction` — temperature prediction form, model explanation, and contextual follow-up chat
- `/analytics` — held-out model metrics, comparison, and charts
- `/history` — searchable, filterable SQLite prediction history
- `/dataset` — data quality, statistics, sample records, and distributions

The sidebar remains available across pages, and prediction history is stored locally in SQLite. Follow-up questions on the prediction page use the existing optional Groq integration and the latest prediction as context; they require `GROQ_API_KEY`. The overview, analytics, dataset, history, and prediction features work without that key.

## Streamlit dashboard

The Streamlit app includes:

- **Home:** dataset statistics and high/critical dataset-risk-threshold counts
- **Predict:** input form, model prediction, deviation, risk index, recommendation, and model explanation
- **Dataset:** shape, types, missing values, summary statistics, records, and distributions
- **Model Performance:** validation comparison, held-out metrics, feature importance, actual-vs-predicted, and residual charts
- **AI Assistant:** Groq-powered explanations using the OpenAI-compatible Responses API

### Groq API configuration

The AI assistant uses Groq's OpenAI-compatible API. Copy `.env.example` to `.env`, then set `GROQ_API_KEY` in `.env`; do not commit or share the key. `GROQ_MODEL` defaults to `openai/gpt-oss-20b` and can be changed in `.env`. The application reads the key locally and sends requests only when the AI assistant is used.

The cargo-type selector in both interfaces is populated from the dataset rather than a hardcoded list. Retrain the model after adding new cargo categories so they are represented in training. Container IDs are not collected or returned by the application. The prediction tests exercise every cargo type across minimum/maximum combinations of the numeric inputs, plus typical median inputs; a separate sample-accuracy check evaluates 200 rows per cargo type.

## Installation

Requires Python 3.10+ and the supplied CSV at the project root.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Train the model

```powershell
python -m ml.train
```

This generates EDA plots, data-quality and evaluation metrics, and the saved model artifacts.

## Run tests

```powershell
pytest -v
```

The test report is written to `artifacts/test_report.txt`.

## Run the application

Start the multipage web dashboard:

```powershell
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000` in a browser.

To run the Streamlit dashboard instead:

```powershell
streamlit run app.py
```

The FastAPI service also exposes prediction and data endpoints for API-based use:

```powershell
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

The FastAPI `/chat` endpoint, prediction-page follow-up chat, and Streamlit AI Assistant use the optional Groq integration. Chat requests require `GROQ_API_KEY`; other dashboard features do not.

## Limitations and future work

The dataset has no timestamp or independent spoilage outcomes; random splitting does not measure future-time generalization. Risk and logistics thresholds are configurable demonstration defaults. Production use would require time-indexed external validation, independent outcome labels, domain review, and approved operational controls.

Possible future enhancements include real-time IoT telemetry, time-series forecasting, validated alerts, and cloud deployment.
