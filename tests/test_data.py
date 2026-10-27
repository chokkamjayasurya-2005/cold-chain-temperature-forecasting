from ml.common import (
    CATEGORICAL_FEATURES,
    DATA_PATH,
    FEATURES,
    NUMERIC_FEATURES,
    TARGET,
    load_cargo_types,
    load_dataset,
)


def test_dataset_shape_columns_and_quality():
    data = load_dataset()

    assert DATA_PATH.is_file()
    assert data.shape == (80_000, 9)
    assert TARGET in data.columns
    assert data.isna().sum().sum() == 0
    assert data.duplicated().sum() == 0
    assert data["Container_ID"].is_unique
    assert (data["HVAC_Power_Consumption_Watts"] >= 0).all()
    assert data["Seal_Integrity_Index"].between(0, 1).all()
    assert set(data["Cargo_Type"]) == set(load_cargo_types())
    assert all(data[column].dtype.kind in "if" for column in NUMERIC_FEATURES + [TARGET])
    assert CATEGORICAL_FEATURES == ["Cargo_Type"]


def test_training_features_exclude_target_and_downstream_fields():
    data = load_dataset()

    assert TARGET not in FEATURES
    assert "Forecasted_4Hr_Spoilage_Risk_Pct" not in FEATURES
    assert "Logistics_Action_Recommendation" not in FEATURES
    assert "Container_ID" not in FEATURES
    assert set(FEATURES).issubset(data.columns)
