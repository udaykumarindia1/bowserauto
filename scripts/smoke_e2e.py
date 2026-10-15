#!/usr/bin/env python3
"""End-to-end smoke test against a running API (uses the built-in mock platform).

Usage: python scripts/smoke_e2e.py [--base http://127.0.0.1:8000] [--speed instant]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid

import httpx

SCRIPT = """# The Lighthouse Keeper

INT. LIGHTHOUSE - NIGHT
MARA, an old keeper with silver hair, climbs the spiral staircase holding a lantern. The wind howls outside.

EXT. CLIFFS - DAWN
The storm has passed. Mara stands at the edge of the cliff watching a small boat approach through golden mist.

INT. LIGHTHOUSE KITCHEN - MORNING
A young sailor, TOM, dries his hands by the stove while Mara pours tea. They laugh about the storm.
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--speed", default="instant")
    parser.add_argument("--fail-rate", type=float, default=0.0)
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()

    client = httpx.Client(base_url=args.base, timeout=60, headers={"Origin": "http://localhost:3000"})
    email = f"smoke-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/auth/register", json={"email": email, "password": "SmokeTest-Password-123", "full_name": "Smoke Tester"})
    if r.status_code == 403:
        print("registration closed; logging in with env credentials is not supported in smoke test")
        return 1
    r.raise_for_status()
    csrf = r.json()["csrf_token"]
    client.headers["X-CSRF-Token"] = csrf
    print("registered", email)

    r = client.post("/api/projects", json={"name": "Smoke project", "script_text": SCRIPT, "platform_key": "mock", "output_settings": {"clip_duration_seconds": 8, "aspect_ratio": "16:9", "resolution": "720p"}})
    r.raise_for_status()
    project = r.json()
    pid = project["id"]
    print("project", pid)

    r = client.post(f"/api/projects/{pid}/analyze", json={"replace_existing": True})
    r.raise_for_status()
    scenes = r.json()["scenes"]
    print(f"analysed into {len(scenes)} scenes via {r.json()['method']}")
    assert len(scenes) >= 3, scenes

    # Edit scene 2 prompt, duplicate scene 1, delete the duplicate, reorder back.
    r = client.patch(f"/api/projects/{pid}/scenes/{scenes[1]['id']}", json={"prompt": scenes[1]["prompt"] + " Golden hour light."})
    r.raise_for_status()
    r = client.post(f"/api/projects/{pid}/scenes/{scenes[0]['id']}/duplicate")
    r.raise_for_status()
    dup = r.json()
    r = client.delete(f"/api/projects/{pid}/scenes/{dup['id']}")
    assert r.status_code == 204, r.text
    r = client.get(f"/api/projects/{pid}/scenes")
    scenes = r.json()
    assert [s["code"] for s in scenes] == [f"scene_{i:03d}" for i in range(1, len(scenes) + 1)], [s["code"] for s in scenes]

    r = client.post("/api/tasks", json={"project_id": pid, "kind": "video_generation", "platform_key": "mock", "config": {"max_retries": 2, "generation_timeout_seconds": 120, "mock_options": {"speed": args.speed, "fail_rate": args.fail_rate}}})
    r.raise_for_status()
    task = r.json()
    tid = task["id"]
    print("task", tid, task["status"])

    deadline = time.time() + args.timeout
    last = None
    while time.time() < deadline:
        r = client.get(f"/api/tasks/{tid}")
        r.raise_for_status()
        task = r.json()
        state = (task["status"], task["progress"].get("done"), task["current_action"])
        if state != last:
            print("  ", state)
            last = state
        if task["status"] in ("completed", "failed", "cancelled"):
            break
        if task["status"] in ("needs_attention", "waiting_for_human"):
            print("   attention:", task["attention"])
            client.post(f"/api/tasks/{tid}/retry").raise_for_status()
        time.sleep(1.5)
    print("final status", task["status"], json.dumps(task["progress"]))
    if task["status"] != "completed":
        r = client.get(f"/api/tasks/{tid}/activity")
        for row in r.json()[-25:]:
            print("   ", row["level"], row["event_type"], row["message"])
        return 1

    r = client.get("/api/files", params={"project_id": pid})
    r.raise_for_status()
    files = r.json()["items"]
    print("files", [(f["filename"], f["size_bytes"], f["duration_seconds"]) for f in files])
    assert len(files) == len(scenes), (len(files), len(scenes))
    dl = client.get(files[0]["download"]["url"])
    assert dl.status_code == 200 and dl.headers["content-type"].startswith("video/"), (dl.status_code, dl.headers)
    print("download ok", len(dl.content), "bytes")
    r = client.get(f"/api/tasks/{tid}/screenshots")
    print("screenshots", len(r.json()))
    r = client.get("/api/dashboard")
    r.raise_for_status()
    print("dashboard tasks", r.json()["tasks"])
    r = client.get(f"/api/projects/{pid}/export")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    print("zip export", len(r.content), "bytes")
    print("SMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
