from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "supply_chain_iot_cold_chain_temp_forecast_80k.csv"
TARGET = "Actual_Internal_Temp_C"
NUMERIC_FEATURES = [
    "Ambient_External_Temp_C",
    "Internal_Set_Point_C",
    "HVAC_Power_Consumption_Watts",
    "Seal_Integrity_Index",
]
CATEGORICAL_FEATURES = ["Cargo_Type"]
FEATURES = CATEGORICAL_FEATURES + NUMERIC_FEATURES
RISK_SOURCE = "Forecasted_4Hr_Spoilage_Risk_Pct"
ACTION_SOURCE = "Logistics_Action_Recommendation"


def load_dataset(path: Path = DATA_PATH) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(f"Dataset not found: {path}")
    frame = pd.read_csv(path)
    required = set(FEATURES + [TARGET, RISK_SOURCE, ACTION_SOURCE, "Container_ID"])
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Dataset is missing required columns: {', '.join(missing)}")
    return frame


@lru_cache(maxsize=1)
def load_cargo_types() -> tuple[str, ...]:
    if not DATA_PATH.is_file():
        raise FileNotFoundError(f"Dataset not found: {DATA_PATH}")
    cargo_types = pd.read_csv(DATA_PATH, usecols=["Cargo_Type"])["Cargo_Type"]
    categories = tuple(sorted(cargo_types.dropna().astype(str).unique()))
    if not categories:
        raise ValueError(f"No cargo types were found in the dataset: {DATA_PATH}")
    return categories


