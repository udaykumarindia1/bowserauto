"""Tenant storage limits are enforced before staged uploads enter project storage."""

from __future__ import annotations

import struct
import zlib

import pytest

from app.core.config import get_settings
from tests.conftest import ApiClient

pytestmark = pytest.mark.asyncio


def _png_bytes(size: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    header = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    idat = chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
    iend = chunk(b"IEND", b"")
    base = header + ihdr + idat + iend
    return base + b"\x00" * max(0, size - len(base))


async def test_asset_upload_cannot_exceed_org_storage_limit(
    client: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = await client.create_project(script="Scene 1: Storage test.")
    settings = get_settings()
    monkeypatch.setattr(settings, "max_storage_per_org_mb", 1)
    headers = {"X-CSRF-Token": client.csrf, "Origin": "http://testserver"}
    within_quota = await client.http.post(
        f"/api/projects/{project['id']}/assets",
        files={"file": ("within-quota.png", _png_bytes(900 * 1024), "image/png")},
        data={"kind": "reference"},
        headers=headers,
    )
    assert within_quota.status_code == 201, within_quota.text
    notifications = (await client.get("/api/notifications")).json()["items"]
    assert any(item["type"] == "storage_quota_warning" for item in notifications)

    response = await client.http.post(
        f"/api/projects/{project['id']}/assets",
        files={"file": ("over-quota.png", _png_bytes(200 * 1024), "image/png")},
        data={"kind": "reference"},
        headers=headers,
    )
    assert response.status_code == 429, response.text
    assert response.json()["error"]["code"] == "limit_exceeded"
    assets = await client.get(f"/api/projects/{project['id']}/assets")
    assert [asset["filename"] for asset in assets.json()] == ["within-quota.png"]


async def test_organization_storage_cap_is_enforced_below_server_default(client: ApiClient) -> None:
    project = await client.create_project(script="Scene 1: Organization quota test.")
    updated = await client.patch("/api/settings", json={"limits": {"max_storage_mb": 1}})
    assert updated.status_code == 200, updated.text
    assert updated.json()["effective_limits"]["max_storage_mb"] == 1

    response = await client.http.post(
        f"/api/projects/{project['id']}/assets",
        files={"file": ("over-org-quota.png", _png_bytes(2 * 1024 * 1024), "image/png")},
        data={"kind": "reference"},
        headers={"X-CSRF-Token": client.csrf, "Origin": "http://testserver"},
    )
    assert response.status_code == 429, response.text
    assert response.json()["error"]["code"] == "limit_exceeded"
    assert (await client.get(f"/api/projects/{project['id']}/assets")).json() == []
