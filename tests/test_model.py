import joblib
import numpy as np

from backend.schemas import PredictionRequest
from backend.services import PredictionService
from ml.common import FEATURES, ROOT, TARGET, load_dataset
from ml.train import make_pipeline
from sklearn.ensemble import (
    ExtraTreesRegressor,
    GradientBoostingRegressor,
    HistGradientBoostingRegressor,
    RandomForestRegressor,
)
from sklearn.linear_model import LinearRegression


def test_model_training_pipeline():
    data = load_dataset().sample(250, random_state=42)
    pipeline = make_pipeline(LinearRegression(), scale_numeric=True)

    pipeline.fit(data[FEATURES], data[TARGET])
    predictions = pipeline.predict(data[FEATURES].iloc[:10])

    assert predictions.shape == (10,)
    assert np.isfinite(predictions).all()


def test_all_regression_models_train_and_predict():
    data = load_dataset().sample(300, random_state=42)
    estimators = [
        LinearRegression(),
        RandomForestRegressor(n_estimators=10, n_jobs=1, random_state=42),
        GradientBoostingRegressor(n_estimators=10, random_state=42),
        ExtraTreesRegressor(n_estimators=10, n_jobs=1, random_state=42),
        HistGradientBoostingRegressor(max_iter=10, random_state=42),
    ]

    for estimator in estimators:
        pipeline = make_pipeline(estimator, scale_numeric=isinstance(estimator, LinearRegression))
        pipeline.fit(data[FEATURES], data[TARGET])
        predictions = pipeline.predict(data[FEATURES].iloc[:5])
        assert predictions.shape == (5,)
        assert np.isfinite(predictions).all()


def test_saved_model_prediction():
    service = PredictionService()
    model = joblib.load(ROOT / "artifacts" / "models" / "cold_chain_temperature_model.joblib")

    row = load_dataset().iloc[0]
    request = PredictionRequest(
        cargo_type=row["Cargo_Type"],
        ambient_temperature_c=row["Ambient_External_Temp_C"],
        set_point_c=row["Internal_Set_Point_C"],
        hvac_power_watts=row["HVAC_Power_Consumption_Watts"],
        seal_integrity=row["Seal_Integrity_Index"],
    )
    result = service.predict(request)

    assert np.isfinite(model.predict(service._features(request))).all()
    assert np.isfinite(result.predicted_internal_temperature_c)
    assert result.risk_level in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}


def test_saved_model_accuracy_across_cargo_categories():
    data = load_dataset()
    sample = data.groupby("Cargo_Type", group_keys=False).sample(n=200, random_state=42)
    model = joblib.load(ROOT / "artifacts" / "models" / "cold_chain_temperature_model.joblib")

    errors = np.abs(model.predict(sample[FEATURES]) - sample[TARGET].to_numpy())

    assert np.isfinite(errors).all()
    assert errors.mean() <= 0.2
    assert np.quantile(errors, 0.95) <= 0.45


def test_end_to_end_prediction_flow():
    data = load_dataset()
    row = data.iloc[123]
    model = joblib.load(ROOT / "artifacts" / "models" / "cold_chain_temperature_model.joblib")
    service = PredictionService()
    request = PredictionRequest(
        cargo_type=row["Cargo_Type"],
        ambient_temperature_c=row["Ambient_External_Temp_C"],
        set_point_c=row["Internal_Set_Point_C"],
        hvac_power_watts=row["HVAC_Power_Consumption_Watts"],
        seal_integrity=row["Seal_Integrity_Index"],
    )

    result = service.predict(request)

    assert np.isfinite(model.predict(service._features(request))).all()
    assert result.temperature_deviation_c == round(
        result.predicted_internal_temperature_c - request.set_point_c, 3
    )
    assert result.temperature_deviation_category
    assert 0 <= result.estimated_risk_index_pct <= 100
    assert result.risk_level
    assert result.recommendation
    assert result.explanation["top_factors"]
