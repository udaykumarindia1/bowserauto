"""Shared pytest fixtures.

The suite runs the real FastAPI application (lifespan included) against a temporary SQLite
database with the in-memory *fake* browser backend, an in-process worker and the in-memory
event bus. No Chromium, no network, no AI credits are involved.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

# --------------------------------------------------------------------------- environment
_TMP = Path(tempfile.mkdtemp(prefix="bowserauto-tests-"))
os.environ.update(
    {
        "APP_ENV": "test",
        "DATABASE_URL": f"sqlite+aiosqlite:///{_TMP / 'test.db'}",
        "DATA_DIR": str(_TMP / "data"),
        "SECRET_KEY": "test-secret-key-0123456789-abcdefghijklmnopqrstuvwxyz",
        "BROWSER_BACKEND": "fake",
        "LLM_PROVIDER": "none",
        "REDIS_URL": "",
        "EMBEDDED_WORKER": "true",
        "RATE_LIMIT_PER_MINUTE": "100000",
        "AUTH_RATE_LIMIT_PER_MINUTE": "100000",
        "WORKER_POLL_INTERVAL_SECONDS": "0.05",
        "WORKER_HEARTBEAT_SECONDS": "0.5",
        "WORKER_STALE_AFTER_SECONDS": "5",
        "MAX_CONCURRENT_TASKS_PER_WORKER": "1",
        "MOCK_PLATFORM_URL": "http://mock.test/mock-platform/",
        "ALLOWED_ORIGINS": "http://testserver",
        "COOKIE_SECURE": "false",
        "LOG_LEVEL": "WARNING",
    }
)

import httpx  # noqa: E402

from app.browser.fake_driver import FakeVideoSite, all_sites, configure_sites  # noqa: E402
from app.main import create_app  # noqa: E402
from app.workers.main import Worker  # noqa: E402

BASE_URL = "http://testserver"
PASSWORD = "Sup3r-secret-password!"


# ------------------------------------------------------------------------------ helpers
class ApiClient:
    """httpx wrapper that mimics the browser: cookie jar + CSRF header + Origin."""

    def __init__(self, app: Any) -> None:
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL, timeout=30)
        self.csrf: str | None = None
        self.user: dict[str, Any] | None = None
        self.organization: dict[str, Any] | None = None

    async def aclose(self) -> None:
        await self.http.aclose()

    def _headers(self, extra: dict[str, str] | None, *, csrf: bool) -> dict[str, str]:
        headers = {"Origin": BASE_URL}
        if csrf and self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        if extra:
            headers.update(extra)
        return headers

    async def request(
        self,
        method: str,
        url: str,
        *,
        csrf: bool = True,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        return await self.http.request(method, url, headers=self._headers(headers, csrf=csrf), **kwargs)

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("POST", url, **kwargs)

    async def patch(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("PATCH", url, **kwargs)

    async def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("DELETE", url, **kwargs)

    async def register(
        self, email: str | None = None, *, organization_name: str | None = None, full_name: str = "Test User"
    ) -> dict[str, Any]:
        email = email or f"user-{uuid.uuid4().hex[:10]}@example.com"
        response = await self.post(
            "/api/auth/register",
            json={
                "email": email,
                "password": PASSWORD,
                "full_name": full_name,
                "organization_name": organization_name or f"Org {email.split('@')[0]}",
            },
        )
        assert response.status_code == 201, response.text
        data = response.json()
        self.csrf = data["csrf_token"]
        self.user = data["user"]
        self.organization = data["organization"]
        self.user["password"] = PASSWORD
        return data

    async def login(self, email: str, password: str = PASSWORD) -> httpx.Response:
        response = await self.post("/api/auth/login", json={"email": email, "password": password})
        if response.status_code == 200:
            data = response.json()
            self.csrf = data["csrf_token"]
            self.user = data["user"]
            self.organization = data["organization"]
        return response

    # ----------------------------------------------------------------- domain helpers
    async def create_project(
        self, *, script: str | None = None, platform_key: str = "mock", analyze: bool = True, **overrides: Any
    ) -> dict[str, Any]:
        payload = {
            "name": overrides.pop("name", f"Project {uuid.uuid4().hex[:6]}"),
            "template_key": overrides.pop("template_key", "ai_video_generation"),
            "script_text": script if script is not None else SAMPLE_SCRIPT,
            "platform_key": platform_key,
            **overrides,
        }
        response = await self.post("/api/projects", json=payload)
        assert response.status_code == 201, response.text
        project = response.json()
        if analyze and payload["script_text"]:
            response = await self.post(
                f"/api/projects/{project['id']}/analyze", json={"replace_existing": True}
            )
            assert response.status_code == 200, response.text
        return project

    async def scenes(self, project_id: str) -> list[dict[str, Any]]:
        response = await self.get(f"/api/projects/{project_id}/scenes")
        assert response.status_code == 200, response.text
        return response.json()

    async def create_session(
        self, *, name: str = "Mock session", platform_key: str = "mock", open: bool = True
    ) -> dict[str, Any]:
        response = await self.post("/api/sessions", json={"name": name, "platform_key": platform_key})
        assert response.status_code == 201, response.text
        session = response.json()
        if open:
            response = await self.post(f"/api/sessions/{session['id']}/open")
            assert response.status_code == 200, response.text
            session = await self.wait_session(session["id"], {"ready", "in_use"})
        return session

    async def wait_session(self, session_id: str, statuses: set[str], timeout: float = 20) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout
        last: dict[str, Any] = {}
        while asyncio.get_running_loop().time() < deadline:
            response = await self.get(f"/api/sessions/{session_id}")
            assert response.status_code == 200, response.text
            last = response.json()
            if last["status"] in statuses:
                return last
            await asyncio.sleep(0.05)
        raise AssertionError(f"session did not reach {statuses}: {last}")

    async def create_task(
        self,
        project_id: str,
        *,
        session_id: str | None = None,
        config: dict[str, Any] | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"project_id": project_id, "config": config or {}, **extra}
        if session_id:
            payload["browser_session_id"] = session_id
        response = await self.post("/api/tasks", json=payload)
        assert response.status_code == 201, response.text
        return response.json()

    async def task(self, task_id: str) -> dict[str, Any]:
        response = await self.get(f"/api/tasks/{task_id}")
        assert response.status_code == 200, response.text
        return response.json()

    async def wait_until(
        self, task_id: str, predicate: Callable[[dict[str, Any]], bool], timeout: float = 30
    ) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout
        last: dict[str, Any] = {}
        while asyncio.get_running_loop().time() < deadline:
            last = await self.task(task_id)
            if predicate(last):
                return last
            await asyncio.sleep(0.05)
        raise AssertionError(
            f"condition not met within {timeout}s: status={last.get('status')} action={last.get('current_action')} attention={last.get('attention')}"
        )

    async def wait_task(self, task_id: str, statuses: set[str], timeout: float = 30) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout
        last: dict[str, Any] = {}
        while asyncio.get_running_loop().time() < deadline:
            last = await self.task(task_id)
            if last["status"] in statuses:
                return last
            await asyncio.sleep(0.05)
        raise AssertionError(
            f"task did not reach {statuses} within {timeout}s: status={last.get('status')} action={last.get('current_action')} error={last.get('error')} attention={last.get('attention')}"
        )


SAMPLE_SCRIPT = """Scene 1: A lighthouse keeper climbs the spiral staircase at dusk, lantern in hand.

