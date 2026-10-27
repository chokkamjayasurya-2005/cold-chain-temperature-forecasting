from __future__ import annotations

import os

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

from backend.schemas import PredictionRequest
from backend.services import PredictionService, dashboard_metrics
from ml.common import DATA_PATH, RISK_SOURCE, TARGET, load_dataset
from services.llm_service import (
    GroqConfigurationError,
    GroqRequestError,
    generate_ai_explanation,
)


@st.cache_data
def get_dataset() -> pd.DataFrame:
    return load_dataset()


@st.cache_resource
def get_prediction_service() -> PredictionService:
    return PredictionService()


def show_home(data: pd.DataFrame, model_info: dict) -> None:
    st.title("Cold Chain Temperature Forecasting")
    st.write(
        "Estimate internal container temperature from operating telemetry, review "
        "temperature deviation, and explore a transparent dataset-based risk estimate."
    )

    high_risk_threshold = get_prediction_service().thresholds["risk_category_thresholds"]["high"]
    metrics = st.columns(4)
    metrics[0].metric("Dataset records", f"{len(data):,}")
    metrics[1].metric("Average actual temperature", f"{data[TARGET].mean():.2f} °C")
    metrics[2].metric("Average ambient temperature", f"{data['Ambient_External_Temp_C'].mean():.2f} °C")
    metrics[3].metric("Average dataset risk estimate", f"{data[RISK_SOURCE].mean():.2f}%")

    high_risk_count = int((data[RISK_SOURCE] >= high_risk_threshold).sum())
    st.metric("Records at HIGH/CRITICAL dataset-risk thresholds", f"{high_risk_count:,}")
    st.caption(
        "The dataset-provided risk estimate and its configurable bands are not "
        "independently validated spoilage probabilities."
    )
    st.info(
        f"Model: {model_info['model']} · Held-out test RMSE: "
        f"{model_info['test_metrics']['rmse']:.4f} °C"
    )
    st.caption(f"Dataset file: {DATA_PATH.name}")


def show_prediction(data: pd.DataFrame, service: PredictionService) -> None:
    st.title("Predict Temperature")
    st.caption(
        "Inputs are restricted to observed dataset ranges. Prediction is performed "
        "by the saved model; this page does not retrain it."
    )
    with st.form("prediction_form"):
        left, right = st.columns(2)
        cargo_type = left.selectbox("Cargo type", sorted(data["Cargo_Type"].unique()))
        ambient = right.number_input(
            "Ambient external temperature (°C)",
            min_value=float(data["Ambient_External_Temp_C"].min()),
            max_value=float(data["Ambient_External_Temp_C"].max()),
            value=float(data["Ambient_External_Temp_C"].median()),
        )
        set_points = sorted(data["Internal_Set_Point_C"].unique())
        set_point = left.selectbox("Internal set point (°C)", set_points)
        hvac = right.number_input(
            "HVAC power consumption (W)",
            min_value=float(data["HVAC_Power_Consumption_Watts"].min()),
            max_value=float(data["HVAC_Power_Consumption_Watts"].max()),
            value=float(data["HVAC_Power_Consumption_Watts"].median()),
        )
        seal = left.number_input(
            "Seal integrity index",
            min_value=float(data["Seal_Integrity_Index"].min()),
            max_value=float(data["Seal_Integrity_Index"].max()),
            value=float(data["Seal_Integrity_Index"].median()),
            step=0.001,
            format="%.3f",
        )
        submitted = st.form_submit_button("Predict Temperature", type="primary")

    if submitted:
        request = PredictionRequest(
            cargo_type=cargo_type,
            ambient_temperature_c=ambient,
            set_point_c=set_point,
            hvac_power_watts=hvac,
            seal_integrity=seal,
        )
        st.session_state["prediction_result"] = service.predict(request).model_dump()

    result = st.session_state.get("prediction_result")
    if not result:
        return
    st.subheader(f"Predicted internal temperature: {result['predicted_internal_temperature_c']:.2f} °C")
    cards = st.columns(4)
    cards[0].metric("Set point", f"{result['observed_inputs']['set_point_c']:.2f} °C")
    cards[1].metric("Temperature deviation", f"{result['temperature_deviation_c']:+.2f} °C")
    cards[2].metric("Operational risk index", f"{result['estimated_risk_index_pct']:.1f}%")
    cards[3].metric("Risk level", result["risk_level"])
    st.write(f"**Temperature status:** {result['temperature_deviation_category']}")
    st.warning(result["recommendation"])
    st.caption(result["risk_disclaimer"])

    st.markdown("#### Why did the model predict this temperature?")
    st.caption(result["explanation"]["method"])
    for factor in result["explanation"]["top_factors"]:
        st.write(f"**{factor['feature']}** — {factor['detail']}")


