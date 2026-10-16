"""URL privacy and browser egress policy helpers.

Browser workers use this module at request time as a defence-in-depth SSRF guard. Operators
must still enforce an outbound firewall: DNS pinning cannot eliminate DNS rebinding or
platform-specific browser networking behavior.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass, field
from urllib.parse import SplitResult, urlsplit, urlunsplit

from app.core.config import Settings, get_settings

_METADATA_HOSTS = {
    "metadata",
    "metadata.google.internal",
    "instance-data.ec2.internal",
}
_METADATA_IPS = {
    "100.100.100.200",
    "168.63.129.16",
    "169.254.169.254",
    "169.254.170.2",
    "fd00:ec2::254",
}
_URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)


class UnsafeDestination(ValueError):
    """A browser navigation points at a disallowed or unsafe destination."""


def _safe_path(path: str) -> str:
    """Remove path-based signed tokens while retaining useful non-secret URL context."""
    parts = path.split("/")
    for index, part in enumerate(parts[:-1]):
        if part.lower() in {"signed", "token", "download-token"} and parts[index + 1]:
            parts[index + 1] = "[redacted]"
    return "/".join(parts)


def sanitize_url(value: str) -> str:
    """Strip URL credentials, query, fragment and common path-token forms for display/logging."""
    raw = (value or "").strip()
    try:
        parsed = urlsplit(raw)
        if not parsed.scheme or not parsed.netloc:
            return raw[:2048]
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        try:
            port = parsed.port
        except ValueError:
            port = None
        netloc = host + (f":{port}" if port is not None else "")
        return urlunsplit((parsed.scheme, netloc, _safe_path(parsed.path), "", ""))[:2048]
    except (ValueError, UnicodeError):
        return raw[:2048]


def sanitize_text(value: str) -> str:
    """Sanitize embedded HTTP(S) URLs in arbitrary text before persistence or model prompts."""

    def replace(match: re.Match[str]) -> str:
        raw = match.group(0)
        suffix = ""
        while raw and raw[-1] in ".,;!?)]}":
            suffix = raw[-1] + suffix
            raw = raw[:-1]
        return sanitize_url(raw) + suffix

    value = _URL_RE.sub(replace, value)
    value = re.sub(r"(?i)(/signed/)[A-Za-z0-9._~-]+", r"\1[redacted]", value)
    return value[:20_000]


def _ip_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        try:
            # Reject legacy/non-canonical numeric IPv4 spellings instead of letting Chromium
            # reinterpret them after the policy check.
            socket.inet_aton(value)
        except OSError:
            return None
        return None


def _is_metadata(host: str, addresses: set[str]) -> bool:
    if host.lower().rstrip(".") in _METADATA_HOSTS:
        return True
    for raw in addresses:
        try:
            address = ipaddress.ip_address(raw)
        except ValueError:
            continue
        mapped = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else None
        if str(address) in _METADATA_IPS or (mapped and str(mapped) in _METADATA_IPS):
            return True
    return False


def _resolve(host: str, port: int) -> set[str]:
    records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses: set[str] = set()
    for record in records:
        raw = record[4][0].split("%", 1)[0]
        try:
            addresses.add(str(ipaddress.ip_address(raw)))
        except ValueError:
            continue
    return addresses


@dataclass
class BrowserURLPolicy:
    """Short-lived per-host DNS cache for browser network request interception."""

    settings: Settings = field(default_factory=get_settings)
    cache_seconds: float = 5.0
    _cache: dict[tuple[str, int], tuple[float, set[str]]] = field(default_factory=dict)
    _locks: dict[tuple[str, int], asyncio.Lock] = field(default_factory=dict)
    allowed_domains: set[str] = field(default_factory=set)

    def set_allowed_domains(self, values: list[str] | set[str]) -> None:
        self.allowed_domains = {
            str(value).strip().lower().rstrip(".") for value in values if str(value).strip()
        }

    async def check(self, value: str) -> None:
        """Raise ``UnsafeDestination`` if URL is not safe for this browser worker."""
        parsed = urlsplit(value)
        if parsed.scheme in {"about", "blob", "data", "chrome-extension"}:
            return
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise UnsafeDestination("Only HTTP(S) browser destinations are allowed")
        if parsed.username is not None or parsed.password is not None:
            raise UnsafeDestination("URLs containing credentials are not allowed")
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError as exc:
            raise UnsafeDestination("URL contains an invalid port") from exc

        host = parsed.hostname.lower().rstrip(".")
        allowed = {
            item.strip().lower().rstrip(".") for item in self.settings.browser_allowed_hosts if item.strip()
        }
        explicitly_allowed = host in allowed
        literal = _ip_address(host)
        addresses = {str(literal)} if literal else set()
        if not literal and (self.settings.is_production or explicitly_allowed):
            key = (host, port)
            lock = self._locks.setdefault(key, asyncio.Lock())
            async with lock:
                cached = self._cache.get(key)
                loop_time = asyncio.get_running_loop().time()
                if cached and cached[0] > loop_time:
                    addresses = cached[1]
                else:
                    try:
                        addresses = await asyncio.to_thread(_resolve, host, port)
                    except (OSError, TimeoutError) as exc:
                        if not explicitly_allowed:
                            raise UnsafeDestination("Destination host could not be safely resolved") from exc
                        addresses = set()
                    self._cache[key] = (loop_time + self.cache_seconds, addresses)

        if _is_metadata(host, addresses):
            raise UnsafeDestination("Cloud metadata destinations are blocked")
        if self.allowed_domains and not any(
            host == domain or host.endswith("." + domain) for domain in self.allowed_domains
        ):
            raise UnsafeDestination("Destination is outside this task's allowed-domain policy")
        if any(ipaddress.ip_address(address).is_link_local for address in addresses):
            raise UnsafeDestination("Link-local browser destinations are blocked")
        if not self.settings.is_production:
            return
        if explicitly_allowed:
            return
        if parsed.scheme != "https":
            raise UnsafeDestination("Production browser navigation requires HTTPS")
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise UnsafeDestination("Private or unresolved browser destinations must be allowlisted")


def require_safe_http_url(value: str, *, settings: Settings | None = None) -> SplitResult:
    """Syntactic URL validation for API-stored or queued destinations.

    The worker's browser request policy performs the authoritative DNS-aware check at use time.
    """
    parsed = urlsplit((value or "").strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise UnsafeDestination("URL must be HTTP(S), include a hostname, and contain no credentials")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise UnsafeDestination("URL contains an invalid port") from exc
    settings = settings or get_settings()
    host = parsed.hostname.lower().rstrip(".")
    if _is_metadata(host, {host} if _ip_address(host) else set()):
        raise UnsafeDestination("Cloud metadata destinations are blocked")
    if settings.is_production and parsed.scheme != "https":
        allowed = {
            item.strip().lower().rstrip(".") for item in settings.browser_allowed_hosts if item.strip()
        }
        if host not in allowed:
            raise UnsafeDestination("Production browser navigation requires HTTPS")
    return parsed
