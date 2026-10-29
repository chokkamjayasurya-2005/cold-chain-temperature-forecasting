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
- `/users` — administrator-only account search and management
- `/user-details/{user_id}` — administrator-only account details and password management
- `/activity` — administrator-only activity log
- `/account` — personal profile and password settings

The sidebar remains available across pages, and prediction history is stored locally in SQLite. The dashboard requires a signed-in account. Follow-up questions on the prediction page use the existing optional Groq integration and the latest prediction as context; they require `GROQ_API_KEY`. Responses are rendered as safe Markdown and are instructed to distinguish model estimates from observed inputs, avoid unsupported causal explanations, and note limitations. The overview, analytics, dataset, history, and prediction features work without that key.

### Accounts and roles

The web dashboard uses administrator-managed `ADMIN` and `USER` accounts. There is no default username or password. Create the first administrator interactively after configuring `.env` and before starting the web server:

```powershell
Copy-Item .env.example .env
python -m backend.bootstrap_admin
```

The bootstrap command prompts for a username and password without echoing the password. Passwords must be at least 6 characters and include at least three of lowercase letters, uppercase letters, numbers, and symbols. Bootstrap is refused once an active administrator exists.

Administrators can search accounts by username, full name, email, or ID; open a separate account profile; edit account fields; assign roles; activate/deactivate accounts; and reset passwords. Self-demotion, self-deactivation, and removal of the last active administrator are blocked. Newly created and reset accounts must change their temporary password at their next sign-in. Users can update their own email and password, make predictions, and view or clear only their own prediction history. Passwords must be at least 6 characters and include at least three of lowercase letters, uppercase letters, numbers, and symbols. Changing or resetting a password invalidates existing sessions for that account. Administrator-only analytics, dataset, user-management, and activity-log endpoints are enforced by the server, not just hidden in the interface.

The GenAI API status is displayed as **NOT CONNECTED** until the application implements and successfully completes an actual connection check. Follow-up chat remains an optional feature; its availability alone is not represented as a verified connection status.

Self-registration is available at `/signup`; each public registration is assigned `USER` by the backend. Sign-in accepts either username or email. “Remember me” extends the authenticated session to 30 days; otherwise it expires after eight hours. Password reset is single-use, expires after 30 minutes, and stores only a SHA-256 digest of the random reset token. The reset token is sent in the URL fragment so it is not included in HTTP request paths or application access logs.

Password reset email requires `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`, and `APP_BASE_URL`. Set `SMTP_USERNAME` and `SMTP_PASSWORD` together for an authenticated relay; STARTTLS is enabled by default. Local development can use a local Mailpit inbox on port 1025 with `SMTP_STARTTLS=false` and `APP_ENV=development`, then open the Mailpit web inbox (commonly port 8025). Never disable STARTTLS outside development. Without mail configuration, reset requests return a non-enumerating message and no reset token is created or exposed; the UI does not claim that an email was sent.

For optional development test accounts, run `python -m backend.seed_dev_accounts`. It creates only missing `admin`/`testuser` accounts, generates a secure password only for each account it creates, and prints that password once. Existing accounts—including their email, role, and password—are left unchanged. This utility is for local development only.

If an account's current temporary password is unavailable, an operator with authorized access to the application host can reset that existing account without creating a duplicate or changing the database. The command prompts for account confirmation and for a new password without echoing it, stores only its Argon2 hash, and requires a password change at next sign-in:

```powershell
python -m backend.reset_user_password --username admin
```

Use the account's own temporary password when signing in to its forced password-change screen. Each account has a distinct password; the screen identifies which username is signed in.

On the first startup against an existing prediction database, the app adds account and audit tables and a nullable owner column. It creates a sibling `*.pre-rbac.bak` backup before changing a legacy prediction table. Existing rows remain unowned and are visible only in the administrator's global history; user accounts see only predictions created under their own account.

Sessions use signed, HTTP-only, same-site cookies that expire after eight hours. Mutating requests also require a CSRF token. Development may use an ephemeral session secret; for production, set `APP_ENV=production`, configure a long random `SESSION_SECRET`, enable HTTPS, and set `CORS_ORIGINS` to the exact trusted web origin. Production mode requires a configured secret and automatically marks the session cookie secure. Generate a secret locally with:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Keep `.env` private and never commit real passwords or session secrets.

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
