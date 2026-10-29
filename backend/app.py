from contextlib import asynccontextmanager
import csv
from functools import lru_cache
import hashlib
import io
import json
import os
import re
import secrets
import sqlite3
import smtplib
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import numpy as np
from starlette.middleware.sessions import SessionMiddleware

from backend import database, email_delivery
from backend.auth import (
    AdminUser,
    CurrentUser,
    CsrfProtected,
    DUMMY_PASSWORD_HASH,
    current_user,
    hash_password,
    verify_password,
)
from backend.llm import explain_prediction
from backend.schemas import (
    ChatRequest,
    LoginRequest,
    PasswordChangeRequest,
    PasswordResetCompletion,
    PasswordResetRequestLink,
    PasswordResetRequest,
    PredictionRequest,
    PredictionResponse,
    RegistrationRequest,
    UserCreateRequest,
    UserUpdateRequest,
)
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
session_secret = os.getenv("SESSION_SECRET")
if os.getenv("APP_ENV", "development").lower() == "production" and not session_secret:
    raise RuntimeError("SESSION_SECRET must be configured for production.")
session_secret = session_secret or secrets.token_urlsafe(48)
app.add_middleware(
    SessionMiddleware,
    secret_key=session_secret,
    session_cookie="cold_chain_session",
    max_age=30 * 24 * 60 * 60,
    same_site="lax",
    https_only=os.getenv("SESSION_COOKIE_SECURE", "").lower() == "true"
    or os.getenv("APP_ENV", "development").lower() == "production",
)
origins = [item.strip() for item in os.getenv("CORS_ORIGINS", "http://localhost:8000").split(",") if item.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)
app.mount("/static", StaticFiles(directory=ROOT / "frontend"), name="static")


@app.middleware("http")
async def apply_session_cookie_lifetime(request: Request, call_next):
    response = await call_next(request)
    session = request.scope.get("session", {})
    cookie = response.headers.get("set-cookie", "")
    if not session.get("remember_me") and cookie.startswith("cold_chain_session="):
        response.headers["set-cookie"] = re.sub(
            r";\s*Max-Age=\d+",
            "",
            cookie,
            flags=re.IGNORECASE,
        )
    return response



@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(ROOT / "frontend" / "index.html")


@app.get("/login", include_in_schema=False)
def login_page():
    return FileResponse(ROOT / "frontend" / "index.html")


@app.get("/signup", include_in_schema=False)
@app.get("/forgot-password", include_in_schema=False)
@app.get("/reset-password", include_in_schema=False)
def authentication_page():
    return FileResponse(ROOT / "frontend" / "index.html")


@app.get(
    "/overview",
    include_in_schema=False,
)
@app.get(
    "/prediction",
    include_in_schema=False,
)
def user_frontend_page():
    return FileResponse(ROOT / "frontend" / "index.html")


@app.get(
    "/analytics",
    include_in_schema=False,
)
@app.get(
    "/dataset",
    include_in_schema=False,
)
@app.get("/users", include_in_schema=False)
@app.get("/activity", include_in_schema=False)
@app.get("/user-details/{user_id:int}", include_in_schema=False)
def frontend_page():
    return FileResponse(ROOT / "frontend" / "index.html")


@app.get("/account", include_in_schema=False)
@app.get("/user", include_in_schema=False)
def user_page():
    return FileResponse(ROOT / "frontend" / "index.html")


@app.get("/admin", include_in_schema=False)
def admin_page():
    return FileResponse(ROOT / "frontend" / "index.html")


@app.get("/health")
def health():
    loaded = getattr(app.state, "prediction_service", None) is not None
    return {"status": "ok" if loaded else "not_ready", "model_loaded": loaded}


def _session_user(user: dict) -> dict:
    return {key: value for key, value in user.items() if key != "password_hash"}


def _client_key(request: Request) -> str:
    address = request.client.host if request.client else "unknown"
    return hashlib.sha256(address.encode("utf-8")).hexdigest()