def create_data_quality_report(frame: pd.DataFrame, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    numeric = frame.select_dtypes(include="number")
    outliers = {}
    for column in numeric.columns:
        q1, q3 = numeric[column].quantile([0.25, 0.75])
        iqr = q3 - q1
        count = int(((numeric[column] < q1 - 1.5 * iqr) | (numeric[column] > q3 + 1.5 * iqr)).sum())
        outliers[column] = {
            "iqr_lower_fence": float(q1 - 1.5 * iqr),
            "iqr_upper_fence": float(q3 + 1.5 * iqr),
            "count_outside_fences": count,
        }
    report = {
        "rows": int(len(frame)),
        "columns": int(len(frame.columns)),
        "column_names": frame.columns.tolist(),
        "data_types": {name: str(dtype) for name, dtype in frame.dtypes.items()},
        "missing_values": {name: int(count) for name, count in frame.isna().sum().items()},
        "duplicate_rows": int(frame.duplicated().sum()),
        "unique_values": {name: int(frame[name].nunique(dropna=True)) for name in frame.columns},
        "numeric_summary": json.loads(numeric.describe().to_json()),
        "categorical_counts": {
            column: {str(key): int(value) for key, value in frame[column].value_counts(dropna=False).items()}
            for column in frame.select_dtypes(exclude="number").columns
            if column != "Container_ID"
        },
        "categorical_distribution_note": (
            "Container_ID is an identifier with a unique value per row; its distinct count is reported above, "
            "but its values are excluded from categorical frequency counts."
        ),
        "iqr_outlier_screen": outliers,
        "correlations": json.loads(numeric.corr().to_json()),
        "modeling_decisions": {
            "target": TARGET,
            "included_features": FEATURES,
            "excluded_identifier": "Container_ID is unique per row and has no temporal or predictive semantics.",
            "excluded_downstream_columns": [
                f"{RISK_SOURCE} is a precomputed downstream risk estimate, not established as available at temperature-prediction time.",
                f"{ACTION_SOURCE} is a downstream operational decision and would leak target-derived information.",
            ],
            "temporal_split": "No timestamp or reliably reconstructable time field exists; use a seeded stratified random split.",
            "outliers": "IQR fences are reported for inspection; observations are retained rather than dropped or clipped.",
        },
    }
    (output_dir / "data_quality_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def create_eda_plots(frame: pd.DataFrame, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    sns.set_theme(style="whitegrid", palette="deep")
    plots = [
        (TARGET, "Target: actual internal temperature (C)", "target_temperature_distribution.png"),
        ("Ambient_External_Temp_C", "Ambient external temperature (C)", "ambient_temperature_distribution.png"),
        ("Internal_Set_Point_C", "Internal set point (C)", "set_point_distribution.png"),
        ("HVAC_Power_Consumption_Watts", "HVAC power consumption (W)", "hvac_power_distribution.png"),
        ("Seal_Integrity_Index", "Seal integrity index", "seal_integrity_distribution.png"),
        (RISK_SOURCE, "Dataset-provided 4-hour risk estimate (%)", "dataset_risk_distribution.png"),
    ]
    for column, title, filename in plots:
        fig, axis = plt.subplots(figsize=(8, 5))
        sns.histplot(data=frame, x=column, bins=40, kde=column != "Internal_Set_Point_C", ax=axis)
        axis.set_title(title)
        fig.tight_layout()
        fig.savefig(output_dir / filename, dpi=140)
        plt.close(fig)
        fig, axis = plt.subplots(figsize=(8, 2.8))
        sns.boxplot(data=frame, x=column, ax=axis, color="#4c9b8b")
        axis.set_title(f"IQR outlier screen: {column}")
        fig.tight_layout()
        fig.savefig(output_dir / f"{column.lower()}_boxplot.png", dpi=140)
        plt.close(fig)

    risk_thresholds = json.loads(
        (ROOT / "config" / "risk_thresholds.json").read_text(encoding="utf-8")
    )["risk_category_thresholds"]
    risk_bands = pd.cut(
        frame[RISK_SOURCE],
        bins=[-float("inf"), risk_thresholds["medium"], risk_thresholds["high"],
              risk_thresholds["critical"], float("inf")],
        labels=["LOW", "MEDIUM", "HIGH", "CRITICAL"],
        right=False,
    )
    fig, axis = plt.subplots(figsize=(8, 5))
    risk_bands.value_counts(sort=False).plot.bar(ax=axis, color="#4c9b8b")
    axis.set_title("Bands of dataset-provided risk estimates (configurable; not ground truth)")
    axis.set_xlabel("Band")
    axis.set_ylabel("Records")
    fig.tight_layout()
    fig.savefig(output_dir / "risk_level_distribution.png", dpi=140)
    plt.close(fig)

    fig, axis = plt.subplots(figsize=(8, 5))
    order = frame["Cargo_Type"].value_counts().index
    sns.countplot(data=frame, x="Cargo_Type", order=order, ax=axis)
    axis.set_title("Container counts by cargo type")
    axis.tick_params(axis="x", rotation=15)
    fig.tight_layout()
    fig.savefig(output_dir / "cargo_type_distribution.png", dpi=140)
    plt.close(fig)

    numeric = frame.select_dtypes(include="number")
    fig, axis = plt.subplots(figsize=(10, 8))
    sns.heatmap(numeric.corr(), annot=True, fmt=".2f", cmap="coolwarm", center=0, ax=axis)
    axis.set_title("Numeric feature correlation (includes target and downstream risk)")
    fig.tight_layout()
    fig.savefig(output_dir / "correlation_heatmap.png", dpi=140)
    plt.close(fig)

    for column in NUMERIC_FEATURES:
        fig, axis = plt.subplots(figsize=(8, 5))
        sns.scatterplot(data=frame.sample(min(4000, len(frame)), random_state=42), x=column, y=TARGET,
                        hue="Cargo_Type", alpha=0.45, s=16, ax=axis)
        axis.set_title(f"{column} vs actual internal temperature")
        fig.tight_layout()
        fig.savefig(output_dir / f"{column.lower()}_vs_target.png", dpi=140)
        plt.close(fig)

    fig, axis = plt.subplots(figsize=(8, 5))
    sns.boxplot(data=frame, x="Cargo_Type", y=TARGET, ax=axis)
    axis.set_title("Actual temperature by cargo type")
    fig.tight_layout()
    fig.savefig(output_dir / "target_by_cargo_boxplot.png", dpi=140)
    plt.close(fig)
