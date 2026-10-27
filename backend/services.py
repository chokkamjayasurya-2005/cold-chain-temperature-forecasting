import json
import os
from pathlib import Path
from typing import Any

import joblib
import pandas as pd

from ml.common import FEATURES, NUMERIC_FEATURES, ROOT
from backend.schemas import PredictionRequest, PredictionResponse

RISK_DISCLAIMER = (
    "This transparent 0-100 operational risk index is not a calibrated probability of spoilage "
    "or a scientifically validated safety threshold."
)


def classify_risk(score: float, thresholds: dict) -> str:
    if score >= thresholds["critical"]:
        return "CRITICAL"
    if score >= thresholds["high"]:
        return "HIGH"
    if score >= thresholds["medium"]:
        return "MEDIUM"
    return "LOW"


def classify_temperature_deviation(deviation: float, thresholds: dict) -> str:
    if deviation < 0:
        return "Below Set Point"
    if deviation >= thresholds["warning_deviation_c"]:
        return "Significantly Above Set Point"
    if deviation >= thresholds["monitor_deviation_c"]:
        return "Slightly Above Set Point"
    return "Near Set Point"


def recommendation_for(
    deviation: float,
    seal_integrity: float,
    score: float,
    risk_thresholds: dict,
    recommendation_thresholds: dict,
) -> str:
    magnitude = abs(deviation)
    if score >= risk_thresholds["critical"] or magnitude >= recommendation_thresholds["critical_deviation_c"]:
        return "CRITICAL — Immediate Logistics Intervention"
    if (
        score >= risk_thresholds["high"]
        or magnitude >= recommendation_thresholds["warning_deviation_c"]
        or seal_integrity < recommendation_thresholds["warning_seal_integrity"]
    ):
        return "WARNING — Inspect HVAC / Container"
    if (
        score >= risk_thresholds["medium"]
        or magnitude >= recommendation_thresholds["monitor_deviation_c"]
        or seal_integrity < recommendation_thresholds["monitor_seal_integrity"]
    ):
        return "MONITOR — Closely Monitor Temperature"
    return "SAFE — Maintain Course"


class PredictionService:
    def __init__(self) -> None:
        model_path = Path(os.getenv("MODEL_PATH", "artifacts/models/cold_chain_temperature_model.joblib"))
        if not model_path.is_absolute():
            model_path = ROOT / model_path
        info_path = ROOT / "artifacts" / "metrics" / "model_info.json"
        config_path = ROOT / "config" / "risk_thresholds.json"
        if not model_path.is_file():
            raise FileNotFoundError(f"Trained model not found at {model_path}. Run `python -m ml.train` first.")
        if not info_path.is_file():
            raise FileNotFoundError(f"Model report not found at {info_path}. Run `python -m ml.train` first.")
        if not config_path.is_file():
            raise FileNotFoundError(f"Risk configuration not found at {config_path}.")
        self.model = joblib.load(model_path)
        self.info = json.loads(info_path.read_text(encoding="utf-8"))
        self.thresholds = json.loads(config_path.read_text(encoding="utf-8"))
        self.baseline = self.info["representative_baseline"]

    @staticmethod
    def _features(inputs: PredictionRequest) -> pd.DataFrame:
        values = {
            "Cargo_Type": inputs.cargo_type,
            "Ambient_External_Temp_C": inputs.ambient_temperature_c,
            "Internal_Set_Point_C": inputs.set_point_c,
            "HVAC_Power_Consumption_Watts": inputs.hvac_power_watts,
            "Seal_Integrity_Index": inputs.seal_integrity,
        }
        return pd.DataFrame([{name: values[name] for name in FEATURES}], columns=FEATURES)

    def _explain(self, features: pd.DataFrame) -> list[dict[str, Any]]:
        baseline = pd.DataFrame([{name: self.baseline[name] for name in FEATURES}], columns=FEATURES)
        baseline_prediction = float(self.model.predict(baseline)[0])
        effects = []
        for feature in FEATURES:
            changed = baseline.copy()
            changed.loc[0, feature] = features.iloc[0][feature]
            effect = float(self.model.predict(changed)[0]) - baseline_prediction
            if abs(effect) < 0.005:
                direction = "no material change"
            else:
                direction = "increases" if effect > 0 else "decreases"
            detail = (
                f"Changing {feature} from a representative training baseline "
                f"{direction} the model output by {abs(effect):.3f} C."
                if direction != "no material change"
                else f"Changing {feature} from a representative training baseline has no material effect on the model output ({effect:+.3f} C)."
            )
            effects.append({
                "feature": feature,
                "effect_c": round(effect, 4),
                "direction": direction,
                "detail": detail,
            })
        return sorted(effects, key=lambda item: abs(item["effect_c"]), reverse=True)

    def predict(self, inputs: PredictionRequest) -> PredictionResponse:
        features = self._features(inputs)
        predicted = float(self.model.predict(features)[0])
        deviation = predicted - inputs.set_point_c
        score = (
            min(abs(deviation) / 3.0, 1.0) * 70.0
            + (1.0 - inputs.seal_integrity) * 30.0
        )
        category_thresholds = self.thresholds["risk_category_thresholds"]
        level = classify_risk(score, category_thresholds)
        recommendation = recommendation_for(
            deviation,
            inputs.seal_integrity,
            score,
            category_thresholds,
            self.thresholds["recommendation_thresholds"],
        )
        factors = self._explain(features)
        return PredictionResponse(
            cargo_type=inputs.cargo_type,
            observed_inputs={
                "ambient_temperature_c": inputs.ambient_temperature_c,
                "set_point_c": inputs.set_point_c,
                "hvac_power_watts": inputs.hvac_power_watts,
                "seal_integrity": inputs.seal_integrity,
            },
            predicted_internal_temperature_c=round(predicted, 3),
            temperature_deviation_c=round(deviation, 3),
            temperature_deviation_category=classify_temperature_deviation(
                deviation, self.thresholds["recommendation_thresholds"]
            ),
            estimated_risk_index_pct=round(score, 2),
            risk_level=level,
            recommendation=recommendation,
            explanation={
                "baseline_temperature_c": round(float(self.model.predict(
                    pd.DataFrame([{name: self.baseline[name] for name in FEATURES}], columns=FEATURES)
                )[0]), 3),
                "top_factors": factors[:5],
                "method": "One-feature-at-a-time model output changes relative to the training-set representative baseline; effects are not additive.",
            },
            risk_disclaimer=RISK_DISCLAIMER,
        )


def dashboard_metrics() -> dict:
    metrics_path = ROOT / "artifacts" / "metrics" / "model_info.json"
    comparison_path = ROOT / "artifacts" / "metrics" / "model_comparison.json"
    sample_path = ROOT / "artifacts" / "metrics" / "evaluation_samples.json"
    for path in (metrics_path, comparison_path, sample_path):
        if not path.is_file():
            raise FileNotFoundError(f"Training output missing at {path}. Run `python -m ml.train` first.")
    return {
        "model_info": json.loads(metrics_path.read_text(encoding="utf-8")),
        "comparison": json.loads(comparison_path.read_text(encoding="utf-8")),
        "evaluation_samples": json.loads(sample_path.read_text(encoding="utf-8")),
    }