Scene 2: Waves crash against the rocks below as the beam sweeps across a stormy sea.

Scene 3: At dawn, a small fishing boat returns safely to the harbour while gulls circle overhead.
"""


# ------------------------------------------------------------------------------ fixtures
@pytest.fixture(scope="session")
def event_loop_policy():
    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture(scope="session")
async def app():
    application = create_app()
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture(scope="session")
async def worker(app) -> AsyncIterator[Worker]:
    instance = Worker(worker_id="test-worker", embedded=True)
    await instance.start()
    try:
        yield instance
    finally:
        await instance.stop()


@pytest.fixture
def fake_sites() -> Callable[..., None]:
    """Configure how the simulated video site behaves for sessions created during the test."""

    def configure(**options: Any) -> None:
        configure_sites(lambda _sid: FakeVideoSite(**options))

    configure()
    yield configure
    configure_sites(None)


@pytest.fixture
def site_lookup() -> Callable[[str], FakeVideoSite]:
    def lookup(session_id: str) -> FakeVideoSite:
        return all_sites()[str(session_id)]

    return lookup


@pytest.fixture
async def client(app, worker, fake_sites) -> AsyncIterator[ApiClient]:
    api = ApiClient(app)
    await api.register()
    try:
        yield api
    finally:
        await api.aclose()


@pytest.fixture
async def anon(app) -> AsyncIterator[ApiClient]:
    api = ApiClient(app)
    try:
        yield api
    finally:
        await api.aclose()


@pytest.fixture
async def other_client(app, worker) -> AsyncIterator[ApiClient]:
    api = ApiClient(app)
    await api.register()
    try:
        yield api
    finally:
        await api.aclose()
