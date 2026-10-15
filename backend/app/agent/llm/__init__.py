"""Provider-neutral LLM interface used by BowserAuto's browser and scene agents."""

from app.agent.llm.clients import AnthropicClient, GeminiClient, OllamaClient, OpenAICompatibleClient
from app.agent.llm.factory import build_llm_client, build_test_client, llm_status, validate_llm_base_url
from app.agent.llm.providers import PROVIDER_IDS, PROVIDER_SPECS, provider_catalog, provider_spec
from app.agent.llm.types import LLMClient, LLMMessage, LLMResponse, LLMUnavailable, extract_json

__all__ = [
    "AnthropicClient",
    "GeminiClient",
    "LLMClient",
    "LLMMessage",
    "LLMResponse",
    "LLMUnavailable",
    "OllamaClient",
    "OpenAICompatibleClient",
    "PROVIDER_IDS",
    "PROVIDER_SPECS",
    "build_llm_client",
    "build_test_client",
    "extract_json",
    "llm_status",
    "provider_catalog",
    "provider_spec",
    "validate_llm_base_url",
]
