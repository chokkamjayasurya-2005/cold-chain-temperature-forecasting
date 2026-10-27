from __future__ import annotations

import json
import time
from pathlib import Path

import joblib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import (
    ExtraTreesRegressor,
    GradientBoostingRegressor,
    HistGradientBoostingRegressor,
    RandomForestRegressor,
)
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import RandomizedSearchCV, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from ml.common import (
    CATEGORICAL_FEATURES,
    DATA_PATH,
    FEATURES,
    NUMERIC_FEATURES,
    ROOT,
    TARGET,
    create_data_quality_report,
    create_eda_plots,
    load_dataset,
)

SEED = 42


def make_pipeline(estimator, scale_numeric: bool = False) -> Pipeline:
    numeric_steps = [("imputer", SimpleImputer(strategy="median"))]
    if scale_numeric:
        numeric_steps.append(("scaler", StandardScaler()))
    categorical = Pipeline([
        ("imputer", SimpleImputer(strategy="most_frequent")),
        ("encoder", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
    ])
    preprocessing = ColumnTransformer(
        [
            ("numeric", Pipeline(numeric_steps), NUMERIC_FEATURES),
            ("categorical", categorical, CATEGORICAL_FEATURES),
        ],
        remainder="drop",
        sparse_threshold=0,
    )
    return Pipeline([("preprocessor", preprocessing), ("model", estimator)])


def metric_values(actual, predicted) -> dict:
    mse = mean_squared_error(actual, predicted)
    values = {
        "mae": float(mean_absolute_error(actual, predicted)),
        "mse": float(mse),
        "rmse": float(np.sqrt(mse)),
        "r2": float(r2_score(actual, predicted)),
    }
    actual_array = np.asarray(actual)
    if np.all(actual_array > 0) and np.all(actual_array != 0):
        values["mape_pct"] = float(np.mean(np.abs((actual_array - predicted) / actual_array)) * 100)
    else:
        values["mape_pct"] = None
        values["mape_note"] = "Not reported: the target includes negative temperatures, so percentage error is not meaningful."
    return values


def main() -> None:
    data = load_dataset()
    eda_dir = ROOT / "artifacts" / "eda"
    metrics_dir = ROOT / "artifacts" / "metrics"
    models_dir = ROOT / "artifacts" / "models"
    for directory in (eda_dir, metrics_dir, models_dir):
        directory.mkdir(parents=True, exist_ok=True)

    report = create_data_quality_report(data, metrics_dir)
    create_eda_plots(data, eda_dir)
    modeling_data = data[FEATURES + [TARGET]]
    train_validation, test = train_test_split(
        modeling_data,
        test_size=0.10,
        random_state=SEED,
        stratify=modeling_data["Cargo_Type"],
    )
    train, validation = train_test_split(
        train_validation,
        test_size=1 / 9,
        random_state=SEED,
        stratify=train_validation["Cargo_Type"],
    )
    x_train, y_train = train[FEATURES], train[TARGET]
    x_validation, y_validation = validation[FEATURES], validation[TARGET]
    x_test, y_test = test[FEATURES], test[TARGET]
    print(f"Rows: {len(data):,}; train: {len(train):,}; validation: {len(validation):,}; test: {len(test):,}")
    print(f"Target: {TARGET}; model inputs: {', '.join(FEATURES)}")

    factories = {
        "Linear Regression": lambda: make_pipeline(LinearRegression(), scale_numeric=True),
        "Random Forest": lambda: make_pipeline(
            RandomForestRegressor(n_estimators=100, min_samples_leaf=2, max_features=0.9,
                                  n_jobs=-1, random_state=SEED)
        ),
        "Gradient Boosting": lambda: make_pipeline(
            GradientBoostingRegressor(n_estimators=100, max_depth=2, learning_rate=0.08,
                                      random_state=SEED)
        ),
        "Extra Trees": lambda: make_pipeline(
            ExtraTreesRegressor(n_estimators=100, min_samples_leaf=2, max_features=0.9,
                               n_jobs=-1, random_state=SEED)
        ),
        "HistGradientBoosting": lambda: make_pipeline(
            HistGradientBoostingRegressor(max_iter=100, learning_rate=0.08,
                                          l2_regularization=1.0, random_state=SEED)
        ),
    }
    comparison = []
    fitted = {}
    for name, factory in factories.items():
        started = time.perf_counter()
        estimator = factory().fit(x_train, y_train)
        elapsed = time.perf_counter() - started
        values = metric_values(y_validation, estimator.predict(x_validation))
        row = {"model": name, **values, "training_seconds": elapsed, "tuned": False}
        comparison.append(row)
        fitted[name] = estimator
        print(f"{name}: validation RMSE={values['rmse']:.5f}, MAE={values['mae']:.5f}")

    tunable_names = [name for name in factories if name != "Linear Regression"]
    tuning_target = min(
        tunable_names,
        key=lambda name: next(row["rmse"] for row in comparison if row["model"] == name),
    )
    search_space = {
        "Random Forest": {
            "model__n_estimators": [80, 120, 160],
            "model__min_samples_leaf": [1, 2, 4],
            "model__max_features": [0.7, 0.9, 1.0],
        },
        "Gradient Boosting": {
            "model__n_estimators": [80, 120, 160],
            "model__max_depth": [1, 2, 3],
            "model__learning_rate": [0.03, 0.06, 0.1],
        },
        "Extra Trees": {
            "model__n_estimators": [80, 120, 160],
            "model__min_samples_leaf": [1, 2, 4],
            "model__max_features": [0.7, 0.9, 1.0],
        },
        "HistGradientBoosting": {
            "model__max_iter": [80, 120, 160],
            "model__max_leaf_nodes": [15, 31, 63],
            "model__learning_rate": [0.03, 0.06, 0.1],
        },
    }
    print(f"Tuning strongest tree candidate: {tuning_target} (3-fold CV, 4 randomized settings)")
    search = RandomizedSearchCV(
        factories[tuning_target](),
        param_distributions=search_space[tuning_target],
        n_iter=4,
        scoring="neg_root_mean_squared_error",
        cv=3,
        n_jobs=1,
        random_state=SEED,
        refit=True,
        return_train_score=False,
    )
    started = time.perf_counter()
    search.fit(x_train, y_train)
    tuning_seconds = time.perf_counter() - started
    tuned_validation_metrics = metric_values(y_validation, search.best_estimator_.predict(x_validation))
    comparison.append({
        "model": f"{tuning_target} (tuned)",
        **tuned_validation_metrics,
        "training_seconds": tuning_seconds,
        "tuned": True,
    })
    candidates = {**fitted, f"{tuning_target} (tuned)": search.best_estimator_}
    best_name = min(comparison, key=lambda row: (row["rmse"], row["mae"], -row["r2"]))["model"]
    selected = candidates[best_name]
    best_row = next(row for row in comparison if row["model"] == best_name)
    print(f"Selected: {best_name} (validation RMSE={best_row['rmse']:.5f})")

    final_started = time.perf_counter()
    selected.fit(pd.concat([x_train, x_validation]), pd.concat([y_train, y_validation]))
    final_training_seconds = time.perf_counter() - final_started
    test_prediction = selected.predict(x_test)
    test_metrics = metric_values(y_test, test_prediction)
    importance_result = permutation_importance(
        selected,
        x_test,
        y_test,
        scoring="neg_root_mean_squared_error",
        n_repeats=5,
        random_state=SEED,
        n_jobs=-1,
    )
    importances = [
        {"feature": feature, "importance_mean": float(mean), "importance_std": float(std)}
        for feature, mean, std in sorted(
            zip(FEATURES, importance_result.importances_mean, importance_result.importances_std),
            key=lambda pair: pair[1],
            reverse=True,
        )
    ]
    sample_size = min(500, len(x_test))
    sample = x_test.iloc[:sample_size].copy()
    sample["actual_temperature_c"] = y_test.iloc[:sample_size].to_numpy()
    sample["predicted_temperature_c"] = selected.predict(sample[FEATURES])
    sample["residual_c"] = sample["actual_temperature_c"] - sample["predicted_temperature_c"]
    sample.to_json(metrics_dir / "evaluation_samples.json", orient="records", indent=2)

    joblib.dump(selected, models_dir / "cold_chain_temperature_model.joblib")
    joblib.dump(selected.named_steps["preprocessor"], models_dir / "preprocessor.joblib")
    representative_index = (
        x_train["Internal_Set_Point_C"] - x_train["Internal_Set_Point_C"].median()
    ).abs().idxmin()
    comparison.sort(key=lambda row: (row["rmse"], row["mae"], -row["r2"]))
    comparison_payload = {
        "selection_rule": "Lowest validation RMSE, then MAE, then highest R2.",
        "selected_model": best_name,
        "validation_results": comparison,
        "tuning": {
            "candidate": tuning_target,
            "best_parameters": search.best_params_,
            "best_cross_validation_rmse": float(-search.best_score_),
            "cross_validation_folds": 3,
            "randomized_settings": 4,
        },
    }
    model_info = {
        "dataset_path": str(DATA_PATH.name),
        "dataset_rows": len(data),
        "train_rows": len(train),
        "validation_rows": len(validation),
        "test_rows": len(test),
        "target": TARGET,
        "features": FEATURES,
        "excluded_columns": ["Container_ID", "Forecasted_4Hr_Spoilage_Risk_Pct",
                             "Logistics_Action_Recommendation"],
        "split_seed": SEED,
        "split_method": "80% train, 10% validation, 10% test; seeded, stratified by Cargo_Type.",
        "representative_baseline": {
            feature: (
                str(x_train.loc[representative_index, feature])
                if feature in CATEGORICAL_FEATURES
                else float(x_train.loc[representative_index, feature])
            )
            for feature in FEATURES
        },
        "model": best_name,
        "validation_metrics": {key: best_row[key] for key in ("mae", "mse", "rmse", "r2", "mape_pct", "mape_note") if key in best_row},
        "test_metrics": test_metrics,
        "model_training_workflow_seconds": float(
            sum(row["training_seconds"] for row in comparison if not row["tuned"])
            + tuning_seconds
            + final_training_seconds
        ),
        "selected_model_refit_seconds": float(final_training_seconds),
        "tuning_seconds": float(tuning_seconds),
        "inference_ms_per_row": None,
        "feature_importance": importances,
        "risk_target_assessment": (
            "The dataset risk estimate is not used as a temperature input or presented as ground-truth spoilage. "
            f"Its Pearson correlation with {TARGET} is {float(data[[TARGET, 'Forecasted_4Hr_Spoilage_Risk_Pct']].corr().iloc[0, 1]):.4f}; "
            "it is treated as a dataset-provided downstream estimate."
        ),
        "data_quality": {
            "rows": report["rows"],
            "missing_values": report["missing_values"],
            "duplicate_rows": report["duplicate_rows"],
        },
        "dataset_risk_sample": data["Forecasted_4Hr_Spoilage_Risk_Pct"]
        .sample(min(500, len(data)), random_state=SEED)
        .tolist(),
        "map_e_note": test_metrics.get("mape_note"),
    }
    started = time.perf_counter()
    selected.predict(x_test.iloc[:100])
    inference_ms_per_row = (time.perf_counter() - started) * 1000 / min(100, len(x_test))
    model_info["inference_ms_per_row"] = float(inference_ms_per_row)
    (metrics_dir / "model_comparison.json").write_text(json.dumps(comparison_payload, indent=2), encoding="utf-8")
    pd.DataFrame(comparison).sort_values("rmse").to_csv(metrics_dir / "model_comparison.csv", index=False)
    (metrics_dir / "model_info.json").write_text(json.dumps(model_info, indent=2), encoding="utf-8")

    fig, axes = plt.subplots(1, 3, figsize=(17, 5))
    axes[0].scatter(y_test, test_prediction, alpha=0.35, s=12)
    low, high = min(y_test.min(), test_prediction.min()), max(y_test.max(), test_prediction.max())
    axes[0].plot([low, high], [low, high], linestyle="--", color="red")
    axes[0].set(title="Actual vs predicted", xlabel="Actual (C)", ylabel="Predicted (C)")
    residuals = y_test.to_numpy() - test_prediction
    axes[1].hist(residuals, bins=40)
    axes[1].set(title="Test residual distribution", xlabel="Actual - predicted (C)", ylabel="Count")
    axes[2].barh([item["feature"] for item in reversed(importances)],
                 [item["importance_mean"] for item in reversed(importances)])
    axes[2].set(title="Permutation feature importance", xlabel="Increase in RMSE (C)")
    fig.tight_layout()
    fig.savefig(eda_dir / "model_evaluation.png", dpi=150)
    plt.close(fig)

    for column, title, filename in [
        ("Ambient_External_Temp_C", "Ambient temperature vs prediction", "ambient_vs_prediction.png"),
        ("HVAC_Power_Consumption_Watts", "HVAC power vs prediction", "hvac_vs_prediction.png"),
        ("Seal_Integrity_Index", "Seal integrity vs prediction", "seal_vs_prediction.png"),
        ("Internal_Set_Point_C", "Prediction vs set point", "prediction_vs_set_point.png"),
    ]:
        fig, axis = plt.subplots(figsize=(8, 5))
        axis.scatter(x_test[column], test_prediction, alpha=0.35, s=12)
        axis.set(title=title, xlabel=column, ylabel="Predicted internal temperature (C)")
        fig.tight_layout()
        fig.savefig(eda_dir / filename, dpi=140)
        plt.close(fig)

    print(f"Test metrics: {json.dumps(test_metrics, sort_keys=True)}")
    print(f"Artifacts written under {ROOT / 'artifacts'}")


if __name__ == "__main__":
    main()
