"""Provider configuration resolution and secure client construction."""

from __future__ import annotations

import ipaddress
import logging
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.agent.llm.clients import AnthropicClient, GeminiClient, OllamaClient, OpenAICompatibleClient
from app.agent.llm.providers import ProviderSpec, provider_spec
from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)
_DNS_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="llm-dns")
_DNS_CACHE: dict[tuple[str, int], tuple[float, set[str]]] = {}
_DNS_LOCK = threading.Lock()
_DNS_TTL_SECONDS = 10.0
_DNS_TIMEOUT_SECONDS = 2.0
_METADATA_HOSTS = {"metadata", "metadata.google.internal", "instance-data.ec2.internal"}
_METADATA_IPS = {
    "100.100.100.200",
    "168.63.129.16",
    "169.254.169.254",
    "169.254.170.2",
    "fd00:ec2::254",
}


def _resolved_addresses(hostname: str, port: int) -> set[str]:
    key = (hostname, port)
    now = time.monotonic()
    with _DNS_LOCK:
        cached = _DNS_CACHE.get(key)
        if cached and cached[0] > now:
            return cached[1]
    future = _DNS_POOL.submit(socket.getaddrinfo, hostname, port, 0, socket.SOCK_STREAM)
    try:
        rows = future.result(timeout=_DNS_TIMEOUT_SECONDS)
    except FutureTimeout as exc:
        future.cancel()
        raise ValueError("LLM provider host could not be resolved within the safety timeout") from exc
    except OSError as exc:
        raise ValueError("LLM provider host could not be safely resolved") from exc
    addresses: set[str] = set()
    for row in rows:
        try:
            addresses.add(str(ipaddress.ip_address(row[4][0].split("%", 1)[0])))
        except ValueError:
            continue
    if not addresses:
        raise ValueError("LLM provider host did not resolve to an IP address")
    with _DNS_LOCK:
        _DNS_CACHE[key] = (now + _DNS_TTL_SECONDS, addresses)
    return addresses


def _is_metadata_destination(hostname: str, addresses: set[str]) -> bool:
    if hostname in _METADATA_HOSTS:
        return True
    for raw in addresses:
        address = ipaddress.ip_address(raw)
        mapped = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else None
        if str(address) in _METADATA_IPS or (mapped and str(mapped) in _METADATA_IPS):
            return True
    return False


@dataclass(frozen=True, slots=True)
class ResolvedLLMConfig:
    provider: str
    model: str | None
    base_url: str | None
    api_key: str | None
    source: str
    issue: str | None = None


def validate_llm_base_url(
    value: str,
    *,
    settings: Settings | None = None,
    provider: str | None = None,
) -> str:
    """Validate an endpoint and apply production private-network restrictions.

    Org admins can configure provider endpoints. In production, internal/private or plain-HTTP
    destinations must be explicitly allowlisted with ``LLM_ALLOWED_HOSTS``. Operators should
    additionally enforce outbound network policy at the container/host boundary.
    """
    settings = settings or get_settings()
    base = value.strip().rstrip("/")
    parsed = urlsplit(base)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("LLM base URL must be an http(s) URL without credentials, query, or fragment")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("LLM base URL contains an invalid port") from exc

    hostname = parsed.hostname.lower().rstrip(".")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        # inet_aton also recognizes legacy numeric IPv4 spellings that ipaddress rejects.
        try:
            address = ipaddress.ip_address(socket.inet_aton(hostname))
        except OSError:
            address = None
    if _is_metadata_destination(hostname, {str(address)} if address else set()):
        raise ValueError("Cloud metadata endpoints are not valid LLM provider URLs")
    is_local_name = hostname == "localhost" or bool(
        address is None
        and (
            "." not in hostname
            or hostname.endswith((".localhost", ".local", ".internal", ".intranet", ".home.arpa", ".lan"))
        )
    )
    is_private_address = bool(address and not address.is_global)
    allowed_hosts = {host.strip().lower().rstrip(".") for host in settings.llm_allowed_hosts if host.strip()}
    is_allowlisted = hostname in allowed_hosts

    if address and address.is_link_local:
        raise ValueError("Link-local LLM endpoints are not allowed")
    if settings.app_env == "production":
        if not is_allowlisted and (parsed.scheme == "http" or is_local_name or is_private_address):
            raise ValueError("Private or plain-HTTP LLM endpoints must be allowlisted in LLM_ALLOWED_HOSTS")
        if address:
            resolved = {str(address)}
        else:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            try:
                resolved = _resolved_addresses(hostname, port)
            except ValueError:
                if not is_allowlisted:
                    raise
                resolved = set()
        if _is_metadata_destination(hostname, resolved):
            raise ValueError("Cloud metadata endpoints are not valid LLM provider URLs")
        if any(ipaddress.ip_address(item).is_link_local for item in resolved):
            raise ValueError("Link-local LLM endpoints are not allowed")
        if not is_allowlisted and any(not ipaddress.ip_address(item).is_global for item in resolved):
            raise ValueError("Private or plain-HTTP LLM endpoints must be allowlisted in LLM_ALLOWED_HOSTS")
    return base


