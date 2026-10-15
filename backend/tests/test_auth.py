"""Authentication, CSRF, roles and tenant isolation."""

from __future__ import annotations

import uuid

import pytest

from tests.conftest import PASSWORD, ApiClient

pytestmark = pytest.mark.asyncio


async def test_status_is_public(anon: ApiClient) -> None:
    response = await anon.get("/api/auth/status", csrf=False)
    assert response.status_code == 200
    body = response.json()
    assert "registration_open" in body or "setup_required" in body or "authenticated" in body


async def test_register_login_logout_cycle(anon: ApiClient) -> None:
    data = await anon.register()
    assert data["user"]["email"].endswith("@example.com")
    assert data["csrf_token"]
    assert data["organization"]["id"]
    assert data["role"] in ("owner", "admin")

    me = await anon.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["id"] == data["user"]["id"]

    assert (await anon.post("/api/auth/logout")).status_code in (200, 204)
    assert (await anon.get("/api/auth/me")).status_code == 401

    bad = await anon.login(data["user"]["email"], "wrong-password-123")
    assert bad.status_code == 401
    good = await anon.login(data["user"]["email"], PASSWORD)
    assert good.status_code == 200
    assert (await anon.get("/api/auth/me")).status_code == 200


async def test_weak_password_rejected(anon: ApiClient) -> None:
    response = await anon.post("/api/auth/register", json={"email": f"{uuid.uuid4().hex[:8]}@example.com", "password": "short", "full_name": "x"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] in ("validation_error", "validation_failed")


async def test_csrf_required_for_cookie_auth(client: ApiClient) -> None:
    # Mutating request without the CSRF header must be rejected...
    response = await client.post("/api/projects", json={"name": "No CSRF"}, csrf=False)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"
    # ...and also with a bogus token.
    response = await client.post("/api/projects", json={"name": "Bad CSRF"}, csrf=False, headers={"X-CSRF-Token": "nope"})
    assert response.status_code == 403
    # Reads are fine without it.
    assert (await client.get("/api/projects", csrf=False)).status_code == 200
    # Cross-site origin is rejected even with a valid token.
    response = await client.post("/api/projects", json={"name": "Evil"}, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


async def test_error_envelope_and_request_id(client: ApiClient) -> None:
    response = await client.get(f"/api/projects/{uuid.uuid4()}")
    assert response.status_code == 404
    body = response.json()
    assert set(body["error"]) >= {"code", "message"}
    assert response.headers.get("X-Request-ID")


async def test_tenant_isolation(client: ApiClient, other_client: ApiClient) -> None:
    project = await client.create_project(analyze=False)
    session = await client.create_session(open=False)
    # The other organisation cannot see or touch these resources.
    assert (await other_client.get(f"/api/projects/{project['id']}")).status_code == 404
    assert (await other_client.patch(f"/api/projects/{project['id']}", json={"name": "hijack"})).status_code == 404
    assert (await other_client.get(f"/api/sessions/{session['id']}")).status_code == 404
    assert (await other_client.delete(f"/api/projects/{project['id']}")).status_code == 404
    listing = await other_client.get("/api/projects")
    assert all(p["id"] != project["id"] for p in listing.json())


async def test_member_roles(client: ApiClient, anon: ApiClient) -> None:
    # Members must have an account first; the owner then adds them with a role.
    viewer = await anon.register(full_name="Viewer")
    viewer_email = viewer["user"]["email"]
    assert (await client.post("/api/settings/members", json={"email": viewer_email, "role": "owner"})).status_code == 422
    created = await client.post("/api/settings/members", json={"email": viewer_email, "role": "viewer"})
    assert created.status_code == 201, created.text
    members = (await client.get("/api/settings/members")).json()
    assert any(m["email"] == viewer_email and m["role"] == "viewer" for m in members)

    # The viewer switches into the shared organisation.
    switched = await anon.post(f"/api/auth/switch-organization/{client.organization['id']}")
    assert switched.status_code == 200, switched.text
    assert switched.json()["role"] == "viewer"
    anon.csrf = switched.json()["csrf_token"]
    # Viewers can read but not mutate.
    assert (await anon.get("/api/projects")).status_code == 200
    denied = await anon.post("/api/projects", json={"name": "viewer project"})
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"
    assert (await anon.post("/api/settings/members", json={"email": "x@example.com", "role": "viewer"})).status_code == 403
    # Promote to member → can create projects; the owner's role is immutable.
    member_id = next(m["id"] for m in members if m["email"] == viewer_email)
    assert (await client.patch(f"/api/settings/members/{member_id}", json={"role": "member"})).status_code == 200
    assert (await anon.post("/api/projects", json={"name": "member project"})).status_code == 201
    owner_id = next(m["id"] for m in members if m["role"] == "owner")
    assert (await client.patch(f"/api/settings/members/{owner_id}", json={"role": "viewer"})).status_code == 409
    # Removal revokes access: the user falls back to their own organisation.
    assert (await client.delete(f"/api/settings/members/{member_id}")).status_code == 204
    me = await anon.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["organization"]["id"] != client.organization["id"]
    assert (await anon.post(f"/api/auth/switch-organization/{client.organization['id']}")).status_code in (403, 404)


async def test_auth_sessions_listing_and_revoke(client: ApiClient) -> None:
    sessions = await client.get("/api/auth/sessions")
    assert sessions.status_code == 200
    items = sessions.json()
    assert len(items) >= 1 and any(s.get("current") for s in items)
    current = next(s for s in items if s.get("current"))
    # Revoking the current session logs us out.
    assert (await client.delete(f"/api/auth/sessions/{current['id']}")).status_code in (200, 204)
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_change_password(client: ApiClient, anon: ApiClient) -> None:
    email = client.user["email"]
    response = await client.post("/api/auth/change-password", json={"current_password": PASSWORD, "new_password": PASSWORD + "-rotated"})
    assert response.status_code in (200, 204), response.text
    assert (await anon.login(email, PASSWORD)).status_code == 401
    assert (await anon.login(email, PASSWORD + "-rotated")).status_code == 200


async def test_audit_log_records_actions(client: ApiClient) -> None:
    await client.create_project(analyze=False)
    audit = await client.get("/api/audit")
    assert audit.status_code == 200
    actions = {entry["action"] for entry in audit.json()}
    assert "project.created" in actions
    assert "auth.register" in actions
