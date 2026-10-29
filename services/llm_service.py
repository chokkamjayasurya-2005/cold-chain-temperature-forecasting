from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import APIError, OpenAI


load_dotenv(Path(__file__).resolve().parents[1] / ".env")

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "openai/gpt-oss-20b"
SYSTEM_PROMPT = (
    "You are an assistant for a cold-chain temperature forecasting demo. "
    "Use only the provided question and prediction context; do not infer missing "
    "sensor readings, feature contributions, causes, or numerical values. When "
    "relevant, format follow-up answers with Markdown headings for 'Current "
    "Prediction Summary', 'Why Is the Risk High or Low?', 'Recommended Actions', "
    "and 'Key Takeaways'. Put predicted temperature, set point, deviation, risk "
    "index, and risk level in a compact Markdown table. Explain risk using only "
    "the supplied risk index, observed inputs, and top_factors. top_factors are "
    "one-feature-at-a-time comparisons to a representative training baseline, "
    "not causal effects or additive contributions; explain that limitation. "
    "Separate model estimates, observed inputs, and recommendations. Keep answers "
    "concise, avoid repeating earlier content, and say when evidence is insufficient. "
    "Do not claim a validated spoilage probability, regulatory compliance, or "
    "certified operational advice."
)


class GroqConfigurationError(RuntimeError):
    pass


class GroqRequestError(RuntimeError):
    pass


def generate_ai_explanation(
    question: str = "Explain the latest cold-chain temperature prediction.",
    context: dict[str, Any] | None = None,
) -> str:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise GroqConfigurationError(
            "GROQ_API_KEY is not set. Add it to the project .env file before using the AI assistant."
        )
    if not question.strip():
        raise ValueError("The AI question cannot be empty.")

    user_input = question
    if context is not None:
        user_input += "\n\nPrediction context:\n" + json.dumps(context, ensure_ascii=False)
    prompt = f"{SYSTEM_PROMPT}\n\nUser question and context:\n{user_input}"

    client = OpenAI(
        api_key=api_key,
        base_url=GROQ_BASE_URL,
        timeout=45,
    )
    try:
        response = client.responses.create(
            input=prompt,
            model=os.getenv("GROQ_MODEL", DEFAULT_MODEL),
        )
    except APIError as error:
        raise GroqRequestError(f"Groq API request failed: {error}") from error

    answer = response.output_text
    if not isinstance(answer, str) or not answer.strip():
        raise GroqRequestError("Groq API returned an empty response.")
    return answer.strip()
