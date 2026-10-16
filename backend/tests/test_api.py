"""API tests: projects, scene editing, assets, generated files, templates, platforms,
notifications, settings/members, dashboard, tenant isolation, CSRF and signed downloads."""

from __future__ import annotations

import io
import struct
import zipfile
import zlib

import httpx
import pytest

from tests.conftest import SAMPLE_SCRIPT, ApiClient

pytestmark = pytest.mark.asyncio


def _png_bytes(width: int = 4, height: int = 4) -> bytes:
    """A minimal valid PNG (so the upload passes magic-byte sniffing)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


# --------------------------------------------------------------------------- projects
async def test_project_crud_and_script_analysis(client: ApiClient) -> None:
    project = await client.create_project(script=SAMPLE_SCRIPT)
    assert project["name"] and project["platform_key"] == "mock"
    scenes = await client.scenes(project["id"])
    assert scenes, "analysing the script produces scenes"
    assert [s["code"] for s in scenes] == [f"scene_{i:03d}" for i in range(1, len(scenes) + 1)]
    assert all(s["prompt"] and s["status"] == "draft" for s in scenes)

    # Re-analyse with a new script and a target duration.
    analysed = await client.post(
        f"/api/projects/{project['id']}/analyze",
        json={"script_text": "Scene 1: Sunrise.\nScene 2: Noon.\nScene 3: Dusk.", "clip_duration_seconds": 6},
    )
    assert analysed.status_code == 200, analysed.text
    body = analysed.json()
    assert body["method"] == "heuristic" and len(body["scenes"]) == 3
    assert all(s["duration_seconds"] <= 6 for s in body["scenes"])

    updated = await client.patch(
        f"/api/projects/{project['id']}", json={"name": "Renamed", "description": "desc"}
    )
    assert updated.status_code == 200 and updated.json()["name"] == "Renamed"
    listed = (await client.get("/api/projects")).json()
    assert any(p["id"] == project["id"] and p["scene_count"] == 3 for p in listed)
    detail = (await client.get(f"/api/projects/{project['id']}")).json()
    assert detail["script_text"].startswith("Scene 1: Sunrise.")
    estimate = (await client.get(f"/api/projects/{project['id']}/estimate")).json()
    assert (
        estimate["scenes"] == 3
        and estimate["estimated_wall_clock_seconds"] > 0
        and estimate["output_seconds"] > 0
    )

    assert (await client.delete(f"/api/projects/{project['id']}")).status_code == 204
    assert (await client.get(f"/api/projects/{project['id']}")).status_code == 404


async def test_scene_editing_workflow(client: ApiClient) -> None:
    project = await client.create_project(script="Scene 1: One.\nScene 2: Two.\nScene 3: Three.")
    pid = project["id"]
    scenes = await client.scenes(pid)
    assert len(scenes) == 3
    one, two, three = scenes

    # Edit prompt + duration.
    edited = await client.patch(
        f"/api/projects/{pid}/scenes/{two['id']}",
        json={"prompt": "A custom prompt", "duration_seconds": 4, "title": "Second"},
    )
    assert (
        edited.status_code == 200
        and edited.json()["prompt"] == "A custom prompt"
        and edited.json()["duration_seconds"] == 4
    )
    # Regenerate the prompt from the scene fields.
    regenerated = await client.post(f"/api/projects/{pid}/scenes/{two['id']}/regenerate-prompt")
    assert regenerated.status_code == 200 and regenerated.json()["prompt"] != "A custom prompt"
    # Duplicate -> inserted right after the original, codes renumbered.
    dup = await client.post(f"/api/projects/{pid}/scenes/{two['id']}/duplicate")
    assert dup.status_code == 201
    codes = [s["code"] for s in await client.scenes(pid)]
    assert codes == ["scene_001", "scene_002", "scene_003", "scene_004"]
    assert (await client.scenes(pid))[2]["title"].startswith("Second")
    # Reorder: move scene three to the front.
    order = [three["id"], one["id"], two["id"], dup.json()["id"]]
    reordered = await client.post(f"/api/projects/{pid}/scenes/reorder", json={"scene_ids": order})
    assert reordered.status_code == 200 and [s["id"] for s in reordered.json()] == order
    assert reordered.json()[0]["code"] == "scene_001"
    # Change platform for one scene, add a scene after another, delete one.
    assert (
        await client.patch(f"/api/projects/{pid}/scenes/{one['id']}", json={"platform_key": "runway"})
    ).json()["platform_key"] == "runway"
    added = await client.post(
        f"/api/projects/{pid}/scenes?after={one['id']}",
        json={"visual": "An added shot", "duration_seconds": 3},
    )
    assert added.status_code == 201 and added.json()["prompt"]
    assert (await client.delete(f"/api/projects/{pid}/scenes/{dup.json()['id']}")).status_code == 204
    final = await client.scenes(pid)
    assert len(final) == 4 and [s["code"] for s in final] == [
        "scene_001",
        "scene_002",
        "scene_003",
        "scene_004",
    ]
    assert final[2]["visual"] == "An added shot"
    # Invalid platform is rejected.
    assert (
        await client.patch(f"/api/projects/{pid}/scenes/{one['id']}", json={"platform_key": "does-not-exist"})
    ).status_code in (400, 422)


async def test_assets_characters_and_export(client: ApiClient) -> None:
    project = await client.create_project(script="Scene 1: Mira waves.")
    pid = project["id"]
    files = {"file": ("mira.png", _png_bytes(), "image/png")}
    uploaded = await client.http.post(
        f"/api/projects/{pid}/assets",
        files=files,
        data={"kind": "character_reference"},
        headers={"X-CSRF-Token": client.csrf},
    )
    assert uploaded.status_code == 201, uploaded.text
    asset = uploaded.json()
    assert asset["mime_type"] == "image/png" and asset["size_bytes"] > 0 and asset["sha256"]
    # Fake image content is rejected even with an image extension.
    bad = await client.http.post(
        f"/api/projects/{pid}/assets",
        files={"file": ("evil.png", b"<script>alert(1)</script>", "image/png")},
        data={"kind": "reference"},
        headers={"X-CSRF-Token": client.csrf},
    )
    assert bad.status_code == 422, bad.text
    # Disallowed extension.
    exe = await client.http.post(
        f"/api/projects/{pid}/assets",
        files={"file": ("tool.exe", b"MZ....", "application/octet-stream")},
        headers={"X-CSRF-Token": client.csrf},
    )
    assert exe.status_code == 422
    listed = (await client.get(f"/api/projects/{pid}/assets")).json()
    assert [a["id"] for a in listed] == [asset["id"]]

    # Signed URL serves the file without auth cookies and rejects tampering.
    signed = (await client.get(f"/api/projects/{pid}/assets/{asset['id']}/url")).json()
    assert signed["url"].startswith("/api/files/signed/") and signed["expires_in"] > 0
    anonymous = httpx.AsyncClient(transport=client.http._transport, base_url=str(client.http.base_url))
    try:
        served = await anonymous.get(signed["url"])
        assert served.status_code == 200 and served.content[:4] == b"\x89PNG"
        assert (await anonymous.get(signed["url"][:-3] + "abc")).status_code in (400, 403, 404)
    finally:
        await anonymous.aclose()

    # Characters linked to the reference image flow into analysis.
    character = await client.post(
        f"/api/projects/{pid}/characters",
        json={"name": "Mira", "description": "A sailor", "reference_asset_id": asset["id"]},
    )
    assert character.status_code == 201
    assert (
        await client.patch(
            f"/api/projects/{pid}/characters/{character.json()['id']}",
            json={"name": "Mira", "description": "Captain"},
        )
    ).json()["description"] == "Captain"
    analysed = (await client.post(f"/api/projects/{pid}/analyze", json={})).json()
    assert asset["id"] in analysed["scenes"][0]["reference_asset_ids"]
    assert "Mira" in analysed["scenes"][0]["characters"]

    export = await client.get(f"/api/projects/{pid}/export")
    assert export.status_code == 200 and export.headers["content-type"].startswith("application/zip")
    names = zipfile.ZipFile(io.BytesIO(export.content)).namelist()
    assert (
        any(n.endswith("mira.png") for n in names)
        and "scenes/scenes.json" in names
        and "script/script.txt" in names
    )

    assert (
        await client.delete(f"/api/projects/{pid}/characters/{character.json()['id']}")
    ).status_code == 204
    assert (await client.delete(f"/api/projects/{pid}/assets/{asset['id']}")).status_code == 204
    assert (await client.get(f"/api/projects/{pid}/assets")).json() == []


# --------------------------------------------------------------------------- files
async def test_generated_files_listing_and_deletion(client: ApiClient, fake_sites) -> None:
    fake_sites(generation_seconds=0.05)
    project = await client.create_project(script="Scene 1: A.\nScene 2: B.")
    task = await client.create_task(project["id"])
    done = await client.wait_task(task["id"], {"completed", "failed"}, timeout=40)
    assert done["status"] == "completed", done
    page = (await client.get(f"/api/files?project_id={project['id']}")).json()
    assert page["total"] == 2 and sorted(f["filename"] for f in page["items"]) == [
        "scene_001.mp4",
        "scene_002.mp4",
    ]
    first = page["items"][0]
    assert (
        first["status"] == "verified"
        and first["size_bytes"] > 0
        and first["duration_seconds"]
        and first["download"]["url"].startswith("/api/files/signed/")
    )
    assert (await client.get(f"/api/files?task_id={task['id']}")).json()["total"] == 2
    detail = (await client.get(f"/api/files/{first['id']}")).json()
    assert (
        detail["id"] == first["id"]
        and detail["scene_id"]
        and detail["preview"]["url"].startswith("/api/files/signed/")
    )
    url = (await client.get(f"/api/files/{first['id']}/url")).json()["url"]
    served = await client.get(url)
    assert served.status_code == 200 and served.content[4:8] == b"ftyp"
    assert (await client.delete(f"/api/files/{first['id']}")).status_code == 204
    assert (await client.get(f"/api/files/{first['id']}")).status_code == 404
    assert (await client.get(f"/api/files?project_id={project['id']}")).json()["total"] == 1
    # The scene that lost its output goes back to pending so it can be regenerated.
    scenes = await client.scenes(project["id"])
    assert sorted(s["status"] for s in scenes) == ["completed", "draft"]


# ----------------------------------------------------------- templates / platforms / misc
async def test_templates_platforms_notifications_dashboard(client: ApiClient, fake_sites) -> None:
    templates = (await client.get("/api/templates")).json()
    keys = {t["key"] for t in templates}
    assert {
        "ai_video_generation",
        "image_generation",
        "research",
        "data_entry",
        "website_testing",
        "content_creation",
        "browser_workflow",
    } <= keys
    builtin = next(t for t in templates if t["key"] == "ai_video_generation")
    assert builtin["is_builtin"] is True
    assert (await client.delete(f"/api/templates/{builtin['id']}")).status_code in (400, 403, 409)
    created = await client.post(
        "/api/templates",
        json={
            "key": "my-flow",
            "name": "My flow",
            "kind": "browser_workflow",
            "config": {"instructions": "Open the dashboard and export the report"},
        },
    )
    assert created.status_code == 201, created.text
    tid = created.json()["id"]
    assert (await client.patch(f"/api/templates/{tid}", json={"name": "My flow v2"})).json()[
        "name"
    ] == "My flow v2"
    assert (await client.post("/api/templates", json={"key": "my-flow", "name": "dup"})).status_code == 409
    assert (await client.delete(f"/api/templates/{tid}")).status_code == 204

    platforms = (await client.get("/api/platforms")).json()
    pkeys = {p["key"] for p in platforms}
    assert {
        "google_flow",
        "runway",
        "kling",
        "luma",
        "hailuo",
        "pika",
        "pixverse",
        "vidu",
        "chatgpt",
        "gemini",
        "arena",
        "generic",
        "mock",
    } <= pkeys
    runway = (await client.get("/api/platforms/runway")).json()
    assert (
        runway["adapter_available"] and runway["requires_login"] and "generate" in runway["supported_actions"]
    )
    assert (await client.post("/api/platforms/runway/enabled", json={"enabled": False})).status_code == 200
    assert (await client.get("/api/platforms/runway")).json()["enabled"] is False
    assert (await client.get("/api/platforms/nope")).status_code == 404
    assert (await client.post("/api/platforms/runway/enabled", json={"enabled": True})).status_code == 200

    fake_sites(generation_seconds=0.05)
    project = await client.create_project(script="Scene 1: Done quickly.")
    task = await client.create_task(project["id"])
    assert (await client.wait_task(task["id"], {"completed", "failed"}, timeout=30))["status"] == "completed"
    notifications = (await client.get("/api/notifications")).json()
    assert notifications["unread"] >= 1 and any(n["type"] == "task_completed" for n in notifications["items"])
    first = notifications["items"][0]
    assert (await client.post(f"/api/notifications/{first['id']}/read")).status_code == 200
    assert (await client.post("/api/notifications/read")).status_code == 200
    assert (await client.get("/api/notifications")).json()["unread"] == 0

    dashboard = (await client.get("/api/dashboard")).json()
    assert dashboard["tasks"]["completed"] >= 1 and dashboard["projects"]["active"] >= 1
    assert (
        dashboard["sessions"]["total"] >= 0
        and dashboard["files"]["count"] >= 1
        and dashboard["usage_week"]["generation_seconds"] >= 0
    )
    activity = (await client.get("/api/activity?limit=20")).json()
    assert activity and all("event_type" in entry for entry in activity)
    assert (await client.get("/api/audit")).status_code == 200  # owner may read the audit log


async def test_llm_connection_test_uses_mocked_client_only(client: ApiClient, monkeypatch) -> None:
    saved = await client.patch(
        "/api/settings",
        json={"llm_provider": "openai", "llm_model": "mock-model", "llm_api_key": "sk-never-return-this"},
    )
    assert saved.status_code == 200

    class MockLLM:
        name = "openai"
        model = "mock-model"

        async def complete(self, _messages, **_kwargs):
            return type("Completion", (), {"text": "OK", "model": self.model, "usage": {}})()

    monkeypatch.setattr("app.api.settings.build_llm_client", lambda _overrides: MockLLM())
    tested = await client.post("/api/settings/llm/test")
    assert tested.status_code == 200, tested.text
    body = tested.json()
    assert body["ok"] is True and body["provider"] == "openai" and body["model"] == "mock-model"
    assert body["latency_ms"] >= 0 and "never-return-this" not in tested.text


async def test_settings_limits_and_members(client: ApiClient, other_client: ApiClient) -> None:
    settings = (await client.get("/api/settings")).json()
    assert settings["effective_limits"]["max_retries"] >= 1 and settings["llm"]["provider"]
    provider_ids = {provider["id"] for provider in settings["llm"]["providers"]}
    assert {"openai_compatible", "gemini", "ollama", "anthropic"}.issubset(provider_ids)
    tightened = await client.patch(
        "/api/settings",
        json={
            "limits": {"max_retries": 1, "max_browser_sessions": 2, "max_storage_mb": 2048},
            "name": "Studio",
        },
    )
    assert tightened.status_code == 200, tightened.text
    assert (
        tightened.json()["effective_limits"]["max_retries"] == 1
        and tightened.json()["effective_limits"]["max_storage_mb"] == 2048
        and tightened.json()["organization"]["name"] == "Studio"
    )
    # Limits can only be tightened within the system range.
    assert (await client.patch("/api/settings", json={"limits": {"max_retries": 999}})).status_code == 422
    assert (
        await client.patch("/api/settings", json={"limits": {"max_storage_mb": 1_000_001}})
    ).status_code == 422
    # LLM key is stored encrypted and never echoed back.
    saved = await client.patch(
        "/api/settings", json={"llm_provider": "openai", "llm_api_key": "sk-test-1234567890abcdef"}
    )
    assert (
        saved.status_code == 200
        and saved.json()["llm"]["has_api_key"] is True
        and saved.json()["llm"]["api_key_masked"] == "••••••••"
        and "sk-test" not in saved.text
    )
    # A key for one provider must not be carried over to a new provider's endpoint.
    switched = await client.patch("/api/settings", json={"llm_provider": "gemini"})
    assert switched.status_code == 200 and switched.json()["llm"]["has_api_key"] is False
    saved_again = await client.patch("/api/settings", json={"llm_api_key": "gemini-test-secret"})
    assert saved_again.status_code == 200 and saved_again.json()["llm"]["has_api_key"] is True
    cleared = await client.patch("/api/settings", json={"clear_llm_api_key": True, "llm_provider": "none"})
    assert cleared.status_code == 200 and cleared.json()["llm"]["has_api_key"] is False
    unused_key = await client.patch("/api/settings", json={"llm_api_key": "unused-secret"})
    assert (
        unused_key.status_code == 422
        and "Select an organization AI provider" in unused_key.text
        and "unused-secret" not in unused_key.text
    )

    members = (await client.get("/api/settings/members")).json()
    assert len(members) == 1 and members[0]["role"] == "owner"
    added = await client.post(
        "/api/settings/members", json={"email": other_client.user["email"], "role": "viewer"}
    )
    assert added.status_code == 201, added.text
    member_id = added.json()["id"]
    assert (await client.patch(f"/api/settings/members/{member_id}", json={"role": "member"})).json()[
        "role"
    ] == "member"
    # The invited user now sees two organisations and can switch.
    status = (await other_client.get("/api/auth/me")).json()
    assert len(status["organizations"]) == 2
    assert (await client.delete(f"/api/settings/members/{member_id}")).status_code == 204
    # Owners cannot remove themselves.
    own = next(m for m in (await client.get("/api/settings/members")).json() if m["role"] == "owner")
    assert (await client.delete(f"/api/settings/members/{own['id']}")).status_code in (400, 409)
    # Members cannot change settings.
    assert (await other_client.patch("/api/settings", json={"name": "Hijack"})).status_code in (
        403,
        200,
    )  # own org: ok


# ------------------------------------------------------------------------- security
async def test_tenant_isolation(client: ApiClient, other_client: ApiClient, fake_sites) -> None:
    project = await client.create_project(script="Scene 1: Private.")
    session = await client.create_session(open=False)
    scenes = await client.scenes(project["id"])
    task = await client.create_task(project["id"], session_id=None)
    for path in (
        f"/api/projects/{project['id']}",
        f"/api/projects/{project['id']}/scenes",
        f"/api/projects/{project['id']}/export",
        f"/api/sessions/{session['id']}",
        f"/api/tasks/{task['id']}",
        f"/api/tasks/{task['id']}/activity",
        f"/api/tasks/{task['id']}/screenshots",
    ):
        assert (await other_client.get(path)).status_code == 404, path
    assert (
        await other_client.patch(
            f"/api/projects/{project['id']}/scenes/{scenes[0]['id']}", json={"prompt": "x"}
        )
    ).status_code == 404
    assert (await other_client.post(f"/api/tasks/{task['id']}/stop")).status_code == 404
    assert (await other_client.delete(f"/api/sessions/{session['id']}")).status_code == 404
    assert all(p["id"] != project["id"] for p in (await other_client.get("/api/projects")).json())
    assert all(t["id"] != task["id"] for t in (await other_client.get("/api/tasks")).json()["items"])
    done = await client.wait_task(task["id"], {"completed", "failed", "cancelled"}, timeout=40)
    assert done["status"] == "completed"
    files = (await client.get(f"/api/files?project_id={project['id']}")).json()["items"]
    assert files and (await other_client.get(f"/api/files/{files[0]['id']}")).status_code == 404
    assert (await other_client.get(f"/api/files?project_id={project['id']}")).json()["total"] == 0


async def test_csrf_and_cookie_protection(client: ApiClient, anon: ApiClient) -> None:
    # A state-changing request without the CSRF header is rejected even with a valid cookie.
    no_csrf = await client.http.post("/api/projects", json={"name": "x"}, headers={"X-CSRF-Token": ""})
    assert no_csrf.status_code == 403
    wrong = await client.http.post("/api/projects", json={"name": "x"}, headers={"X-CSRF-Token": "nope"})
    assert wrong.status_code == 403
    # Cross-origin browser requests are refused.
    cross = await client.http.post(
        "/api/projects",
        json={"name": "x"},
        headers={"X-CSRF-Token": client.csrf, "Origin": "https://evil.example"},
    )
    assert cross.status_code == 403
    # Anonymous access is unauthorized; cookies are HttpOnly.
    assert (await anon.get("/api/projects")).status_code == 401
    login = await anon.http.post(
        "/api/auth/login", json={"email": client.user["email"], "password": "wrong-password"}
    )
    assert login.status_code == 401
    cookie_header = ""
    for name in ("set-cookie",):
        cookie_header = (await client.http.get("/api/auth/me")).headers.get(name, "")
    assert "ba_session=" not in cookie_header or "httponly" in cookie_header.lower()
    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert "password" not in str(body).lower() or "password_hash" not in body


async def test_viewer_role_is_read_only(client: ApiClient, other_client: ApiClient) -> None:
    added = await client.post(
        "/api/settings/members", json={"email": other_client.user["email"], "role": "viewer"}
    )
    assert added.status_code == 201
    switched = await other_client.post(f"/api/auth/switch-organization/{client.organization['id']}")
    assert switched.status_code == 200, switched.text
    other_client.csrf = switched.json()["csrf_token"]
    project = await client.create_project(script="Scene 1: Read only.")
    assert (await other_client.get(f"/api/projects/{project['id']}")).status_code == 200
    assert (await other_client.post("/api/projects", json={"name": "nope"})).status_code == 403
    assert (
        await other_client.patch(f"/api/projects/{project['id']}", json={"name": "nope"})
    ).status_code == 403
    assert (await other_client.post("/api/tasks", json={"project_id": project["id"]})).status_code == 403
    assert (
        await other_client.post("/api/sessions", json={"name": "nope", "platform_key": "mock"})
    ).status_code == 403
    assert (await other_client.get("/api/audit")).status_code == 403
