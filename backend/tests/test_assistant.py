"""AVINASH assistant console: command grammar, confirmations, task control, sessions, memory, briefing, telemetry."""

from __future__ import annotations

import pytest

from app.services.assistant_service import parse_command
from tests.conftest import ApiClient

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- grammar (pure)
@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("status", "status"),
        ("What's running?", "status"),
        ("stop everything", "stop_everything"),
        ("pause the current task", "task_control"),
        ("resume task Sunrise", "task_control"),
        ("take over", "task_control"),
        ("retry", "task_control"),
        ("go to https://example.com", "navigate"),
        ("open settings", "open_page"),
        ("show files", "open_page"),
        ("open runway", "open_session"),
        ("new session for google flow", "new_session"),
        ("run: open example.com and read the headline", "create_task"),
        ("remember that the brand colour is teal", "remember"),
        ("what do you remember", "recall"),
        ("forget brand colour", "forget"),
        ("change colour to red", "set_theme"),
        ("set theme to #ff00aa", "set_theme"),
        ("call me Tony", "set_name"),
        ("your name is Friday", "set_assistant"),
        ("generate videos for project Sunrise", "generate_project"),
        ("help", "help"),
        ("blah blah nothing", "unknown"),
    ],
)
def test_parse_command_grammar(text: str, intent: str) -> None:
    assert parse_command(text)[0] == intent


def test_parse_command_extracts_arguments() -> None:
    intent, args = parse_command("navigate to example.com/path in Runway session")
    assert intent == "navigate" and args["url"] == "example.com/path" and args["session"] == "Runway session"
    intent, args = parse_command("remember that my favourite platform is kling")
    assert intent == "remember" and args["value"] == "my favourite platform is kling"
    intent, args = parse_command("change the ui colour to #00ff88")
    assert intent == "set_theme" and args["color"].lower() == "#00ff88"


