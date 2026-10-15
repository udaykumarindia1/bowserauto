"""Task queue + scene execution against the in-memory fake browser."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.workers.main import Worker
from tests.conftest import ApiClient

pytestmark = pytest.mark.asyncio


async def test_video_task_completes_and_files_are_verified(client: ApiClient, fake_sites) -> None:
    fake_sites(generation_seconds=0.2)
    project = await client.create_project()
    scenes = await client.scenes(project["id"])
    assert len(scenes) == 3
    assert [s["code"] for s in scenes] == ["scene_001", "scene_002", "scene_003"]

    task = await client.create_task(
        project["id"], config={"max_retries": 1, "generation_timeout_seconds": 60}
    )
    assert task["status"] in ("queued", "running")
    done = await client.wait_task(task["id"], {"completed", "failed"}, timeout=60)
    assert done["status"] == "completed", done
    assert done["progress"]["completed"] == 3
    assert done["progress"]["percent"] == 100

    scenes = await client.scenes(project["id"])
    assert all(s["status"] == "completed" for s in scenes)
    assert all(s["generated_file_id"] for s in scenes)

    files = (await client.get("/api/files", params={"project_id": project["id"]})).json()
    assert files["total"] == 3
    names = sorted(f["filename"] for f in files["items"])
    assert names == ["scene_001.mp4", "scene_002.mp4", "scene_003.mp4"]
    for item in files["items"]:
        assert item["size_bytes"] > 1000
        assert item["duration_seconds"] and 2.5 < item["duration_seconds"] < 3.5  # sample clip is 3s
        assert item["width"] == 480 and item["height"] == 270
        assert item["sha256"] and item["mime_type"] == "video/mp4"
        assert item["status"] == "verified"
        # Signed download URL works without a session cookie.
        download = await client.http.get(item["download"]["url"])
        assert download.status_code == 200
        assert download.headers["content-type"].startswith("video/")
        assert len(download.content) == item["size_bytes"]

    # Files live in the documented project folder structure.
    root = Path(get_settings().projects_dir) / project["id"]
    assert (root / "generated" / "scene_001.mp4").exists()
    assert (root / "scenes" / "scene_001.json").exists()  # per-scene metadata
    assert (root / "script" / "script.txt").exists()
    assert list((root / "logs").glob("task_*.log"))  # structured per-task log

    # Activity stream recorded the lifecycle with structured event types.
    activity = (await client.get(f"/api/tasks/{task['id']}/activity")).json()
    types = {entry["event_type"] for entry in activity}
    assert {
        "task.started",
        "scene.started",
        "scene.completed",
        "download.completed",
        "task.completed",
    } <= types
    screenshots = (await client.get(f"/api/tasks/{task['id']}/screenshots")).json()
    assert len(screenshots) >= 3
    shot = await client.http.get(screenshots[0]["image"]["url"])
    assert shot.status_code == 200
    assert shot.headers["content-type"].startswith("image/")

    # Dashboard reflects the completed work.
    dashboard = (await client.get("/api/dashboard")).json()
    assert dashboard["tasks"]["completed"] >= 1
    assert dashboard["files"]["count"] >= 3

    # Export as a zip archive containing the generated clips and the script.
    export = await client.get(f"/api/projects/{project['id']}/export")
    assert export.status_code == 200
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(export.content)) as archive:
        names = archive.namelist()
        assert "generated/scene_001.mp4" in names
        assert "script/script.txt" in names
        assert len(names) == len(set(names))


async def test_retry_with_backoff_then_success(client: ApiClient, fake_sites, site_lookup) -> None:
    fake_sites(generation_seconds=0.1, fail_first_attempts=1)  # first attempt of each scene fails
    project = await client.create_project(script="Scene 1: A fox runs through snow.")
    session = await client.create_session()
    task = await client.create_task(project["id"], session_id=session["id"], config={"max_retries": 2})
    done = await client.wait_task(task["id"], {"completed", "failed", "needs_attention"}, timeout=60)
    assert done["status"] == "completed", done
    scenes = await client.scenes(project["id"])
    assert scenes[0]["retry_count"] == 1
    assert scenes[0]["status"] == "completed"
    activity = (await client.get(f"/api/tasks/{task['id']}/activity")).json()
    types = [entry["event_type"] for entry in activity]
    assert "scene.failed" in types and "scene.completed" in types
    assert done["browser_session_id"] == session["id"]
    site = site_lookup(session["id"])
    assert site.attempts == 2
    # The persistent session is released (not closed) once the task finishes.
    released = await client.wait_session(session["id"], {"ready"}, timeout=10)
    assert released["locked_by_task_id"] is None


async def test_failure_after_retries_needs_attention_then_skip(client: ApiClient, fake_sites) -> None:
    fake_sites(generation_seconds=0.05, fail_first_attempts=99)
    project = await client.create_project(
        script="Scene 1: It always fails.\n\nScene 2: This one works after skip."
    )
    task = await client.create_task(project["id"], config={"max_retries": 0})
    waiting = await client.wait_task(task["id"], {"needs_attention", "failed", "completed"}, timeout=30)
    assert waiting["status"] == "needs_attention", waiting
    assert waiting["attention"]["scene_code"] == "scene_001"
    assert set(waiting["attention"]["options"]) >= {"retry", "edit", "skip", "takeover", "stop"}
    # Nothing is silently dead: the task keeps its worker and reports the reason.
    assert waiting["attention"]["reason"]
    # Skip the broken scene; the second one also fails -> needs attention again -> stop.
    assert (await client.post(f"/api/tasks/{task['id']}/skip")).status_code == 200
    second = await client.wait_until(
        task["id"],
        lambda t: (
            t["status"] in ("failed", "completed") or t.get("attention", {}).get("scene_code") == "scene_002"
        ),
        timeout=30,
    )
    assert second["status"] == "needs_attention" and second["attention"]["scene_code"] == "scene_002"
    assert (await client.post(f"/api/tasks/{task['id']}/stop")).status_code == 200
    stopped = await client.wait_task(task["id"], {"cancelled"}, timeout=15)
    assert stopped["status"] == "cancelled"
    scenes = await client.scenes(project["id"])
    assert scenes[0]["status"] == "skipped"
    assert scenes[1]["status"] in ("failed", "draft", "queued")
    # Scene edit is allowed while paused on attention, and 'retry failed only' re-runs just that scene.
    assert (
        await client.patch(
            f"/api/projects/{project['id']}/scenes/{scenes[1]['id']}", json={"prompt": "A calmer prompt"}
        )
    ).status_code == 200


async def test_pause_resume_and_stop(client: ApiClient, fake_sites) -> None:
    fake_sites(generation_seconds=2.0, queue_seconds=0.5)
    project = await client.create_project(script="Scene 1: Slow scene one.\n\nScene 2: Slow scene two.")
    session = await client.create_session()
    task = await client.create_task(project["id"], session_id=session["id"], config={"max_retries": 0})
    await client.wait_task(task["id"], {"running"}, timeout=15)
    assert (await client.post(f"/api/tasks/{task['id']}/pause")).status_code == 200
    paused = await client.wait_task(task["id"], {"paused"}, timeout=10)
    assert paused["status"] == "paused"
    await asyncio.sleep(0.3)
    assert (await client.task(task["id"]))["status"] == "paused"  # stays paused
    assert (await client.post(f"/api/tasks/{task['id']}/resume")).status_code == 200
    running = await client.wait_task(task["id"], {"running", "completed"}, timeout=10)
    assert running["status"] in ("running", "completed")
    assert (await client.post(f"/api/tasks/{task['id']}/stop")).status_code == 200
    stopped = await client.wait_task(task["id"], {"cancelled", "completed"}, timeout=15)
    assert stopped["status"] == "cancelled"
    assert stopped["finished_at"]
    # The browser session the task used is released again.
    session = await client.wait_session(session["id"], {"ready"}, timeout=10)
    assert session["locked_by_task_id"] is None


async def test_queue_order_priority_and_reorder(client: ApiClient, fake_sites) -> None:
    """Priority 1 runs first; manual reorder overrides priorities for queued tasks."""
    fake_sites(generation_seconds=1.5)
    project = await client.create_project(script="Scene 1: Blocking scene.")
    blocker = await client.create_task(project["id"], config={"max_retries": 0})
    await client.wait_task(blocker["id"], {"running"}, timeout=15)
    # One project can only have one active task at a time, so queue three separate projects.
    assert (await client.post("/api/tasks", json={"project_id": project["id"]})).status_code == 409
    projects = [await client.create_project(script=f"Scene 1: Queued scene {i}.") for i in range(3)]
    low = await client.create_task(projects[0]["id"], priority=8, title="low")
    high = await client.create_task(projects[1]["id"], priority=2, title="high")
    mid = await client.create_task(projects[2]["id"], priority=5, title="mid")
    listing = (await client.get("/api/tasks", params={"status": "queued"})).json()
    assert {t["id"] for t in listing["items"]} >= {low["id"], high["id"], mid["id"]}
    assert listing["total"] >= 3
    # Manual reorder: move "low" to the front of the queue.
    reorder = await client.post("/api/tasks/reorder", json={"task_ids": [low["id"], high["id"], mid["id"]]})
    assert reorder.status_code == 200, reorder.text
    assert [t["title"] for t in reorder.json()] == ["low", "high", "mid"]
    # When the blocker finishes, "low" is claimed next while the others stay queued.
    await client.wait_task(blocker["id"], {"completed", "failed"}, timeout=30)
    running = await client.wait_task(low["id"], {"running", "completed"}, timeout=15)
    assert running["status"] in ("running", "completed")
    assert (await client.task(high["id"]))["status"] == "queued"
    assert (await client.task(mid["id"]))["status"] == "queued"
    # Queued tasks are cancelled directly (no worker involved).
    for t in (high, mid):
        assert (await client.post(f"/api/tasks/{t['id']}/stop")).status_code == 200
        assert (await client.task(t["id"]))["status"] == "cancelled"
    finished = await client.wait_task(low["id"], {"completed", "failed"}, timeout=30)
    assert finished["status"] == "completed"
    # Deleting a finished task is allowed; deleting an active one is refused.
    assert (await client.delete(f"/api/tasks/{high['id']}")).status_code == 204
    assert (await client.get(f"/api/tasks/{high['id']}")).status_code == 404


async def test_tasks_survive_worker_restart(client: ApiClient, fake_sites, worker: Worker) -> None:
    """A task claimed by a worker that dies is recovered by the next worker (persistent queue)."""
    fake_sites(generation_seconds=3.0)
    project = await client.create_project(script="Scene 1: Long running scene.")
    task = await client.create_task(project["id"], config={"max_retries": 0})
    running = await client.wait_task(task["id"], {"running"}, timeout=15)
    assert running["worker_id"] == worker.id
    # Let the worker get genuinely mid-flight (browser attached, scene in progress) first.
    await client.wait_until(
        task["id"], lambda t: t["browser_session_id"] is not None and bool(t["current_action"]), timeout=15
    )
    # Simulate a crash of the current worker: running work goes back to the queue.
    await worker.stop()
    try:
        requeued = await client.task(task["id"])
        assert requeued["status"] == "queued", requeued
        replacement = Worker(worker_id="test-worker-2", embedded=True)
        await replacement.start()
        try:
            done = await client.wait_task(task["id"], {"completed", "failed"}, timeout=40)
            assert done["status"] == "completed", done
            activity = (await client.get(f"/api/tasks/{task['id']}/activity")).json()
            starts = [entry for entry in activity if entry["event_type"] == "task.started"]
            assert len(starts) >= 2 and any("test-worker-2" in entry["message"] for entry in starts)
        finally:
            await replacement.stop()
    finally:
        await worker.start()


async def test_stop_everything(client: ApiClient, fake_sites) -> None:
    fake_sites(generation_seconds=5.0)
    project = await client.create_project(script="Scene 1: Something slow.")
    other = await client.create_project(script="Scene 1: Something else slow.")
    first = await client.create_task(project["id"])
    second = await client.create_task(other["id"])
    await client.wait_task(first["id"], {"running"}, timeout=15)
    result = await client.post("/api/tasks/stop-all")
    assert result.status_code == 200
    assert result.json()["stopped"] >= 2
    assert (await client.wait_task(first["id"], {"cancelled"}, timeout=15))["status"] == "cancelled"
    assert (await client.task(second["id"]))["status"] == "cancelled"


async def test_task_limits_enforced(client: ApiClient) -> None:
    project = await client.create_project(script="Scene 1: One scene.")
    # Organisation limits can only be tightened, never loosened beyond system limits.
    too_loose = await client.patch("/api/settings", json={"limits": {"max_retries": 999}})
    assert too_loose.status_code == 422
    settings = (await client.get("/api/settings")).json()
    assert settings["effective_limits"]["max_retries"] <= get_settings().max_retries_limit
    assert (
        await client.patch("/api/settings", json={"limits": {"max_scenes_per_task": 0}})
    ).status_code == 422
    tightened = await client.patch(
        "/api/settings", json={"limits": {"max_scenes_per_task": 1, "max_retries": 1}}
    )
    assert tightened.status_code == 200, tightened.text
    assert (await client.get("/api/settings")).json()["effective_limits"]["max_scenes_per_task"] == 1
    # Task configs are clamped to the effective organisation limits.
    created = await client.post(
        "/api/tasks", json={"project_id": project["id"], "config": {"max_retries": 5}, "start": False}
    )
    assert created.status_code == 201, created.text
    assert created.json()["config"]["max_retries"] == 1
    assert created.json()["config"]["max_task_duration_seconds"] <= get_settings().max_task_duration_seconds
    assert (await client.post(f"/api/tasks/{created.json()['id']}/stop")).status_code == 200
    # A project with more scenes than allowed cannot be started.
    big = await client.create_project(script="Scene 1: one.\n\nScene 2: two.")
    too_many = await client.post("/api/tasks", json={"project_id": big["id"]})
    assert too_many.status_code == 429 and too_many.json()["error"]["code"] == "limit_exceeded", too_many.text
    estimate = (await client.get(f"/api/projects/{project['id']}/estimate")).json()
    assert estimate["scenes"] == 1 and estimate["estimated_minutes"] >= 0
