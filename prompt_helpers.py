"""Shared provider helpers for the prompt-engineering notebooks."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(dotenv_path=Path(".env"), override=False)


@dataclass(frozen=True)
class ProviderConfig:
    """Connection details for one OpenAI-compatible chat provider."""

    name: str
    base_url: str
    api_key_env: str
    model_env: str
    default_model: str


PROVIDERS = {
    "groq": ProviderConfig(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        api_key_env="GROQ_API_KEY",
        model_env="GROQ_MODEL",
        default_model="openai/gpt-oss-20b",
    ),
    "mistral": ProviderConfig(
        name="mistral",
        base_url="https://api.mistral.ai/v1",
        api_key_env="MISTRAL_API_KEY",
        model_env="MISTRAL_MODEL",
        default_model="mistral-small-latest",
    ),
}


def selected_provider() -> ProviderConfig:
    """Read the provider switch once, and fail clearly for unsupported values."""

    provider_name = os.getenv("LLM_PROVIDER", "groq").strip().lower()
    if provider_name not in PROVIDERS:
        supported = ", ".join(PROVIDERS)
        raise ValueError(f"LLM_PROVIDER must be one of: {supported}")
    return PROVIDERS[provider_name]


def client_and_model() -> tuple[OpenAI | None, str, ProviderConfig, str | None]:
    """Return a configured client, or a reason why dry-run mode is active."""

    provider = selected_provider()
    api_key = os.getenv(provider.api_key_env)
    model = os.getenv(provider.model_env, provider.default_model)
    if not api_key:
        reason = (
            f"Dry run: set {provider.api_key_env} in .env to call {provider.name}. "
            "The surrounding code still runs so you can inspect the workflow."
        )
        return None, model, provider, reason
    return OpenAI(api_key=api_key, base_url=provider.base_url), model, provider, None


def dry_target_text(prompt: str) -> str:
    """Extract the final user example from a rendered classification prompt."""

    matches = re.findall(
        r'(?:Customer message|Message):\s*"?(.+?)(?:"?\s*(?:Category|Label):|\Z)',
        prompt,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return matches[-1].strip() if matches else prompt


def dry_category_for_prompt(prompt: str) -> str:
    """Return a deterministic support category for dry-run classification demos."""

    lowered = dry_target_text(prompt).lower()
    if any(word in lowered for word in ["invoice", "charged", "payment", "refund"]):
        return "BILLING"
    if any(
        word in lowered
        for word in ["login", "password", "mfa", "authenticator", "locked account"]
    ):
        return "ACCOUNT_ACCESS"
    if any(
        word in lowered
        for word in ["error", "bug", "500", "broken", "export", "timeout", "times out"]
    ):
        return "BUG_REPORT"
    if any(word in lowered for word in ["dark mode", "feature", "add "]):
        return "FEATURE_REQUEST"
    return "OTHER"


def dry_structured_payload(prompt: str) -> dict[str, object]:
    """Build deterministic JSON for structured-output examples."""

    label = dry_category_for_prompt(prompt)
    if label == "BUG_REPORT" or (
        label == "BILLING"
        and any(word in prompt.lower() for word in ["failure", "dispute", "charged"])
    ):
        priority = "high"
    else:
        priority = "medium"
    return {
        "label": label,
        "priority": priority,
        "summary": "Dry-run structured response for a support request.",
        "confidence": 0.72,
    }


def dry_chat(
    messages: list[dict[str, str]], response_format: dict[str, Any] | None = None
) -> str:
    """Small deterministic stand-in used when no API key is configured."""

    latest_user = next(
        (
            message["content"]
            for message in reversed(messages)
            if message["role"] == "user"
        ),
        "",
    )
    if response_format and response_format.get("type") == "json_object":
        return json.dumps(dry_structured_payload(latest_user), indent=2)
    lowered_user = latest_user.lower()
    if (
        "category:" in lowered_user
        or "label:" in lowered_user
        or "classify" in lowered_user
    ):
        return dry_category_for_prompt(latest_user)
    if "return json" in lowered_user:
        # Without JSON mode, live models usually wrap JSON in a markdown code fence.
        payload = json.dumps(dry_structured_payload(latest_user), indent=2)
        return f"```json\n{payload}\n```"
    return (
        "DRY RUN RESPONSE\n"
        "- Configure .env with a provider key to replace this with a live model response.\n"
        f"- Last user message preview: {latest_user[:160]}"
    )


def chat(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.2,
    response_format: dict[str, Any] | None = None,
) -> str:
    """Call the selected provider, falling back to dry-run text when no key exists."""

    client, model, _, dry_reason = client_and_model()
    if client is None:
        print(dry_reason)
        return dry_chat(messages, response_format=response_format)

    request: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if response_format is not None:
        request["response_format"] = response_format

    response = client.chat.completions.create(**request)
    return response.choices[0].message.content or ""