# --------------------------------------------------------------------------- API
async def test_help_status_and_briefing(client: ApiClient) -> None:
    response = await client.post("/api/assistant/command", json={"text": "help"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["intent"] == "help" and "stop everything" in body["reply"]

    response = await client.post("/api/assistant/command", json={"text": "status"})
    body = response.json()
    assert body["intent"] == "status" and body["ok"] and "Nothing is running" in body["reply"]
    assert body["data"]["status"]["queued"] == 0

    response = await client.get("/api/assistant/briefing?local_hour=9")
    assert response.status_code == 200
    briefing = response.json()
    assert briefing["text"].startswith("Good morning, sir. AVINASH online.")
    assert "status" in briefing

    response = await client.get("/api/assistant/capabilities")
    assert response.status_code == 200 and any(
        t["tool"] == "stop_everything" for t in response.json()["tools"]
    )


async def test_memory_commands_and_endpoints(client: ApiClient) -> None:
    response = await client.post(
        "/api/assistant/command", json={"text": "remember that the brand colour is teal"}
    )
    body = response.json()
    assert body["intent"] == "remember" and body["ok"], body
    assert body["data"]["fact"]["key"] == "brand colour" and body["data"]["fact"]["value"] == "teal"

    response = await client.post("/api/assistant/command", json={"text": "remember the office opens at 9"})
    assert response.json()["ok"]

    response = await client.get("/api/assistant/memory")
    facts = response.json()
    assert len(facts) == 2

    recall = (await client.post("/api/assistant/command", json={"text": "what do you remember"})).json()
    assert recall["intent"] == "recall" and "brand colour: teal" in recall["reply"]

    # Credentials are refused.
    response = await client.post("/api/assistant/memory", json={"value": "my password is hunter2"})
    assert response.status_code == 422, response.text
    refused = (
        await client.post(
            "/api/assistant/command", json={"text": "remember my api key is sk-abcdefgh12345678"}
        )
    ).json()
    assert refused["ok"] is False and "refuses" in refused["reply"]

    forgotten = (await client.post("/api/assistant/command", json={"text": "forget brand colour"})).json()
    assert forgotten["ok"] and "1 fact removed" in forgotten["reply"]
    assert len((await client.get("/api/assistant/memory")).json()) == 1

    created = await client.post("/api/assistant/memory", json={"key": "studio", "value": "Patna"})
    assert created.status_code == 201
    deleted = await client.delete(f"/api/assistant/memory/{created.json()['id']}")
    assert deleted.status_code == 204
    wipe = (await client.post("/api/assistant/command", json={"text": "forget everything"})).json()
    assert wipe["confirm"] and wipe["confirm"]["title"] == "CLEAR MEMORY"
    wiped = (
        await client.post("/api/assistant/command", json={"text": "forget everything", "confirmed": True})
    ).json()
    assert wiped["ok"]
    assert (await client.get("/api/assistant/memory")).json() == []


async def test_task_control_via_commands(client: ApiClient, fake_sites) -> None:
    fake_sites(generation_seconds=5.0)
    session = await client.create_session()
    project = await client.create_project(name="Sunrise film")
    task = await client.create_task(project["id"], session_id=session["id"], title="Sunrise render")
    await client.wait_until(
        task["id"], lambda t: t["status"] == "running" and (t["progress"] or {}).get("current_scene_code")
    )

    paused = (await client.post("/api/assistant/command", json={"text": "pause task Sunrise"})).json()
    assert paused["intent"] == "task.pause" and paused["navigate"] == f"/tasks/{task['id']}", paused
    await client.wait_task(task["id"], {"paused"})

    resumed = (
        await client.post(
            "/api/assistant/command", json={"text": "resume", "context": {"task_id": task["id"]}}
        )
    ).json()
    assert resumed["intent"] == "task.resume"
    await client.wait_task(task["id"], {"running"})

    status = (await client.post("/api/assistant/command", json={"text": "what's running"})).json()
    assert "1 task running" in status["reply"] and "Sunrise render" in status["reply"]

    # Destructive controls ask for confirmation first, then act.
    stop = (await client.post("/api/assistant/command", json={"text": "stop everything"})).json()
    assert stop["confirm"]["title"] == "STOP EVERYTHING" and not stop["actions"]
    assert (await client.task(task["id"]))["status"] == "running"
    stopped = (
        await client.post("/api/assistant/command", json={"text": "stop everything", "confirmed": True})
    ).json()
    assert stopped["actions"][0]["type"] == "task.stop_all"
    await client.wait_task(task["id"], {"cancelled"})

    missing = (await client.post("/api/assistant/command", json={"text": "pause task doesnotexist"})).json()
    assert missing["ok"] is False and "cannot find" in missing["reply"]


async def test_sessions_navigation_and_browser_tasks(client: ApiClient, fake_sites) -> None:
    created = (await client.post("/api/assistant/command", json={"text": "new session for mock"})).json()
    assert created["intent"] == "new_session" and created["actions"][0]["type"] == "session.create", created
    session_id = created["data"]["session_id"]
    session = await client.wait_session(session_id, {"ready", "in_use"})
    assert session["platform_key"] == "mock"

    opened = (await client.post("/api/assistant/command", json={"text": "open Mock platform session"})).json()
    assert opened["intent"] == "open_session" and opened["data"]["session_id"] == session_id

    nav = (
        await client.post(
            "/api/assistant/command",
            json={
                "text": "go to https://mock.test/mock-platform/?login=1",
                "context": {"session_id": session_id},
            },
        )
    ).json()
    assert nav["intent"] == "navigate" and nav["actions"][0]["url"].startswith("https://mock.test/")

    page = (await client.post("/api/assistant/command", json={"text": "open the activity log"})).json()
    assert page["navigate"] == "/activity"

    queued = (
        await client.post(
            "/api/assistant/command",
            json={
                "text": "run: open the generator and read the page title",
                "context": {"session_id": session_id},
            },
        )
    ).json()
    assert queued["confirm"]["title"] == "QUEUE BROWSER TASK"
    queued = (
        await client.post(
            "/api/assistant/command",
            json={
                "text": "run: open the generator and read the page title",
                "context": {"session_id": session_id},
                "confirmed": True,
            },
        )
    ).json()
    assert queued["intent"] == "create_task" and queued["actions"][0]["type"] == "task.create", queued
    task = await client.task(queued["data"]["task_id"])
    assert task["kind"] == "browser_workflow" and task["browser_session_id"] == session_id
    await client.wait_until(
        task["id"],
        lambda t: t["status"] in {"running", "completed", "failed", "needs_attention", "waiting_for_human"},
    )
    stop = (
        await client.post(
            "/api/assistant/command",
            json={"text": "stop", "context": {"task_id": task["id"]}, "confirmed": True},
        )
    ).json()
    assert stop["intent"] in {"task.stop", "task_control"}, stop
    await client.wait_task(task["id"], {"cancelled", "completed", "failed"})

    unknown = (await client.post("/api/assistant/command", json={"text": "blah blah nothing"})).json()
    assert unknown["ok"] is False and unknown["intent"] == "unknown"


async def test_personalisation_and_theme_commands(client: ApiClient) -> None:
    named = (await client.post("/api/assistant/command", json={"text": "call me Tony"})).json()
    assert named["client"] == {"action": "set_user_name", "value": "Tony"}
    renamed = (await client.post("/api/assistant/command", json={"text": "your name is Friday"})).json()
    assert (
        renamed["client"] == {"action": "set_assistant_name", "value": "Friday"}
        and "Tony" in renamed["reply"]
    )
    me = (await client.get("/api/auth/me")).json()
    assert me["user"]["preferences"]["jarvis"] == {"user_name": "Tony", "assistant_name": "Friday"}
    briefing = (await client.get("/api/assistant/briefing?local_hour=20")).json()
    assert briefing["text"].startswith("Good evening, Tony. Friday online.")

    theme = (await client.post("/api/assistant/command", json={"text": "change colour to red"})).json()
    assert theme["client"] == {"action": "set_color", "value": "#ff3355"}
    theme = (await client.post("/api/assistant/command", json={"text": "set theme to #ABCDEF"})).json()
    assert theme["client"]["value"] == "#abcdef"


async def test_viewer_cannot_act_but_can_ask(client: ApiClient, other_client: ApiClient) -> None:
    other = (await other_client.get("/api/auth/me")).json()["user"]["email"]
    added = await client.post("/api/settings/members", json={"email": other, "role": "viewer"})
    assert added.status_code in (200, 201), added.text
    org_id = (await client.get("/api/auth/me")).json()["organization"]["id"]
    switched = await other_client.post(f"/api/auth/switch-organization/{org_id}")
    assert switched.status_code == 200, switched.text
    other_client.csrf = switched.json()["csrf_token"]

    status = (await other_client.post("/api/assistant/command", json={"text": "status"})).json()
    assert status["ok"]
    denied = (await other_client.post("/api/assistant/command", json={"text": "stop everything"})).json()
    assert denied["ok"] is False and denied["data"]["code"] == "forbidden"


async def test_system_metrics_endpoint(client: ApiClient, anon: ApiClient) -> None:
    assert (await anon.get("/api/assistant/system")).status_code == 401
    response = await client.get("/api/assistant/system")
    assert response.status_code == 200
    frame = response.json()
    assert {
        "cpu_percent",
        "memory",
        "network",
        "disk",
        "uptime_seconds",
        "process_uptime_seconds",
        "environment",
    } <= set(frame)
    assert frame["environment"] == "test"
