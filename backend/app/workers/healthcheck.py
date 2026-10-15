"""Container health check for worker processes.

Exits 0 when the worker on this host touched its liveness file recently (3 × heartbeat
interval) and 1 otherwise. Usage: ``python -m app.workers.healthcheck``.
"""

from __future__ import annotations

import socket
import sys
import time
from datetime import UTC, datetime

from app.core.config import get_settings


def main() -> int:
    settings = get_settings()
    directory = settings.resolved_data_dir / "workers"
    prefix = f"{socket.gethostname()}-"
    if not directory.exists():
        print("no liveness directory yet")
        return 1
    newest: float | None = None
    for path in directory.glob(f"{prefix}*.alive"):
        newest = max(newest or 0.0, path.stat().st_mtime)
    if newest is None:
        print("no liveness file for this host")
        return 1
    age = time.time() - newest
    limit = max(15.0, settings.worker_heartbeat_seconds * 3)
    if age > limit:
        print(f"stale liveness file ({age:.0f}s old > {limit:.0f}s) at {datetime.now(UTC).isoformat()}")
        return 1
    print(f"ok ({age:.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