@app.get("/auth/csrf")
def csrf_token(request: Request):
    token = request.session.get("csrf_token")
    if not isinstance(token, str):
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return {"csrf_token": token}


@app.post("/auth/login")
def login(
    credentials: LoginRequest,
    request: Request,
    _csrf: CsrfProtected,
):
    address_key = _client_key(request)
    if database.begin_login_attempt(credentials.username, address_key):
        database.log_activity(
            actor_user_id=None,
            action="login_throttled",
            target_type="account",
            target_id=hashlib.sha256(credentials.username.strip().casefold().encode("utf-8")).hexdigest(),
            outcome="failure",
            safe_metadata={"address_key": address_key},
        )
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")
    user = database.get_user_by_login_identifier(credentials.username)
    stored_hash = user["password_hash"] if user else DUMMY_PASSWORD_HASH
    valid_password = verify_password(credentials.password, stored_hash)
    if user is None or not user["is_active"] or not valid_password:
        database.log_activity(
            actor_user_id=None,
            action="login_failed",
            target_type="account",
            target_id=hashlib.sha256(credentials.username.strip().casefold().encode("utf-8")).hexdigest(),
            outcome="failure",
            safe_metadata={"address_key": address_key},
        )
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    database.clear_login_attempts(credentials.username, address_key)
    request.session.clear()
    request.session["user_id"] = user["id"]
    request.session["csrf_token"] = secrets.token_urlsafe(32)
    remembered = credentials.remember_me
    lifetime = timedelta(days=30) if remembered else timedelta(hours=8)
    request.session["remember_me"] = remembered
    request.session["session_expires_at"] = (
        datetime.now(timezone.utc) + lifetime
    ).isoformat()
    database.record_login(user["id"])
    updated_user = database.get_user_by_id(user["id"])
    request.session["password_changed_at"] = updated_user["password_changed_at"]
    database.log_activity(
        actor_user_id=user["id"],
        action="login_success",
        target_type="account",
        target_id=user["id"],
        safe_metadata={"role": user["role"]},
    )
    return {"user": _session_user(updated_user), "csrf_token": request.session["csrf_token"]}


@app.post("/auth/register", status_code=201)
def register(payload: RegistrationRequest, _csrf: CsrfProtected):
    if database.get_user_by_username(payload.username):
        raise HTTPException(status_code=409, detail="Username is already in use.")
    if database.get_user_by_email(payload.email):
        raise HTTPException(status_code=409, detail="Email address is already in use.")
    try:
        hashed = hash_password(payload.password)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    try:
        user_id = database.create_user(
            username=payload.username,
            email=payload.email,
            password_hash=hashed,
            role="USER",
            full_name=payload.full_name,
        )
    except sqlite3.IntegrityError as error:
        raise HTTPException(
            status_code=409,
            detail="Username or email address is already in use.",
        ) from error
    database.log_activity(
        actor_user_id=None,
        action="account_registered",
        target_type="user",
        target_id=user_id,
        safe_metadata={"role": "USER"},
    )
    user = database.get_user_by_id(user_id)
    return {"message": "Account created. You can now sign in.", "user": _session_user(user)}


RESET_REQUEST_MESSAGE = (
    "If an active account uses that email, reset instructions will be sent only "
    "if email delivery is configured and succeeds."
)


@app.post("/auth/password-reset/request", status_code=202)
def request_password_reset(
    payload: PasswordResetRequestLink,
    request: Request,
    _csrf: CsrfProtected,
):
    email_key = hashlib.sha256(payload.email.encode("utf-8")).hexdigest()
    if database.begin_login_attempt(f"password-reset:{email_key}", _client_key(request)):
        return {"message": RESET_REQUEST_MESSAGE}
    user = database.get_user_by_email(payload.email)
    if user is not None and user["is_active"] and email_delivery.email_delivery_configured():
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
        database.create_password_reset_token(user["id"], token_hash, expires_at)
        try:
            email_delivery.send_password_reset(payload.email, token)
        except (email_delivery.EmailConfigurationError, OSError, smtplib.SMTPException):
            database.invalidate_password_reset_token(token_hash)
            database.log_activity(
                actor_user_id=None,
                action="password_reset_delivery_failed",
                target_type="account",
                outcome="failure",
            )
        else:
            database.log_activity(
                actor_user_id=None,
                action="password_reset_requested",
                target_type="account",
            )
    return {"message": RESET_REQUEST_MESSAGE}


