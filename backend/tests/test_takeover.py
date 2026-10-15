"""Human takeover, live viewer WebSocket and browser session lifecycle."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from httpx_ws import WebSocketDisconnect, WebSocketUpgradeError, aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport

from app.workers.main import Worker
from tests.conftest import BASE_URL, ApiClient

pytestmark = pytest.mark.asyncio


class LiveViewer:
    """Small helper around httpx-ws speaking the /api/ws protocol."""

    def __init__(self, app: Any, cookies: httpx.Cookies) -> None:
        self.app = app
        self.cookies = cookies
        self.messages: list[dict[str, Any]] = []

    async def __aenter__(self) -> LiveViewer:
        # The transport must be entered as a context manager (it owns the task group that
        # runs the ASGI app); AsyncClient.__aenter__ does that for us.
        self._client = httpx.AsyncClient(
            transport=ASGIWebSocketTransport(app=self.app),
            base_url=BASE_URL,
            cookies=self.cookies,
            headers={"Origin": BASE_URL},
        )
        await self._client.__aenter__()
        self._ctx = aconnect_ws(f"{BASE_URL}/api/ws", self._client)
        try:
            self.ws = await self._ctx.__aenter__()
        except BaseException:
            await self._client.__aexit__(None, None, None)
            raise
        return self

    async def __aexit__(self, *exc: Any) -> None:
        try:
            await self._ctx.__aexit__(*exc)
        finally:
            await self._client.__aexit__(*exc)

    async def send(self, message: dict[str, Any]) -> None:
        await self.ws.send_text(json.dumps(message))

    async def expect(self, predicate, timeout: float = 10) -> dict[str, Any]:  # noqa: ANN001
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            raw = await asyncio.wait_for(
                self.ws.receive_text(), timeout=max(0.1, deadline - asyncio.get_running_loop().time())
            )
            message = json.loads(raw)
            self.messages.append(message)
            if predicate(message):
                return message
        raise AssertionError(f"no matching websocket message; got {[m.get('type') for m in self.messages]}")


async def test_login_wall_takeover_and_release(app, client: ApiClient, fake_sites, site_lookup) -> None:
    fake_sites(generation_seconds=0.1, require_login=True)
    session = await client.create_session(name="Needs login")
    project = await client.create_project(script="Scene 1: A kite in the wind.")
    task = await client.create_task(project["id"], session_id=session["id"], config={"max_retries": 0})

    waiting = await client.wait_task(task["id"], {"waiting_for_human", "failed", "completed"}, timeout=30)
    assert waiting["status"] == "waiting_for_human", waiting
    assert waiting["attention"]["kind"] == "login"
    assert "takeover" in waiting["attention"]["options"]
    assert waiting["human_control"] is False

    async with LiveViewer(app, client.http.cookies) as viewer:
        hello = await viewer.expect(lambda m: m["type"] == "hello")
        assert hello["organization_id"] == client.organization["id"]
        await viewer.send({"type": "subscribe_session", "session_id": session["id"]})
        await viewer.expect(lambda m: m["type"] == "subscribed")
        frame = await viewer.expect(lambda m: m["type"] == "frame")
        assert frame["data"] and frame["session_id"] == session["id"]
        status = await viewer.expect(lambda m: m["type"] == "status")
        assert status["interactive"] is False

        # Input is ignored while the agent owns the browser.
        await viewer.send(
            {"type": "input", "session_id": session["id"], "event": {"type": "click", "x": 10, "y": 10}}
        )
        await asyncio.sleep(0.2)
        assert site_lookup(session["id"]).logged_in is False

        # TAKE OVER BROWSER -> the human gets control, the session becomes interactive.
        assert (await client.post(f"/api/tasks/{task['id']}/takeover")).status_code == 200
        taken = await client.wait_until(task["id"], lambda t: t["human_control"] is True, timeout=10)
        assert taken["status"] == "waiting_for_human"
        await viewer.expect(lambda m: m["type"] == "status" and m["interactive"] is True)
        live = await client.wait_session(session["id"], {"in_use", "ready"})
        assert live["interactive"] is True
        await viewer.expect(lambda m: m["type"] == "human.takeover", timeout=5)

        # The human "signs in" through the live viewer (raw input is forwarded to the browser).
        await viewer.send(
            {"type": "input", "session_id": session["id"], "event": {"type": "click", "x": 200, "y": 300}}
        )
        await asyncio.sleep(0.3)
        assert site_lookup(session["id"]).logged_in is True
        assert (
            await client.post(f"/api/sessions/{session['id']}/mark-login", json={"logged_in": True})
        ).status_code == 200

        # RETURN CONTROL TO AI -> the agent re-inspects the page and finishes the scene.
        assert (await client.post(f"/api/tasks/{task['id']}/release")).status_code == 200
        await viewer.expect(lambda m: m["type"] == "status" and m["interactive"] is False)
        done = await client.wait_task(task["id"], {"completed", "failed", "needs_attention"}, timeout=40)
        assert done["status"] == "completed", done
        assert done["human_control"] is False
        await viewer.expect(lambda m: m["type"] == "task.completed", timeout=10)

    activity = (await client.get(f"/api/tasks/{task['id']}/activity")).json()
    types = [a["event_type"] for a in activity]
    assert "human.required" in types and "human.takeover" in types and "human.released" in types
    shots = (await client.get(f"/api/tasks/{task['id']}/screenshots")).json()
    assert any(s["stage"] == "after_takeover" for s in shots)
    # The saved session remembers the login (encrypted storage state, never exposed).
    final = (await client.get(f"/api/sessions/{session['id']}")).json()
    assert final["login_status"] in ("logged_in", "unknown")
    assert "storage_state_encrypted" not in final and "cookies" not in final


async def test_verification_challenge_is_never_bypassed(
    client: ApiClient, fake_sites, site_lookup, worker
) -> None:
    fake_sites(generation_seconds=0.1, human_check_on_attempt=1)
    session = await client.create_session(name="Challenge")
    project = await client.create_project(script="Scene 1: A quiet lake at dawn.")
    task = await client.create_task(project["id"], session_id=session["id"], config={"max_retries": 2})
    waiting = await client.wait_task(task["id"], {"waiting_for_human", "failed", "completed"}, timeout=30)
    assert waiting["status"] == "waiting_for_human", waiting
    assert waiting["attention"]["kind"] == "verification"
    site = site_lookup(session["id"])
    assert site.human_check_pending is True  # the agent did not try to tick the box itself
    # Session-level takeover (without a task) is also possible; here we use the task flow.
    assert (await client.post(f"/api/tasks/{task['id']}/takeover")).status_code == 200
    await client.wait_until(task["id"], lambda t: t["human_control"] is True, timeout=10)
    host = worker.hosts[__import__("uuid").UUID(session["id"])]
    await host.input.handle({"type": "click", "x": 50, "y": 50})  # human completes the challenge
    assert site.human_check_pending is False
    assert (await client.post(f"/api/tasks/{task['id']}/release")).status_code == 200
    done = await client.wait_task(task["id"], {"completed", "failed", "needs_attention"}, timeout=40)
    assert done["status"] == "completed", done


async def test_session_lifecycle_and_limits(client: ApiClient, fake_sites) -> None:
    session = await client.create_session(name="Lifecycle", open=False)
    assert session["status"] == "closed"
    opened = await client.post(
        f"/api/sessions/{session['id']}/open", json={"url": "http://mock.test/mock-platform/?speed=fast"}
    )
    assert opened.status_code == 200
    ready = await client.wait_session(session["id"], {"ready"})
    assert ready["current_url"].startswith("http://mock.test/mock-platform/")
    # Navigate through the API (allowed when no task owns the browser).
    assert (
        await client.post(
            f"/api/sessions/{session['id']}/navigate", json={"url": "http://mock.test/mock-platform/?login=1"}
        )
    ).status_code == 200
    await asyncio.sleep(0.3)
    assert (await client.get(f"/api/sessions/{session['id']}")).json()["current_url"].endswith("login=1")
    # Session level takeover / release without a task.
    assert (await client.post(f"/api/sessions/{session['id']}/takeover")).status_code == 200
    await asyncio.sleep(0.2)
    assert (await client.get(f"/api/sessions/{session['id']}")).json()["interactive"] is True
    assert (await client.post(f"/api/sessions/{session['id']}/release")).status_code == 200
    await asyncio.sleep(0.2)
    assert (await client.get(f"/api/sessions/{session['id']}")).json()["interactive"] is False
    # Rename + close + saved state flag.
    assert (await client.patch(f"/api/sessions/{session['id']}", json={"name": "Renamed"})).status_code == 200
    assert (await client.post(f"/api/sessions/{session['id']}/close")).status_code == 200
    closed = await client.wait_session(session["id"], {"closed"})
    assert closed["name"] == "Renamed"
    assert closed["worker_id"] is None
    # Deleting removes it from the list.
    assert (await client.delete(f"/api/sessions/{session['id']}")).status_code == 204
    assert all(s["id"] != session["id"] for s in (await client.get("/api/sessions")).json())
    # Per-organisation session limit is enforced.
    limit = (await client.get("/api/settings")).json()["effective_limits"]["max_browser_sessions"]
    assert (
        await client.patch("/api/settings", json={"limits": {"max_browser_sessions": 1}})
    ).status_code == 200
    first = await client.create_session(name="only one", open=False)
    denied = await client.post("/api/sessions", json={"name": "too many", "platform_key": "mock"})
    assert denied.status_code == 429, denied.text
    assert (await client.delete(f"/api/sessions/{first['id']}")).status_code == 204
    assert (
        await client.patch("/api/settings", json={"limits": {"max_browser_sessions": limit}})
    ).status_code == 200


async def test_viewer_role_cannot_control_browser(
    app, client: ApiClient, anon: ApiClient, fake_sites
) -> None:
    session = await client.create_session(name="Viewer test")
    viewer_account = await anon.register(full_name="Viewer")
    assert (
        await client.post(
            "/api/settings/members", json={"email": viewer_account["user"]["email"], "role": "viewer"}
        )
    ).status_code == 201
    switched = await anon.post(f"/api/auth/switch-organization/{client.organization['id']}")
    assert switched.status_code == 200
    async with LiveViewer(app, anon.http.cookies) as viewer:
        hello = await viewer.expect(lambda m: m["type"] == "hello")
        assert hello["role"] == "viewer"
        await viewer.send({"type": "subscribe_session", "session_id": session["id"]})
        await viewer.expect(lambda m: m["type"] == "subscribed")
        await viewer.send(
            {"type": "input", "session_id": session["id"], "event": {"type": "click", "x": 1, "y": 1}}
        )
        error = await viewer.expect(lambda m: m["type"] == "error")
        assert "viewers" in error["message"]
    # And the REST controls are forbidden too.
    anon.csrf = switched.json()["csrf_token"]
    assert (await anon.post(f"/api/sessions/{session['id']}/takeover")).status_code == 403


async def test_websocket_requires_authentication(app) -> None:
    # No session cookie: the server closes with 4401 before accepting (a real server turns
    # that into a rejected upgrade / HTTP 403; the in-process transport reports the close code).
    async with httpx.AsyncClient(
        transport=ASGIWebSocketTransport(app=app), base_url=BASE_URL, headers={"Origin": BASE_URL}
    ) as client:
        with pytest.raises((WebSocketUpgradeError, WebSocketDisconnect)) as info:
            async with aconnect_ws(f"{BASE_URL}/api/ws", client) as ws:
                await ws.receive_text()
    if isinstance(info.value, WebSocketDisconnect):
        assert info.value.code == 4401


async def test_worker_evicts_idle_browser_when_slots_are_full(client: ApiClient, worker: Worker) -> None:
    """A worker at its browser capacity frees the longest-idle browser nobody is using instead of leaving
    the new session silently closed; browsers a human is actively using are never taken."""
    capacity = worker.host_capacity()
    for leftover in list(worker.hosts):  # browsers left open by earlier tests would skew the count
        await worker.close_host(leftover, reason="test reset")
    sessions = [await client.create_session(name=f"Slot {i}") for i in range(capacity)]
    full = await client.create_session(name="One too many", open=False)
    assert (await client.post(f"/api/sessions/{full['id']}/open")).status_code == 200
    # Every open browser is under (fresh) human control, so the new one has to wait - visibly.
    for _ in range(100):
        state = (await client.get(f"/api/sessions/{full['id']}")).json()
        if state["error"]:
            break
        await asyncio.sleep(0.05)
    assert state["status"] == "closed" and "free browser slot" in state["error"], state
    assert len(worker.hosts) == capacity
    # Hand the first browser back (no human, no task): it becomes the eviction candidate.
    assert (await client.post(f"/api/sessions/{sessions[0]['id']}/release")).status_code == 200
    opened = await client.wait_session(full["id"], {"ready", "in_use"})
    assert opened["error"] == "" and opened["worker_id"] == worker.id
    assert len(worker.hosts) <= capacity
    evicted = (await client.get(f"/api/sessions/{sessions[0]['id']}")).json()
    assert (
        evicted["status"] == "closed"
        and evicted["desired_state"] == "closed"
        and evicted["worker_id"] is None
    )
    # An evicted session reopens on demand like any other closed session (after a slot is released).
    assert (await client.post(f"/api/sessions/{full['id']}/release")).status_code == 200
    assert (await client.post(f"/api/sessions/{sessions[0]['id']}/open")).status_code == 200
    reopened = await client.wait_session(sessions[0]["id"], {"ready", "in_use"})
    assert reopened["worker_id"] == worker.id