def _resolved_config(overrides: dict[str, Any] | None, settings: Settings) -> ResolvedLLMConfig:
    values = overrides or {}
    provider_override = values.get("provider")
    has_org_override = provider_override not in (None, "", "inherit")
    requested_provider = (
        str(provider_override if has_org_override else (settings.llm_provider or "none")).strip().lower()
    )
    if requested_provider == "google":
        requested_provider = "gemini"
    source = (
        "organization" if has_org_override else ("environment" if settings.llm_provider != "none" else "none")
    )

    # Never pass the server's environment key to an organization-selected provider or endpoint.
    # An org override uses only its own encrypted key, model, and endpoint; missing fields fall
    # back to that provider's catalogue defaults, not global settings for another provider.
    fallback = (
        {}
        if has_org_override
        else {
            "model": settings.llm_model,
            "api_key": settings.llm_api_key,
            "base_url": settings.llm_base_url,
        }
    )
    model = str(values.get("model") or fallback.get("model") or "").strip() or None
    api_key = str(values.get("api_key") or fallback.get("api_key") or "").strip() or None
    base_url = str(values.get("base_url") or fallback.get("base_url") or "").strip() or None
    return ResolvedLLMConfig(requested_provider, model, base_url, api_key, source)


def _configuration_issue(config: ResolvedLLMConfig, spec: ProviderSpec | None) -> str | None:
    if config.provider == "none":
        return "AI provider is disabled; the heuristic planner is active."
    if spec is None:
        return "The selected AI provider is not supported."
    if spec.kind == "none":
        return "AI provider is disabled; the heuristic planner is active."
    model = config.model or spec.default_model
    if not model:
        return "Enter a model ID for the selected provider."
    if spec.requires_api_key and not config.api_key:
        return "Add an API key for the selected provider."
    base_url = config.base_url or spec.default_base_url
    if not base_url:
        return "Enter the base URL for the OpenAI-compatible endpoint."
    return None


def _client_for(
    config: ResolvedLLMConfig,
    spec: ProviderSpec,
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> OpenAICompatibleClient | GeminiClient | OllamaClient | AnthropicClient | None:
    issue = _configuration_issue(config, spec)
    if issue:
        return None
    model = config.model or spec.default_model
    base_url = validate_llm_base_url(
        config.base_url or spec.default_base_url or "", settings=settings, provider=config.provider
    )
    if spec.kind == "openai_compatible":
        return OpenAICompatibleClient(
            config.api_key,
            model or "",
            base_url,
            name=config.provider,
            timeout=settings.llm_timeout_seconds,
            transport=transport,
        )
    if spec.kind == "gemini":
        return GeminiClient(
            config.api_key or "",
            model or "",
            base_url,
            timeout=settings.llm_timeout_seconds,
            transport=transport,
        )
    if spec.kind == "ollama":
        return OllamaClient(
            config.api_key,
            model or "",
            base_url,
            timeout=settings.llm_timeout_seconds,
            transport=transport,
        )
    if spec.kind == "anthropic":
        return AnthropicClient(
            config.api_key or "",
            model or "",
            base_url,
            timeout=settings.llm_timeout_seconds,
            transport=transport,
        )
    return None


def _build(
    overrides: dict[str, Any] | None,
    *,
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[
    ResolvedLLMConfig,
    OpenAICompatibleClient | GeminiClient | OllamaClient | AnthropicClient | None,
    str | None,
]:
    config = _resolved_config(overrides, settings)
    spec = provider_spec(config.provider)
    issue = _configuration_issue(config, spec)
    if issue:
        return config, None, issue
    try:
        client = _client_for(config, spec, settings, transport=transport) if spec else None
    except ValueError as exc:
        # Do not echo custom URLs into API responses or logs. They can contain internal hostnames.
        message = str(exc)
        issue = (
            message
            if "allowlisted" in message.lower() or "not allowed" in message.lower()
            else "The configured LLM base URL is invalid."
        )
        return config, None, issue
    return config, client, None if client else "The selected provider could not be initialized."


def build_llm_client(overrides: dict[str, Any] | None = None) -> Any:
    """Create a provider client, or return ``None`` when configuration is incomplete."""
    settings = get_settings()
    _config, client, issue = _build(overrides, settings=settings)
    if issue and _config.provider != "none":
        logger.info("LLM provider is not ready: provider=%s reason=%s", _config.provider, issue)
    return client


def llm_status(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = get_settings()
    config, client, issue = _build(overrides, settings=settings)
    return {
        "configured": client is not None,
        "provider": config.provider,
        "model": getattr(client, "model", None) or config.model,
        "source": config.source,
        "status": "ready" if client else ("disabled" if config.provider == "none" else "not_configured"),
        "message": "Provider is ready." if client else (issue or "Provider is not configured."),
    }


def build_test_client(
    overrides: dict[str, Any] | None,
    *,
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Test seam used by API tests without making an external provider request."""
    effective_settings = settings or get_settings()
    config, client, issue = _build(overrides, settings=effective_settings, transport=transport)
    return client, {
        "provider": config.provider,
        "model": getattr(client, "model", None) or config.model,
        "source": config.source,
        "configured": client is not None,
        "message": "Provider is ready." if client else (issue or "Provider is not configured."),
    }
