import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ml.common import load_cargo_types


def _normalize_email(value: str) -> str:
    value = value.strip()
    pattern = r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"
    if not re.fullmatch(pattern, value):
        raise ValueError("Enter a valid email address.")
    return value.casefold()


class PredictionRequest(BaseModel):
    cargo_type: str = Field(min_length=1)
    ambient_temperature_c: float = Field(ge=-80, le=80)
    set_point_c: float = Field(ge=-80, le=20)
    hvac_power_watts: float = Field(ge=0, le=20000)
    seal_integrity: float = Field(ge=0, le=1)

    @field_validator("cargo_type")
    @classmethod
    def validate_cargo_type(cls, value: str) -> str:
        if value not in load_cargo_types():
            raise ValueError(f"Cargo type must be one of: {', '.join(load_cargo_types())}")
        return value


class FactorExplanation(BaseModel):
    feature: str
    effect_c: float
    direction: Literal["increases", "decreases", "no material change"]
    detail: str


class PredictionResponse(BaseModel):
    cargo_type: str
    observed_inputs: dict[str, float | str]
    predicted_internal_temperature_c: float
    temperature_deviation_c: float
    temperature_deviation_category: Literal[
        "Near Set Point",
        "Slightly Above Set Point",
        "Significantly Above Set Point",
        "Below Set Point",
    ]
    estimated_risk_index_pct: float
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    recommendation: str
    explanation: dict
    risk_disclaimer: str


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=1500)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    conversation: list[ChatMessage] = Field(default_factory=list, max_length=6)
    container_context: PredictionResponse


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=256)
    remember_me: bool = False


class RegistrationRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=120)
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$")
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=6, max_length=128)

    @field_validator("full_name", "username", "email")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field is required.")
        return value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return _normalize_email(value)


class PasswordResetRequestLink(BaseModel):
    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return _normalize_email(value)


class PasswordResetCompletion(BaseModel):
    token: str = Field(min_length=32, max_length=256)
    new_password: str = Field(min_length=6, max_length=128)


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$")
    email: str | None = Field(default=None, max_length=254)
    password: str = Field(min_length=6, max_length=128)
    role: Literal["ADMIN", "USER"]


class UserUpdateRequest(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    email: str | None = Field(default=None, max_length=254)
    role: Literal["ADMIN", "USER"] | None = None
    is_active: bool | None = None

    @field_validator("full_name")
    @classmethod
    def normalize_full_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Full name cannot be empty.")
        return value

    @field_validator("email")
    @classmethod
    def normalize_optional_email(cls, value: str | None) -> str | None:
        return _normalize_email(value) if value else None


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=6, max_length=128)


class PasswordResetRequest(BaseModel):
    new_password: str = Field(min_length=6, max_length=128)
