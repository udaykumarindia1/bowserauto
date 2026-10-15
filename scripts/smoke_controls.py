#!/usr/bin/env python3
"""Smoke test for task controls, human takeover and the live browser WebSocket.

Runs against a live API using the mock platform (slow mode so there is time to interact).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid

import httpx
import websockets

SCRIPT = "Scene one: a fox runs through snow.\n\nScene two: the fox finds a frozen lake.\n\nScene three: the fox watches the northern lights."


def wait_status(client: httpx.Client, tid: str, wanted: set[str], timeout: float = 90) -> dict:
    deadline = time.time() + timeout
    task = {}
    while time.time() < deadline:
        task = client.get(f"/api/tasks/{tid}").json()
        if task["status"] in wanted:
            return task
        time.sleep(0.5)
    raise AssertionError(f"timeout waiting for {wanted}; last={task.get('status')} action={task.get('current_action')} attention={task.get('attention')}")


async def ws_check(base: str, cookies: dict, session_id: str, want_frames: int = 2, timeout: float = 30) -> dict:
    url = base.replace("http", "ws", 1) + "/api/ws"
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())
    frames = 0
    statuses = 0
    events = 0
    seen_types = set()
    async with websockets.connect(url, additional_headers={"Cookie": cookie_header, "Origin": "http://localhost:3000"}, max_size=8_000_000) as ws:
        hello = json.loads(await ws.recv())
        assert hello["type"] == "hello", hello
        await ws.send(json.dumps({"type": "subscribe_session", "session_id": session_id}))
        deadline = time.time() + timeout
        while time.time() < deadline and frames < want_frames:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
            except asyncio.TimeoutError:
                await ws.send(json.dumps({"type": "ping"}))
                continue
            msg = json.loads(raw)
            seen_types.add(msg["type"])
            if msg["type"] == "frame":
                frames += 1
                assert msg["data"] and msg["width"], msg.keys()
            elif msg["type"] in ("status", "action"):
                statuses += 1
            else:
                events += 1
        # send a harmless human input event (ignored unless interactive)
        await ws.send(json.dumps({"type": "input", "session_id": session_id, "event": {"type": "mousemove", "x": 10, "y": 10}}))
    return {"frames": frames, "statuses": statuses, "events": events, "types": sorted(seen_types)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    client = httpx.Client(base_url=args.base, timeout=60, headers={"Origin": "http://localhost:3000"})
    r = client.post("/api/auth/register", json={"email": f"ctl-{uuid.uuid4().hex[:8]}@example.com", "password": "SmokeTest-Password-123"})
    r.raise_for_status()
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]

    # --- a persistent browser session the user can open & take over ---------------------------
    r = client.post("/api/sessions", json={"name": "Mock session", "platform_key": "mock"})
    r.raise_for_status()
    session = r.json()
    sid = session["id"]
    r = client.post(f"/api/sessions/{sid}/open")
    r.raise_for_status()
    deadline = time.time() + 60
    while time.time() < deadline:
        session = client.get(f"/api/sessions/{sid}").json()
        if session["status"] == "ready":
            break
        time.sleep(0.5)
    assert session["status"] == "ready", session
    print("session ready at", session["current_url"])
    ws_result = asyncio.run(ws_check(args.base, dict(client.cookies), sid))
    print("websocket:", ws_result)
    assert ws_result["frames"] >= 1, ws_result
    r = client.post(f"/api/sessions/{sid}/navigate", json={"url": args.base + "/mock-platform/?login=1"})
    r.raise_for_status()
    time.sleep(2)
    session = client.get(f"/api/sessions/{sid}").json()
    print("navigated to", session["current_url"])
    assert "login=1" in session["current_url"], session["current_url"]

    # --- project + task bound to that session ---------------------------------------------------
    r = client.post("/api/projects", json={"name": "Controls project", "script_text": SCRIPT, "platform_key": "mock", "browser_session_id": sid})
    r.raise_for_status()
    pid = r.json()["id"]
    client.post(f"/api/projects/{pid}/analyze", json={}).raise_for_status()
    scenes = client.get(f"/api/projects/{pid}/scenes").json()
    assert len(scenes) == 3, len(scenes)

    # Login required → task must wait for a human instead of bypassing anything.
    r = client.post("/api/tasks", json={"project_id": pid, "browser_session_id": sid, "config": {"max_retries": 1, "mock_options": {"speed": "slow", "login_required": True}}})
    r.raise_for_status()
    tid = r.json()["id"]
    task = wait_status(client, tid, {"waiting_for_human"})
    print("waiting_for_human:", task["attention"]["kind"], "-", task["attention"]["reason"][:60])
    assert task["attention"]["kind"] == "login"
    # Take over, "log in" through the mock site's API using the browser session cookie jar is not
    # possible from here, so instead flip the task to a non-login run: skip scene 1 then stop.
    client.post(f"/api/tasks/{tid}/takeover").raise_for_status()
    task = wait_status(client, tid, {"waiting_for_human"})
    time.sleep(1)
    session = client.get(f"/api/sessions/{sid}").json()
    assert session["interactive"] is True, session
    print("human has control:", session["interactive"])
    client.post(f"/api/tasks/{tid}/release").raise_for_status()
    time.sleep(1.5)
    session = client.get(f"/api/sessions/{sid}").json()
    assert session["interactive"] is False, session
    task = wait_status(client, tid, {"waiting_for_human"})  # still needs login → back to waiting
    client.post(f"/api/tasks/{tid}/stop").raise_for_status()
    task = wait_status(client, tid, {"cancelled"})
    print("stopped:", task["status"])

    # --- pause / resume / skip / attention / retry ----------------------------------------------
    r = client.post("/api/tasks", json={"project_id": pid, "browser_session_id": sid, "config": {"max_retries": 0, "mock_options": {"speed": "slow", "fail_rate": 0.0}}})
    r.raise_for_status()
    tid = r.json()["id"]
    task = wait_status(client, tid, {"running"})
    time.sleep(2)
    client.post(f"/api/tasks/{tid}/pause").raise_for_status()
    task = wait_status(client, tid, {"paused"})
    print("paused at:", task["current_action"])
    time.sleep(2)
    client.post(f"/api/tasks/{tid}/resume").raise_for_status()
    task = wait_status(client, tid, {"running"})
    print("resumed")
    client.post(f"/api/tasks/{tid}/skip").raise_for_status()
    deadline = time.time() + 30
    while time.time() < deadline:
        scenes = client.get(f"/api/projects/{pid}/scenes").json()
        if scenes[0]["status"] == "skipped":
            break
        time.sleep(0.5)
    assert scenes[0]["status"] == "skipped", [s["status"] for s in scenes]
    print("scene_001 skipped; task continues with", client.get(f"/api/tasks/{tid}").json()["progress"]["current_scene_code"])
    client.post(f"/api/tasks/{tid}/stop").raise_for_status()
    wait_status(client, tid, {"cancelled"})

    # failing platform with no retries → needs attention → retry (works 2nd time) and skip
    r = client.post("/api/tasks", json={"project_id": pid, "browser_session_id": sid, "config": {"max_retries": 0, "retry_failed_only": False, "mock_options": {"speed": "instant", "fail_rate": 1.0}}})
    r.raise_for_status()
    tid = r.json()["id"]
    task = wait_status(client, tid, {"needs_attention"})
    print("needs attention:", task["attention"]["reason"][:70], task["attention"]["options"])
    # switch the mock to succeed, then retry the scene
    client.patch(f"/api/tasks/{tid}", json={"config": {"mock_options": {"speed": "instant", "fail_rate": 0.0}}})
    client.post(f"/api/tasks/{tid}/skip").raise_for_status()
    task = wait_status(client, tid, {"needs_attention", "completed", "failed"})
    if task["status"] == "needs_attention":
        client.post(f"/api/tasks/{tid}/skip").raise_for_status()
        task = wait_status(client, tid, {"needs_attention", "completed", "failed"})
    if task["status"] == "needs_attention":
        client.post(f"/api/tasks/{tid}/skip").raise_for_status()
        task = wait_status(client, tid, {"completed", "failed"})
    print("after skipping all:", task["status"], task["progress"])
    scenes = client.get(f"/api/projects/{pid}/scenes").json()
    assert all(s["status"] == "skipped" for s in scenes), [s["status"] for s in scenes]

    # retry whole task, now with the mock succeeding (config was changed while it was paused)
    r = client.post(f"/api/tasks/{tid}/retry")
    r.raise_for_status()
    task = wait_status(client, tid, {"completed", "failed"}, timeout=120)
    print("retry result:", task["status"], task["progress"])
    assert task["status"] == "completed", task

    # emergency stop with nothing running
    r = client.post("/api/tasks/stop-all")
    print("stop-all:", r.json())
    r = client.post(f"/api/sessions/{sid}/close")
    r.raise_for_status()
    deadline = time.time() + 30
    while time.time() < deadline:
        session = client.get(f"/api/sessions/{sid}").json()
        if session["status"] == "closed":
            break
        time.sleep(0.5)
    print("session closed:", session["status"], "saved state:", session["has_saved_state"])
    r = client.get("/api/activity", params={"task_id": tid, "limit": 5})
    assert r.status_code == 200 and r.json()
    r = client.get("/api/notifications")
    print("notifications:", r.json()["unread"], "unread")
    print("CONTROL SMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
