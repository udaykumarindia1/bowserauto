"""Provider catalogue and defaults shown by the organisation AI settings UI.

The presets are conveniences only: OpenAI-compatible endpoints use the same adapter, so a
self-hosted vLLM, LM Studio, LocalAI, or a newer hosted provider can be configured without adding
an SDK or changing the worker.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

ProviderKind = Literal["none", "openai_compatible", "gemini", "ollama", "anthropic"]


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    id: str
    label: str
    kind: ProviderKind
    description: str
    default_model: str | None = None
    default_base_url: str | None = None
    requires_api_key: bool = False
    model_hint: str = "Enter a model ID supported by this provider"
    base_url_hint: str = ""


PROVIDER_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        id="none",
        label="Heuristic agent",
        kind="none",
        description="Run the deterministic browser planner without sending page content to an AI provider.",
    ),
    ProviderSpec(
        id="openai",
        label="OpenAI",
        kind="openai_compatible",
        description="OpenAI Chat Completions API. Choose any model available to your API account.",
        default_model="gpt-4.1-mini",
        default_base_url="https://api.openai.com/v1",
        requires_api_key=True,
        model_hint="For example: gpt-4.1-mini",
        base_url_hint="https://api.openai.com/v1",
    ),
    ProviderSpec(
        id="openai_compatible",
        label="OpenAI-compatible API",
        kind="openai_compatible",
        description="Works with vLLM, LM Studio, LocalAI, and hosted APIs exposing standard OpenAI chat completions.",
        requires_api_key=False,
        model_hint="Use the model/deployment ID exposed by your endpoint",
        base_url_hint="For example: http://localhost:1234/v1 or https://api.example.com/v1",
    ),
    ProviderSpec(
        id="openrouter",
        label="OpenRouter",
        kind="openai_compatible",
        description="Route to many hosted models through one OpenAI-compatible endpoint.",
        default_model="openai/gpt-4.1-mini",
        default_base_url="https://openrouter.ai/api/v1",
        requires_api_key=True,
        model_hint="Use a model ID from the OpenRouter model catalogue",
        base_url_hint="https://openrouter.ai/api/v1",
    ),
    ProviderSpec(
        id="groq",
        label="Groq",
        kind="openai_compatible",
        description="Low-latency hosted inference through Groq's OpenAI-compatible API.",
        default_model="llama-3.3-70b-versatile",
        default_base_url="https://api.groq.com/openai/v1",
        requires_api_key=True,
        model_hint="Use a model ID enabled for your Groq account",
        base_url_hint="https://api.groq.com/openai/v1",
    ),
    ProviderSpec(
        id="deepseek",
        label="DeepSeek",
        kind="openai_compatible",
        description="DeepSeek chat and reasoning models through the compatible API.",
        default_model="deepseek-chat",
        default_base_url="https://api.deepseek.com",
        requires_api_key=True,
        model_hint="For example: deepseek-chat or deepseek-reasoner",
        base_url_hint="https://api.deepseek.com",
    ),
    ProviderSpec(
        id="together",
        label="Together AI",
        kind="openai_compatible",
        description="Hosted open-weight models through Together's compatible API.",
        default_model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        default_base_url="https://api.together.xyz/v1",
        requires_api_key=True,
        model_hint="Use a model ID from the Together AI catalogue",
        base_url_hint="https://api.together.xyz/v1",
    ),
    ProviderSpec(
        id="fireworks",
        label="Fireworks AI",
        kind="openai_compatible",
        description="Fireworks hosted inference through the compatible API.",
        default_model="accounts/fireworks/models/llama-v3p3-70b-instruct",
        default_base_url="https://api.fireworks.ai/inference/v1",
        requires_api_key=True,
        model_hint="Use a model ID from your Fireworks account",
        base_url_hint="https://api.fireworks.ai/inference/v1",
    ),
    ProviderSpec(
        id="mistral",
        label="Mistral AI",
        kind="openai_compatible",
        description="Mistral hosted models through the compatible API.",
        default_model="mistral-small-latest",
        default_base_url="https://api.mistral.ai/v1",
        requires_api_key=True,
        model_hint="For example: mistral-small-latest",
        base_url_hint="https://api.mistral.ai/v1",
    ),
    ProviderSpec(
        id="xai",
        label="xAI",
        kind="openai_compatible",
        description="Grok models through xAI's OpenAI-compatible API.",
        default_model="grok-3-mini",
        default_base_url="https://api.x.ai/v1",
        requires_api_key=True,
        model_hint="Use a model ID currently available to your xAI account",
        base_url_hint="https://api.x.ai/v1",
    ),
    ProviderSpec(
        id="gemini",
        label="Google Gemini",
        kind="gemini",
        description="Google Gemini generateContent API, including structured JSON and vision input.",
        default_model="gemini-2.5-flash",
        default_base_url="https://generativelanguage.googleapis.com/v1beta",
        requires_api_key=True,
        model_hint="For example: gemini-2.5-flash",
        base_url_hint="https://generativelanguage.googleapis.com/v1beta",
    ),
    ProviderSpec(
        id="ollama",
        label="Ollama (local or cloud)",
        kind="ollama",
        description="Native Ollama chat API. Use a model already pulled locally, or a supported cloud endpoint.",
        default_model="qwen3:latest",
        default_base_url="http://localhost:11434",
        requires_api_key=False,
        model_hint="Use an installed model tag, e.g. qwen3:latest",
        base_url_hint="http://localhost:11434 (the backend/worker must be able to reach it)",
    ),
    ProviderSpec(
        id="anthropic",
        label="Anthropic Claude",
        kind="anthropic",
        description="Anthropic Messages API with text and image input.",
        default_model="claude-sonnet-4-5",
        default_base_url="https://api.anthropic.com",
        requires_api_key=True,
        model_hint="Use a model ID currently available to your Anthropic account",
        base_url_hint="https://api.anthropic.com",
    ),
)

PROVIDER_IDS = tuple(spec.id for spec in PROVIDER_SPECS)
_PROVIDER_BY_ID = {spec.id: spec for spec in PROVIDER_SPECS}


def provider_spec(provider: str) -> ProviderSpec | None:
    return _PROVIDER_BY_ID.get(provider.strip().lower())


def provider_catalog() -> list[dict[str, object]]:
    """Return a JSON-safe provider catalogue; never includes credentials."""
    return [asdict(spec) for spec in PROVIDER_SPECS]