def show_dataset(data: pd.DataFrame) -> None:
    st.title("Dataset")
    st.write(f"**Shape:** {data.shape[0]:,} rows × {data.shape[1]} columns")
    st.markdown("#### Data types and missing values")
    quality = pd.DataFrame({
        "data_type": data.dtypes.astype(str),
        "missing_values": data.isna().sum(),
        "unique_values": data.nunique(),
    })
    st.dataframe(quality, use_container_width=True)
    st.markdown("#### Numerical summary")
    st.dataframe(data.describe().T, use_container_width=True)
    st.markdown("#### First rows")
    st.dataframe(data.head(10), use_container_width=True)

    columns = st.columns(2)
    for column, title in [
        (TARGET, "Actual internal temperature (°C)"),
        ("Ambient_External_Temp_C", "Ambient temperature (°C)"),
        ("HVAC_Power_Consumption_Watts", "HVAC power (W)"),
        (RISK_SOURCE, "Dataset-based spoilage risk estimate (%)"),
    ]:
        figure, axis = plt.subplots(figsize=(7, 3))
        data[column].plot.hist(bins=35, ax=axis, color="#278678")
        axis.set_title(title)
        axis.set_ylabel("Records")
        figure.tight_layout()
        columns[0 if column in (TARGET, "HVAC_Power_Consumption_Watts") else 1].pyplot(figure)
        plt.close(figure)

    figure, axis = plt.subplots(figsize=(8, 4))
    data["Cargo_Type"].value_counts().plot.bar(ax=axis, color="#278678")
    axis.set_title("Cargo type distribution")
    axis.set_ylabel("Records")
    figure.tight_layout()
    st.pyplot(figure)
    plt.close(figure)


def show_model_performance(metrics: dict) -> None:
    info = metrics["model_info"]
    st.title("Model Performance")
    st.write(f"**Selected model:** {info['model']}")
    st.write(f"**Target:** `{info['target']}`")
    st.write("**Training features:** " + ", ".join(f"`{feature}`" for feature in info["features"]))
    st.caption(
        "Container ID, dataset-provided risk estimate, and logistics recommendation "
        "are excluded from model inputs to avoid identifier use and downstream leakage."
    )
    scores = info["test_metrics"]
    columns = st.columns(3)
    columns[0].metric("Test MAE", f"{scores['mae']:.4f} °C")
    columns[1].metric("Test RMSE", f"{scores['rmse']:.4f} °C")
    columns[2].metric("Test R²", f"{scores['r2']:.5f}")
    st.markdown("#### Validation comparison")
    comparison = pd.DataFrame(metrics["comparison"]["validation_results"])
    st.dataframe(comparison, use_container_width=True)

    importances = pd.DataFrame(info["feature_importance"])
    st.markdown("#### Permutation feature importance")
    st.bar_chart(importances.set_index("feature")["importance_mean"])

    sample = pd.DataFrame(metrics["evaluation_samples"])
    first, second = st.columns(2)
    first.markdown("#### Actual vs predicted")
    first.scatter_chart(sample, x="actual_temperature_c", y="predicted_temperature_c")
    second.markdown("#### Prediction error distribution")
    figure, axis = plt.subplots(figsize=(7, 3))
    axis.hist(sample["residual_c"], bins=30, color="#278678")
    axis.set_xlabel("Actual − predicted (°C)")
    axis.set_ylabel("Test observations")
    figure.tight_layout()
    second.pyplot(figure)
    plt.close(figure)
    st.caption("Evaluation charts use held-out test observations; residual = actual − predicted.")


def show_ai_assistant() -> None:
    st.title("AI Assistant")
    if os.getenv("GROQ_API_KEY"):
        st.success("Groq API is configured.")
    else:
        st.warning("Groq API is not configured. Add GROQ_API_KEY to the project .env file.")
    with st.form("ai_assistant_form"):
        question = st.text_input("Ask the AI assistant about cold-chain operations...")
        submitted = st.form_submit_button("AI Analysis")
    if submitted:
        if not question.strip():
            st.error("Enter a question for the AI assistant.")
            return
        try:
            st.write(generate_ai_explanation(question=question))
        except (GroqConfigurationError, GroqRequestError) as error:
            st.error(f"AI assistant request failed: {error}")


def main() -> None:
    st.set_page_config(page_title="Cold Chain Temperature Forecasting", layout="wide")
    data = get_dataset()
    service = get_prediction_service()
    metrics = dashboard_metrics()
    page = st.sidebar.radio(
        "Navigate",
        ["Home", "Predict", "Dataset", "Model Performance", "AI Assistant"],
    )
    if page == "Home":
        show_home(data, metrics["model_info"])
    elif page == "Predict":
        show_prediction(data, service)
    elif page == "Dataset":
        show_dataset(data)
    elif page == "Model Performance":
        show_model_performance(metrics)
    else:
        show_ai_assistant()


if __name__ == "__main__":
    main()
