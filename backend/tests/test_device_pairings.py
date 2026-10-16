"""QR phone pairing: short-lived codes, explicit approval, and revocable app sessions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.database import get_session_factory
from app.core.security import hash_token
from app.models import DevicePairing
from tests.conftest import ApiClient

pytestmark = pytest.mark.asyncio


async def _create(client: ApiClient) -> dict:
    response = await client.post("/api/auth/device-pairings")
    assert response.status_code == 201, response.text
    return response.json()


async def test_phone_pairing_requires_desktop_approval_and_issues_http_only_session(
    client: ApiClient, anon: ApiClient
) -> None:
    pair = await _create(client)
    assert len(pair["code"]) >= 40

    async with get_session_factory()() as db:
        row = await db.get(DevicePairing, uuid.UUID(pair["id"]))
        assert row is not None
        assert row.code_hash == hash_token(pair["code"])
        assert pair["code"] not in row.code_hash

    claim = await anon.post(
        "/api/auth/device-pairings/claim",
        csrf=False,
        headers={"User-Agent": "PhoneBrowser/1.0"},
        json={"pairing_id": pair["id"], "code": pair["code"], "device_name": "My phone"},
    )
    assert claim.status_code == 200, claim.text
    assert claim.json()["status"] == "claimed"

    state = await client.get(f"/api/auth/device-pairings/{pair['id']}")
    assert state.status_code == 200
    assert state.json()["status"] == "claimed"
    assert state.json()["device_name"] == "My phone"
    assert "code" not in state.json()

    # A scan alone does not log the phone in.
    waiting = await anon.post(
        "/api/auth/device-pairings/poll",
        csrf=False,
        json={"pairing_id": pair["id"], "code": pair["code"]},
    )
    assert waiting.status_code == 200
    assert waiting.json() == {"status": "claimed", "authenticated": False}
    assert "set-cookie" not in waiting.headers
    assert (await anon.get("/api/auth/me")).status_code == 401

    approved = await client.post(f"/api/auth/device-pairings/{pair['id']}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"

    exchange = await anon.post(
        "/api/auth/device-pairings/poll",
        csrf=False,
        headers={"User-Agent": "PhoneBrowser/1.0"},
        json={"pairing_id": pair["id"], "code": pair["code"]},
    )
    assert exchange.status_code == 200, exchange.text
    assert exchange.json()["status"] == "completed"
    assert exchange.json()["authenticated"] is True
    assert exchange.json()["csrf_token"]
    assert "httponly" in exchange.headers["set-cookie"].lower()
    anon.csrf = exchange.json()["csrf_token"]
    assert (await anon.get("/api/auth/me")).status_code == 200

    # The same QR code cannot mint another session after its one successful exchange.
    replay = await anon.post(
        "/api/auth/device-pairings/poll",
        csrf=False,
        json={"pairing_id": pair["id"], "code": pair["code"]},
    )
    assert replay.status_code == 200
    assert replay.json() == {"status": "completed", "authenticated": False}
    assert "set-cookie" not in replay.headers


async def test_pairing_rejects_wrong_codes_and_is_private_to_the_requesting_user(
    client: ApiClient, anon: ApiClient, other_client: ApiClient
) -> None:
    pair = await _create(client)
    wrong = await anon.post(
        "/api/auth/device-pairings/claim",
        csrf=False,
        json={"pairing_id": pair["id"], "code": "x" * len(pair["code"])},
    )
    assert wrong.status_code == 401

    hidden = await other_client.get(f"/api/auth/device-pairings/{pair['id']}")
    assert hidden.status_code == 404
    denied = await other_client.post(f"/api/auth/device-pairings/{pair['id']}/approve")
    assert denied.status_code == 404

    claim = await anon.post(
        "/api/auth/device-pairings/claim",
        csrf=False,
        json={"pairing_id": pair["id"], "code": pair["code"]},
    )
    assert claim.status_code == 200
    assert claim.json()["status"] == "claimed"
    rejected = await client.post(f"/api/auth/device-pairings/{pair['id']}/reject")
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"

    poll = await anon.post(
        "/api/auth/device-pairings/poll",
        csrf=False,
        json={"pairing_id": pair["id"], "code": pair["code"]},
    )
    assert poll.status_code == 200
    assert poll.json() == {"status": "rejected", "authenticated": False}


async def test_pairing_expiry_and_csrf_protection(client: ApiClient, anon: ApiClient) -> None:
    pair = await _create(client)
    csrf_missing = await client.post(f"/api/auth/device-pairings/{pair['id']}/approve", csrf=False)
    assert csrf_missing.status_code == 403

    async with get_session_factory()() as db:
        row = await db.get(DevicePairing, uuid.UUID(pair["id"]))
        assert row is not None
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()

    claim = await anon.post(
        "/api/auth/device-pairings/claim",
        csrf=False,
        json={"pairing_id": pair["id"], "code": pair["code"]},
    )
    assert claim.status_code == 200
    assert claim.json()["status"] == "expired"
    state = await client.get(f"/api/auth/device-pairings/{pair['id']}")
    assert state.json()["status"] == "expired"
    approve = await client.post(f"/api/auth/device-pairings/{pair['id']}/approve")
    assert approve.status_code == 409


async def test_pairing_code_cannot_be_approved_before_a_phone_claims_it(client: ApiClient) -> None:
    pair = await _create(client)
    response = await client.post(f"/api/auth/device-pairings/{pair['id']}/approve")
    assert response.status_code == 409
    assert (await client.get(f"/api/auth/device-pairings/{pair['id']}")).json()["status"] == "pending"
