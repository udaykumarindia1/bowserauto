"""Worker process: claims queued tasks from the database and hosts browser sessions.

Queue semantics
---------------
* Claims are atomic ``UPDATE … WHERE status='queued' AND worker_id IS NULL`` statements, so any
  number of workers can run against the same database.
* Workers heartbeat running tasks; tasks owned by a worker whose heartbeat went stale are
  re-queued automatically (crash recovery, "a restart never loses the queue").
* A task bound to a browser session hosted by another live worker is left for that worker
  (session affinity); unhosted sessions are launched by whichever worker claims the task.
* Control requests (pause/resume/stop/takeover/…) are written to ``tasks.control`` by the API
  and applied here, both on a bus wake-up and on every poll.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select, update

from app.core.config import get_settings
from app.core.database import session_scope
from app.core.logging import configure_logging
from app.models import BrowserSession, BrowserSessionStatus, Organization, Task, TaskControl, TaskStatus
from app.queue.control import ControlToken
from app.realtime.bus import WORKER_WAKEUP, get_bus, start_bus, stop_bus
from app.realtime.events import emit, publish_event
from app.services.org_settings import effective_limits
from app.workers.executor import TaskExecutor
from app.workers.session_host import SessionHost

logger = logging.getLogger(__name__)

RUNNING_STATUSES = [
    TaskStatus.running.value,
    TaskStatus.paused.value,
    TaskStatus.waiting_for_human.value,
    TaskStatus.needs_attention.value,
]


@dataclass
class TaskRun:
    task_id: uuid.UUID
    org_id: uuid.UUID
    token: ControlToken
    executor: TaskExecutor
    future: asyncio.Task
    session_id: uuid.UUID | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class Worker:
    def __init__(
        self, *, worker_id: str | None = None, capabilities: list[str] | None = None, embedded: bool = False
    ) -> None:
        self.settings = get_settings()
        self.id = worker_id or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self.capabilities = capabilities or list(self.settings.worker_capabilities)
        self.embedded = embedded
        self.runs: dict[uuid.UUID, TaskRun] = {}
        self.hosts: dict[uuid.UUID, SessionHost] = {}
        self._host_locks: dict[uuid.UUID, asyncio.Lock] = {}
        self._wake = asyncio.Event()
        self._stopping = False
        self._loop_task: asyncio.Task | None = None
        self._wake_task: asyncio.Task | None = None
        self._subscription = None
        self.started_at = datetime.now(UTC)

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self._stopping = False
        if not self.embedded:
            await start_bus()
        bus = get_bus()
        self._subscription = bus.subscribe(WORKER_WAKEUP, maxsize=1000)
        self._wake_task = asyncio.create_task(self._wake_loop(), name="worker-wake")
        self._loop_task = asyncio.create_task(self.run_forever(), name="worker-loop")
        logger.info(
            "Worker %s started (capabilities=%s, embedded=%s)",
            self.id,
            ",".join(self.capabilities),
            self.embedded,
        )

    async def stop(self) -> None:
        self._stopping = True
        for task in (self._loop_task, self._wake_task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        if self._subscription is not None:
            get_bus().unsubscribe(self._subscription)
        # Running tasks go back to the queue so another worker (or a restart) resumes them.
        for run in list(self.runs.values()):
            run.future.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await run.future
        self.runs.clear()
        for session_id in list(self.hosts):
            await self.close_host(session_id, reason="worker shutdown")
        if not self.embedded:
            await stop_bus()
        logger.info("Worker %s stopped", self.id)

    async def _wake_loop(self) -> None:
        assert self._subscription is not None
        async for _channel, message in self._subscription:
            self._wake.set()
            if message.get("control") and message.get("task_id"):
                with contextlib.suppress(Exception):
                    await self._apply_controls(only=uuid.UUID(str(message["task_id"])))

    async def run_forever(self) -> None:
        last_heartbeat = 0.0
        last_recovery = 0.0
        loop = asyncio.get_running_loop()
        while not self._stopping:
            try:
                now = loop.time()
                if now - last_recovery > max(10.0, self.settings.worker_stale_after_seconds / 2):
                    await self._recover_stale()
                    last_recovery = now
                await self._apply_controls()
                await self._reconcile_sessions()
                await self._reap_finished()
                while len(self.runs) < self.settings.max_concurrent_tasks_per_worker:
                    task_id = await self._claim_task()
                    if task_id is None:
                        break
                    await self._launch(task_id)
                if now - last_heartbeat > self.settings.worker_heartbeat_seconds:
                    await self._heartbeat()
                    last_heartbeat = now
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("worker loop error")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.settings.worker_poll_interval_seconds)
            except TimeoutError:
                pass
            self._wake.clear()

    # --------------------------------------------------------------------- claims
    async def _claim_task(self) -> uuid.UUID | None:
        stale = datetime.now(UTC) - timedelta(seconds=self.settings.worker_stale_after_seconds)
        async with session_scope() as db:
            candidates = (
                await db.execute(
                    select(Task.id, Task.organization_id, Task.browser_session_id, Task.kind)
                    .where(Task.status == TaskStatus.queued.value, Task.worker_id.is_(None))
                    .order_by(Task.priority.asc(), Task.queued_at.asc().nulls_last(), Task.created_at.asc())
                    .limit(25)
                )
            ).all()
            if not candidates:
                return None
            org_running: dict[uuid.UUID, int] = {}
            for task_id, org_id, session_id, kind in candidates:
                needs_browser = kind != "general"
                if needs_browser and "browser" not in self.capabilities:
                    continue
                if session_id is not None:
                    session = await db.get(BrowserSession, session_id)
                    if session is None:
                        continue
                    hb = session.last_heartbeat_at
                    if hb is not None and hb.tzinfo is None:
                        hb = hb.replace(tzinfo=UTC)
                    hosted_elsewhere = (
                        session.worker_id and session.worker_id != self.id and hb is not None and hb > stale
                    )
                    if hosted_elsewhere:
                        continue
                    if session.locked_by_task_id and session.locked_by_task_id != task_id:
                        other = await db.get(Task, session.locked_by_task_id)
                        if other is not None and other.status in RUNNING_STATUSES:
                            continue
                if org_id not in org_running:
                    rows = (
                        (
                            await db.execute(
                                select(Task.id).where(
                                    Task.organization_id == org_id,
                                    Task.status.in_(RUNNING_STATUSES),
                                    Task.worker_id.isnot(None),
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    org_running[org_id] = len(rows)
                org = await db.get(Organization, org_id)
                if org_running[org_id] >= effective_limits(org)["max_concurrent_tasks"]:
                    continue
                result = await db.execute(
                    update(Task)
                    .where(
                        Task.id == task_id, Task.status == TaskStatus.queued.value, Task.worker_id.is_(None)
                    )
                    .values(
                        worker_id=self.id,
                        status=TaskStatus.running.value,
                        locked_at=datetime.now(UTC),
                        heartbeat_at=datetime.now(UTC),
                        control=TaskControl.none.value,
                    )
                )
                if result.rowcount == 1:
                    return task_id
            return None

    async def _launch(self, task_id: uuid.UUID) -> None:
        async with session_scope() as db:
            task = await db.get(Task, task_id)
            if task is None:
                return
            org_id, session_id = task.organization_id, task.browser_session_id
        token = ControlToken()
        executor = TaskExecutor(self, task_id, token)
        future = asyncio.create_task(executor.run(), name=f"task-{task_id}")
        self.runs[task_id] = TaskRun(
            task_id=task_id,
            org_id=org_id,
            token=token,
            executor=executor,
            future=future,
            session_id=session_id,
        )
        logger.info("Claimed task %s", task_id)

    async def _reap_finished(self) -> None:
        for task_id, run in list(self.runs.items()):
            if run.future.done():
                self.runs.pop(task_id, None)
                exc = run.future.exception() if not run.future.cancelled() else None
                if exc:
                    logger.error("Task %s ended with %r", task_id, exc)
                self._wake.set()

    # ------------------------------------------------------------------- controls
    async def _apply_controls(self, only: uuid.UUID | None = None) -> None:
        """Apply pending control requests written by the API.

        Reads are done in one short transaction; each control then performs its own small writes.
        Nested write transactions are avoided on purpose (SQLite allows a single writer).
        """
        if not self.runs:
            return
        async with session_scope() as db:
            query = select(Task.id, Task.control, Task.status).where(
                Task.worker_id == self.id, Task.control != TaskControl.none.value
            )
            if only is not None:
                query = query.where(Task.id == only)
            rows = (await db.execute(query)).all()
        for task_id, control, status in rows:
            run = self.runs.get(task_id)
            if run is None:
                continue
            async with session_scope() as db:
                result = await db.execute(
                    update(Task)
                    .where(Task.id == task_id, Task.control == control)
                    .values(control=TaskControl.none.value)
                )
            if result.rowcount != 1:
                continue  # somebody else consumed it
            try:
                await self._apply_control(run, control, status)
            except Exception:
                logger.exception("Failed to apply control %s to task %s", control, task_id)

    async def _apply_control(self, run: TaskRun, control: str, status: str) -> None:
        token = run.token
        host = self.hosts.get(run.session_id) if run.session_id else None
        if host is None and run.executor.host is not None:
            host = run.executor.host
        now = datetime.now(UTC)
        if control == TaskControl.pause.value:
            token.request_pause("paused by user")
            async with session_scope() as db:
                await db.execute(
                    update(Task)
                    .where(Task.id == run.task_id)
                    .values(status=TaskStatus.paused.value, paused_at=now, current_action="Paused")
                )
                await emit(
                    db,
                    "task.paused",
                    run.org_id,
                    "Task paused",
                    task_id=run.task_id,
                    payload={"status": "paused"},
                )
            if host:
                await host.set_action("Paused", kind="control")
        elif control == TaskControl.resume.value:
            if host and host.interactive:
                await host.set_interactive(False)
            if not token.request_resume():
                # The executor never reached a checkpoint while paused, so its on_resume hook will
                # not fire: reconcile the persisted status here.
                await self._mark_running(run, "Task resumed")
        elif control == TaskControl.stop.value:
            token.request_stop("stopped by user")
            if host and host.interactive:
                await host.set_interactive(False)
        elif control == TaskControl.takeover.value:
            token.request_pause("human takeover", human=True)
            if host:
                await host.set_interactive(True)
                await host.set_action("Human has control", kind="human")
            async with session_scope() as db:
                await db.execute(
                    update(Task)
                    .where(Task.id == run.task_id)
                    .values(
                        status=TaskStatus.waiting_for_human.value,
                        human_control=True,
                        paused_at=now,
                        current_action="Human has control of the browser",
                    )
                )
                await emit(
                    db,
                    "human.takeover",
                    run.org_id,
                    "You have control of the browser. Click 'Return control to AI' when done.",
                    task_id=run.task_id,
                    session_id=run.session_id,
                    payload={"status": "waiting_for_human", "human_control": True},
                )
        elif control == TaskControl.release.value:
            if host:
                await host.set_interactive(False)
            async with session_scope() as db:
                await db.execute(update(Task).where(Task.id == run.task_id).values(human_control=False))
            if not token.request_resume():
                await self._mark_running(run, "Control returned to the agent")
        elif control == TaskControl.skip_scene.value:
            if host and host.interactive:
                await host.set_interactive(False)
            token.request_skip()
        elif control == TaskControl.retry_scene.value:
            if host and host.interactive:
                await host.set_interactive(False)
            token.request_retry()
        await publish_event("task.updated", run.org_id, {"control_applied": control}, task_id=run.task_id)

    async def _mark_running(self, run: TaskRun, message: str) -> None:
        async with session_scope() as db:
            result = await db.execute(
                update(Task)
                .where(
                    Task.id == run.task_id,
                    Task.status.in_(
                        [
                            TaskStatus.paused.value,
                            TaskStatus.waiting_for_human.value,
                            TaskStatus.needs_attention.value,
                        ]
                    ),
                )
                .values(status=TaskStatus.running.value, human_control=False, paused_at=None)
            )
            if result.rowcount:
                await emit(
                    db,
                    "task.resumed",
                    run.org_id,
                    message,
                    task_id=run.task_id,
                    session_id=run.session_id,
                    payload={"status": "running"},
                )

    # ------------------------------------------------------------------ sessions
    async def get_or_start_host(self, session_id: uuid.UUID, org_id: uuid.UUID) -> SessionHost:
        lock = self._host_locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            host = self.hosts.get(session_id)
            if host is not None and host.is_alive():
                return host
            if host is not None:
                await self.close_host(session_id, reason="restart")
            if len(self.hosts) >= self.host_capacity():
                await self._make_room()
            async with session_scope() as db:
                session = await db.get(BrowserSession, session_id)
                if session is None:
                    raise RuntimeError("Browser session no longer exists")
                profile_dir = Path(session.profile_dir)
                start_url = session.start_url
                encrypted = session.storage_state_encrypted
                open_url = (session.metadata_ or {}).get("open_url")
                interactive = bool(session.interactive)
                await db.execute(
                    update(BrowserSession)
                    .where(BrowserSession.id == session_id)
                    .values(worker_id=self.id, desired_state="open")
                )
            profile_dir.mkdir(parents=True, exist_ok=True)
            fresh_profile = not any(profile_dir.iterdir())
            host = SessionHost(
                session_id=session_id,
                org_id=org_id,
                profile_dir=profile_dir,
                bus=get_bus(),
                worker_id=self.id,
                start_url=start_url,
            )
            host.interactive = interactive
            host.on_closed.append(self._on_host_closed)
            await host.start(url=open_url or None)
            if fresh_profile and encrypted:
                await host.restore_storage_state(encrypted)
                if start_url:
                    with contextlib.suppress(Exception):
                        await host.driver.reload()
            if open_url:
                async with session_scope() as db:
                    session = await db.get(BrowserSession, session_id)
                    if session:
                        session.metadata_ = {
                            k: v for k, v in (session.metadata_ or {}).items() if k != "open_url"
                        }
            self.hosts[session_id] = host
            return host

    async def close_host(self, session_id: uuid.UUID, *, reason: str) -> None:
        host = self.hosts.pop(session_id, None)
        if host is None:
            return
        with contextlib.suppress(Exception):
            await host.stop(reason)

    def _on_host_closed(self, host: SessionHost) -> None:
        if self.hosts.get(host.session_id) is host:
            self.hosts.pop(host.session_id, None)

    def host_capacity(self) -> int:
        """How many browsers this worker keeps open at once (see ``MAX_OPEN_SESSIONS_PER_WORKER``)."""
        configured = self.settings.max_open_sessions_per_worker
        return configured if configured > 0 else max(2, self.settings.max_concurrent_tasks_per_worker + 2)

    async def _make_room(self) -> bool:
        """Evict the longest-idle browser that no task or human is using.

        Profiles and encrypted storage state stay on disk, so the evicted session simply relaunches
        the next time it is needed. Returns False when every open browser is busy.
        """
        grace = self.settings.session_interactive_idle_seconds
        candidates = [
            (host.interactive, -host.idle_seconds(), session_id)
            for session_id, host in self.hosts.items()
            if host.in_use_by_task is None and (not host.interactive or host.idle_seconds() > grace)
        ]
        if not candidates:
            return False
        # Prefer browsers nobody is looking at, then the human-controlled one idle the longest.
        candidates.sort()
        _interactive, _idle, victim = candidates[0]
        logger.info("Evicting idle browser session %s to free a slot on %s", victim, self.id)
        await self.close_host(victim, reason="closed to free a browser slot")
        return True

    async def _note_waiting_for_slot(self, session_id: uuid.UUID) -> None:
        """Make a session that cannot open yet say so instead of silently staying closed."""
        message = f"Waiting for a free browser slot ({len(self.hosts)}/{self.host_capacity()} browsers busy on {self.id})"
        async with session_scope() as db:
            await db.execute(
                update(BrowserSession)
                .where(BrowserSession.id == session_id, BrowserSession.error != message)
                .values(error=message)
            )

    async def _reconcile_sessions(self) -> None:
        """Open/close browser sessions to match the desired state recorded by the API."""
        if "browser" not in self.capabilities:
            return
        async with session_scope() as db:
            wanted = (
                await db.execute(
                    select(BrowserSession.id, BrowserSession.organization_id).where(
                        BrowserSession.desired_state == "open",
                        or_(BrowserSession.worker_id.is_(None), BrowserSession.worker_id == self.id),
                    )
                )
            ).all()
            closing = (
                (
                    await db.execute(
                        select(BrowserSession.id).where(
                            BrowserSession.desired_state == "closed", BrowserSession.worker_id == self.id
                        )
                    )
                )
                .scalars()
                .all()
            )
        for session_id, org_id in wanted:
            host = self.hosts.get(session_id)
            if host is not None and host.is_alive():
                continue
            if len(self.hosts) >= self.host_capacity() and not await self._make_room():
                await self._note_waiting_for_slot(session_id)
                continue
            async with session_scope() as db:
                claimed = await db.execute(
                    update(BrowserSession)
                    .where(
                        BrowserSession.id == session_id,
                        or_(BrowserSession.worker_id.is_(None), BrowserSession.worker_id == self.id),
                    )
                    .values(worker_id=self.id)
                )
                if claimed.rowcount != 1:
                    continue
            try:
                await self.get_or_start_host(session_id, org_id)
            except Exception as exc:
                logger.warning("Could not open session %s: %s", session_id, exc)
        for session_id in closing:
            host = self.hosts.get(session_id)
            if host is None:
                async with session_scope() as db:
                    await db.execute(
                        update(BrowserSession)
                        .where(BrowserSession.id == session_id, BrowserSession.worker_id == self.id)
                        .values(worker_id=None, status=BrowserSessionStatus.closed.value, interactive=False)
                    )
                continue
            if host.in_use_by_task is not None:
                continue
            await self.close_host(session_id, reason="closed by user")
        # Idle sessions (no task, no human) are closed to free memory; the profile stays on disk.
        idle_limit = self.settings.session_idle_timeout_seconds
        for session_id, host in list(self.hosts.items()):
            if host.in_use_by_task is None and not host.interactive and host.idle_seconds() > idle_limit:
                await self.close_host(session_id, reason="idle timeout")
            elif not host.is_alive() and host.in_use_by_task is None:
                await self.close_host(session_id, reason="browser exited")

    # ----------------------------------------------------------------- heartbeat
    async def _heartbeat(self) -> None:
        now = datetime.now(UTC)
        self._touch_liveness(now)
        async with session_scope() as db:
            if self.runs:
                await db.execute(update(Task).where(Task.id.in_(list(self.runs))).values(heartbeat_at=now))
            if self.hosts:
                await db.execute(
                    update(BrowserSession)
                    .where(BrowserSession.id.in_(list(self.hosts)), BrowserSession.worker_id == self.id)
                    .values(last_heartbeat_at=now)
                )
        for host in list(self.hosts.values()):
            with contextlib.suppress(Exception):
                await host.heartbeat()

    async def _recover_stale(self) -> None:
        threshold = datetime.now(UTC) - timedelta(seconds=self.settings.worker_stale_after_seconds)
        async with session_scope() as db:
            stale_tasks = (
                (
                    await db.execute(
                        select(Task).where(
                            Task.status.in_(RUNNING_STATUSES),
                            Task.worker_id.isnot(None),
                            Task.worker_id != self.id,
                            or_(Task.heartbeat_at.is_(None), Task.heartbeat_at < threshold),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for task in stale_tasks:
                hb = task.heartbeat_at
                if hb is not None and hb.tzinfo is None and hb.replace(tzinfo=UTC) >= threshold:
                    continue
                logger.warning("Re-queueing task %s from stale worker %s", task.id, task.worker_id)
                task.status = TaskStatus.queued.value
                task.worker_id = None
                task.heartbeat_at = None
                task.control = TaskControl.none.value
                task.human_control = False
                task.queued_at = task.queued_at or datetime.now(UTC)
                task.current_action = "Recovered after worker restart; waiting for a worker"
                await emit(
                    db,
                    "task.queued",
                    task.organization_id,
                    "Task re-queued after its worker stopped responding",
                    task_id=task.id,
                    project_id=task.project_id,
                    level="warning",
                    payload={"status": "queued", "recovered": True},
                )
            stale_sessions = (
                (
                    await db.execute(
                        select(BrowserSession).where(
                            BrowserSession.worker_id.isnot(None),
                            BrowserSession.worker_id != self.id,
                            BrowserSession.status.in_(
                                [
                                    BrowserSessionStatus.ready.value,
                                    BrowserSessionStatus.in_use.value,
                                    BrowserSessionStatus.launching.value,
                                ]
                            ),
                            or_(
                                BrowserSession.last_heartbeat_at.is_(None),
                                BrowserSession.last_heartbeat_at < threshold,
                            ),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for session in stale_sessions:
                hb = session.last_heartbeat_at
                if hb is not None and hb.tzinfo is None and hb.replace(tzinfo=UTC) >= threshold:
                    continue
                session.worker_id = None
                session.status = BrowserSessionStatus.closed.value
                session.interactive = False
                session.locked_by_task_id = None
                # keep desired_state so this worker re-opens it if the user wanted it open

    def _touch_liveness(self, now: datetime) -> None:
        """Write a small liveness file used by container health checks (see app.workers.healthcheck)."""
        try:
            path = liveness_path(self.settings.resolved_data_dir)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{self.id} {now.isoformat()} runs={len(self.runs)} hosts={len(self.hosts)}\n")
        except OSError as exc:  # never let a read-only volume kill the worker loop
            logger.debug("liveness file not writable: %s", exc)

    # --------------------------------------------------------------------- stats
    def stats(self) -> dict[str, Any]:
        return {
            "worker_id": self.id,
            "capabilities": self.capabilities,
            "embedded": self.embedded,
            "running_tasks": [str(t) for t in self.runs],
            "hosted_sessions": [str(s) for s in self.hosts],
            "started_at": self.started_at.isoformat(),
        }


_worker: Worker | None = None


def liveness_path(data_dir: Path) -> Path:
    """Per-process liveness file; one per container/host so a shared volume works too."""
    return data_dir / "workers" / f"{socket.gethostname()}-{os.getpid()}.alive"


def get_worker() -> Worker | None:
    return _worker


async def start_embedded_worker() -> Worker:
    global _worker
    _worker = Worker(embedded=True)
    await _worker.start()
    return _worker


async def stop_embedded_worker() -> None:
    global _worker
    if _worker is not None:
        await _worker.stop()
        _worker = None


async def _main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    from app.core.database import init_db

    await init_db()
    worker = Worker()
    await worker.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    import signal

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    await worker.stop()


if __name__ == "__main__":
    asyncio.run(_main())
