"""Host telemetry for the HUD "SYS MONITOR" panel.

Reads Linux ``/proc`` and ``/sys`` directly so it needs no extra dependency (psutil is not
guaranteed in every deployment). Every value degrades to ``None`` on platforms that do
not expose it; the UI renders missing readings as ``--``.

Sampling is rate-limited: callers may poll freely, the module re-reads the kernel
counters at most every ``MIN_INTERVAL`` seconds and otherwise returns the cached frame.
"""

from __future__ import annotations

import os
import platform
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MIN_INTERVAL = 1.0
_PROCESS_START = time.time()


@dataclass
class _CpuSample:
    busy: int
    total: int
    ts: float


@dataclass
class _NetSample:
    rx: int
    tx: int
    ts: float


_last_cpu: _CpuSample | None = None
_last_net: _NetSample | None = None
_cache: dict[str, Any] | None = None
_cache_ts = 0.0
_cpu_pct = 0.0
_net_rate = {"rx_bps": 0.0, "tx_bps": 0.0}


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _cpu_percent() -> float | None:
    """Overall CPU busy percentage since the previous sample (``/proc/stat``)."""
    global _last_cpu, _cpu_pct
    text = _read("/proc/stat")
    if not text:
        return None
    first = text.splitlines()[0].split()
    if first[0] != "cpu":
        return None
    values = [int(v) for v in first[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    total = sum(values)
    now = time.time()
    if _last_cpu is not None and total > _last_cpu.total:
        d_total = total - _last_cpu.total
        d_busy = (total - idle) - _last_cpu.busy
        _cpu_pct = max(0.0, min(100.0, 100.0 * d_busy / d_total))
    _last_cpu = _CpuSample(busy=total - idle, total=total, ts=now)
    return round(_cpu_pct, 1)


def _memory() -> dict[str, Any]:
    text = _read("/proc/meminfo")
    if not text:
        return {"total_bytes": None, "used_bytes": None, "percent": None}
    fields: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts:
            fields[key] = int(parts[0]) * 1024
    total = fields.get("MemTotal", 0)
    available = fields.get("MemAvailable", fields.get("MemFree", 0))
    used = max(0, total - available)
    return {"total_bytes": total, "used_bytes": used, "percent": round(100.0 * used / total, 1) if total else None}


def _network() -> dict[str, Any]:
    """Aggregate RX/TX throughput in bytes per second across non-loopback interfaces."""
    global _last_net, _net_rate
    text = _read("/proc/net/dev")
    if not text:
        return {"rx_bps": None, "tx_bps": None}
    rx = tx = 0
    for line in text.splitlines()[2:]:
        name, _, rest = line.partition(":")
        if name.strip() == "lo":
            continue
        cols = rest.split()
        if len(cols) >= 9:
            rx += int(cols[0])
            tx += int(cols[8])
    now = time.time()
    if _last_net is not None and now > _last_net.ts:
        dt = now - _last_net.ts
        _net_rate = {"rx_bps": max(0.0, (rx - _last_net.rx) / dt), "tx_bps": max(0.0, (tx - _last_net.tx) / dt)}
    _last_net = _NetSample(rx=rx, tx=tx, ts=now)
    return {k: round(v, 1) for k, v in _net_rate.items()}


def _temperature() -> float | None:
    base = Path("/sys/class/thermal")
    if not base.exists():
        return None
    readings: list[float] = []
    for zone in base.glob("thermal_zone*/temp"):
        raw = _read(str(zone))
        if raw and raw.strip().lstrip("-").isdigit():
            value = int(raw.strip())
            readings.append(value / 1000.0 if value > 1000 else float(value))
    return round(max(readings), 1) if readings else None


def _load() -> list[float] | None:
    try:
        return [round(v, 2) for v in os.getloadavg()]
    except (OSError, AttributeError):
        return None


def _uptime_seconds() -> float | None:
    text = _read("/proc/uptime")
    if not text:
        return None
    try:
        return float(text.split()[0])
    except (ValueError, IndexError):
        return None


def _process_count() -> int | None:
    try:
        return sum(1 for entry in os.listdir("/proc") if entry.isdigit())
    except OSError:
        return None


def _disk(path: Path) -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(path if path.exists() else Path.cwd())
    except OSError:
        return {"total_bytes": None, "used_bytes": None, "percent": None}
    return {"total_bytes": usage.total, "used_bytes": usage.used, "percent": round(100.0 * usage.used / usage.total, 1) if usage.total else None}


def system_metrics(storage_path: Path | None = None) -> dict[str, Any]:
    """Return a telemetry frame; cached for ``MIN_INTERVAL`` seconds between kernel reads."""
    global _cache, _cache_ts
    now = time.time()
    if _cache is not None and now - _cache_ts < MIN_INTERVAL:
        return _cache
    frame = {
        "cpu_percent": _cpu_percent(),
        "cpu_count": os.cpu_count(),
        "load": _load(),
        "memory": _memory(),
        "network": _network(),
        "disk": _disk(storage_path or Path.cwd()),
        "temperature_c": _temperature(),
        "gpu_percent": None,
        "uptime_seconds": _uptime_seconds(),
        "process_uptime_seconds": round(now - _PROCESS_START, 1),
        "process_count": _process_count(),
        "os": platform.system(),
        "python": platform.python_version(),
        "ts": now,
    }
    _cache, _cache_ts = frame, now
    return frame