@app.post("/auth/password-reset/complete")
def complete_password_reset(payload: PasswordResetCompletion, _csrf: CsrfProtected):
    try:
        new_password_hash = hash_password(payload.new_password)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    token_hash = hashlib.sha256(payload.token.encode("utf-8")).hexdigest()
    user_id = database.consume_password_reset_token(token_hash, new_password_hash)
    if user_id is None:
        raise HTTPException(status_code=400, detail="This reset link is invalid or expired.")
    database.log_activity(
        actor_user_id=user_id,
        action="password_reset_completed",
        target_type="account",
        target_id=user_id,
    )
    return {"message": "Password reset complete. Sign in with your new password."}


@app.get("/auth/me")
def auth_me(user: CurrentUser):
    return {"user": _session_user(user)}


@app.post("/auth/logout")
def logout(request: Request, user: CurrentUser, _csrf: CsrfProtected):
    database.log_activity(
        actor_user_id=user["id"],
        action="logout",
        target_type="account",
        target_id=user["id"],
    )
    request.session.clear()
    return {"status": "ok"}


@app.post("/auth/change-password")
def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    user: CurrentUser,
    _csrf: CsrfProtected,
):
    if not verify_password(payload.current_password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")
    try:
        hashed = hash_password(payload.new_password)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    database.update_password(user["id"], hashed)
    updated_user = database.get_user_by_id(user["id"])
    remembered = bool(request.session.get("remember_me"))
    request.session.clear()
    request.session["user_id"] = updated_user["id"]
    request.session["csrf_token"] = secrets.token_urlsafe(32)
    request.session["remember_me"] = remembered
    lifetime = timedelta(days=30) if remembered else timedelta(hours=8)
    request.session["session_expires_at"] = (
        datetime.now(timezone.utc) + lifetime
    ).isoformat()
    request.session["password_changed_at"] = updated_user["password_changed_at"]
    database.log_activity(
        actor_user_id=user["id"],
        action="password_changed",
        target_type="account",
        target_id=user["id"],
    )
    return {"status": "ok", "csrf_token": request.session["csrf_token"]}


@app.get("/profile")
def profile(user: CurrentUser):
    return _session_user(user)


@app.patch("/profile")
def update_profile(
    payload: UserUpdateRequest,
    user: CurrentUser,
    _csrf: CsrfProtected,
):
    if payload.role is not None or payload.is_active is not None:
        raise HTTPException(status_code=403, detail="Role and account status are administrator-managed.")
    try:
        database.update_user(user["id"], {"email": payload.email})
    except sqlite3.IntegrityError as error:
        raise HTTPException(status_code=409, detail="Email address is already in use.") from error
    database.log_activity(
        actor_user_id=user["id"],
        action="profile_updated",
        target_type="account",
        target_id=user["id"],
        safe_metadata={"email_updated": payload.email is not None},
    )
    return _session_user(database.get_user_by_id(user["id"]))


@app.get("/user/dashboard")
def user_dashboard(user: CurrentUser):
    if user["role"] != "USER":
        raise HTTPException(status_code=403, detail="User dashboard is for USER accounts.")
    return {
        **database.dashboard_metrics_for_user(user["id"]),
        "recent_predictions": database.read_history(limit=8, user_id=user["id"]),
        "account": _session_user(user),
        "timezone": "UTC",
    }


@app.get("/admin/dashboard")
def admin_dashboard(_admin: AdminUser):
    return {
        **database.dashboard_metrics_for_user(None),
        "recent_predictions": database.read_history(limit=8),
        "recent_activity": database.list_activity_logs(
            search=None,
            action=None,
            outcome=None,
            start=None,
            end=None,
            offset=0,
            limit=8,
        )[0],
        "model_info": dashboard_metrics()["model_info"],
        "timezone": "UTC",
    }


@app.get("/admin/users")
def admin_users(_admin: AdminUser, search: str | None = Query(default=None, max_length=120)):
    return database.list_users(search)


@app.get("/admin/users/{user_id}")
def admin_user_details(user_id: int, _admin: AdminUser):
    user = database.get_user_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    return _session_user(user)


@app.post("/admin/users", status_code=201)
def admin_create_user(
    payload: UserCreateRequest,
    admin: AdminUser,
    _csrf: CsrfProtected,
):
    try:
        hashed = hash_password(payload.password)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    try:
        user_id = database.create_user(
            payload.username,
            payload.email,
            hashed,
            payload.role,
            must_change_password=True,
        )
    except sqlite3.IntegrityError as error:
        raise HTTPException(
            status_code=409,
            detail="Username or email address is already in use.",
        ) from error
    database.log_activity(
        actor_user_id=admin["id"],
        action="account_created",
        target_type="user",
        target_id=user_id,
        safe_metadata={"role": payload.role},
    )
    return _session_user(database.get_user_by_id(user_id))


@app.patch("/admin/users/{user_id}")
def admin_update_user(
    user_id: int,
    payload: UserUpdateRequest,
    admin: AdminUser,
    _csrf: CsrfProtected,
):
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=422, detail="Provide at least one editable field.")
    previous_account = database.get_user_by_id(user_id)
    if previous_account is None:
        raise HTTPException(status_code=404, detail="User not found.")
    if user_id == admin["id"] and (
        updates.get("role", previous_account["role"]) != "ADMIN"
        or updates.get("is_active", previous_account["is_active"]) is False
    ):
        raise HTTPException(
            status_code=409,
            detail="You cannot demote or deactivate your own administrator account.",
        )
    try:
        exists = database.update_user_guarded(user_id, updates)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except sqlite3.IntegrityError as error:
        raise HTTPException(status_code=409, detail="Email address is already in use.") from error
    if not exists:
        raise HTTPException(status_code=404, detail="User not found.")
    if "role" in updates and updates["role"] != previous_account["role"]:
        database.log_activity(
            actor_user_id=admin["id"],
            action="role_changed",
            target_type="user",
            target_id=user_id,
            safe_metadata={"previous_role": previous_account["role"], "role": updates["role"]},
        )
    if "is_active" in updates and updates["is_active"] != previous_account["is_active"]:
        database.log_activity(
            actor_user_id=admin["id"],
            action="account_activated" if updates["is_active"] else "account_deactivated",
            target_type="user",
            target_id=user_id,
            safe_metadata={"is_active": updates["is_active"]},
        )
    other_updates = set(updates) - {"role", "is_active"}
    if other_updates:
        database.log_activity(
            actor_user_id=admin["id"],
            action="account_updated",
            target_type="user",
            target_id=user_id,
            safe_metadata={"updated_fields": sorted(other_updates)},
        )
    return _session_user(database.get_user_by_id(user_id))


