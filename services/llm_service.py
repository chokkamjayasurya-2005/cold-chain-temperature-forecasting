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
    "Use only the provided question and prediction context. Distinguish model "
    "estimates from observed measurements. Do not claim a validated spoilage "
    "probability, regulatory compliance, or certified operational advice. "
    "If context is missing, say so rather than inventing prediction details."
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
