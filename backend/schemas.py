from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ml.common import load_cargo_types


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