@app.post("/admin/users/{user_id}/reset-password")
def admin_reset_password(
    user_id: int,
    payload: PasswordResetRequest,
    admin: AdminUser,
    _csrf: CsrfProtected,
):
    if database.get_user_by_id(user_id) is None:
        raise HTTPException(status_code=404, detail="User not found.")
    try:
        hashed = hash_password(payload.new_password)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    database.update_password(user_id, hashed, must_change_password=True)
    database.log_activity(
        actor_user_id=admin["id"],
        action="password_reset",
        target_type="user",
        target_id=user_id,
    )
    return {"status": "ok", "must_change_password": True}


def run_prediction(inputs: PredictionRequest, user: dict) -> PredictionResponse:
    service: PredictionService = app.state.prediction_service
    result = service.predict(inputs)
    database.record_prediction(inputs, result, user["id"])
    database.log_activity(
        actor_user_id=user["id"],
        action="prediction_created",
        target_type="prediction",
        target_id=None,
        safe_metadata={"risk_level": result.risk_level},
    )
    return result


@app.post("/predict", response_model=PredictionResponse)
def predict(inputs: PredictionRequest, user: CurrentUser, _csrf: CsrfProtected):
    return run_prediction(inputs, user)


@app.post("/analyze")
def analyze(inputs: PredictionRequest, user: CurrentUser, _csrf: CsrfProtected):
    result = run_prediction(inputs, user)
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
def batch_predict(
    inputs: list[PredictionRequest],
    user: CurrentUser,
    _csrf: CsrfProtected,
):
    if not inputs:
        raise HTTPException(status_code=422, detail="Provide at least one container for batch prediction.")
    if len(inputs) > 1000:
        raise HTTPException(status_code=422, detail="Batch requests are limited to 1000 containers.")
    return [run_prediction(item, user) for item in inputs]


@app.get("/model-info")
def model_info(_admin: AdminUser):
    return dashboard_metrics()["model_info"]


@app.get("/options")
def options(_user: CurrentUser):
    return {"cargo_types": load_cargo_types()}


@app.get("/metrics")
def metrics(_admin: AdminUser):
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
def dataset_summary(_admin: AdminUser):
    thresholds = app.state.prediction_service.thresholds["risk_category_thresholds"]
    return _dataset_summary(thresholds["high"], thresholds["critical"])


@app.get("/history")
def history(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
):
    if "text/html" in request.headers.get("accept", ""):
        return FileResponse(ROOT / "frontend" / "index.html")
    user = current_user(request)
    return database.read_history(limit, user_id=None if user["role"] == "ADMIN" else user["id"])


@app.get("/admin/activity")
def admin_activity(
    _admin: AdminUser,
    search: str | None = Query(default=None, max_length=100),
    action: str | None = Query(default=None, max_length=100),
    outcome: str | None = Query(default=None, pattern="^(success|failure)$"),
    start: str | None = Query(default=None, max_length=32),
    end: str | None = Query(default=None, max_length=32),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
):
    rows, total = database.list_activity_logs(
        search=search,
        action=action,
        outcome=outcome,
        start=start,
        end=end,
        offset=offset,
        limit=limit,
    )
    return {"items": rows, "total": total, "offset": offset, "limit": limit}


@app.get("/admin/activity.csv")
def admin_activity_csv(
    _admin: AdminUser,
    search: str | None = Query(default=None, max_length=100),
    action: str | None = Query(default=None, max_length=100),
    outcome: str | None = Query(default=None, pattern="^(success|failure)$"),
    start: str | None = Query(default=None, max_length=32),
    end: str | None = Query(default=None, max_length=32),
):
    rows, _ = database.list_activity_logs(
        search=search,
        action=action,
        outcome=outcome,
        start=start,
        end=end,
        offset=0,
        limit=10000,
    )
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=(
            "id", "timestamp", "actor_user_id", "username", "action",
            "target_type", "target_id", "outcome", "safe_metadata",
        ),
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({**row, "safe_metadata": json.dumps(row["safe_metadata"])})
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=cold-chain-activity.csv"},
    )


@app.delete("/history")
def delete_history(user: CurrentUser, _csrf: CsrfProtected):
    user_id = None if user["role"] == "ADMIN" else user["id"]
    deleted = database.clear_history(user_id=user_id)
    database.log_activity(
        actor_user_id=user["id"],
        action="prediction_history_cleared",
        target_type="prediction_history",
        safe_metadata={"deleted_count": deleted, "scope": "all" if user_id is None else "own"},
    )
    return {"deleted": deleted}


@app.post("/chat")
def chat(request: ChatRequest, user: CurrentUser, _csrf: CsrfProtected):
    try:
        answer = explain_prediction(request)
    except GroqConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except GroqRequestError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return {"answer": answer, "source": "Groq"}
