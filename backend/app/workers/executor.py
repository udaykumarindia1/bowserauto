"""TaskExecutor: runs one task (all its scenes) inside a worker.

Reliability rules implemented here:

* every scene step is wrapped so that no exception can silently kill the task,
* retries with exponential back-off, then a *needs attention* pause with Retry/Edit/Skip/Take over,
* login walls / CAPTCHAs never get bypassed – the task waits for a human,
* after a human releases the browser the page is re-inspected and the scene restarts cleanly,
* downloaded files are verified (container, size, duration) before being accepted,
* file names never collide (older versions are archived),
* progress, screenshots and activity are persisted continuously so a restart can resume.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update

from app.agent.agent import AgentStep, BrowserAgent
from app.agent.llm import build_llm_client
from app.agent.tools import ToolContext, sanitize_action_args
from app.browser.policy_driver import ApprovalGuardDriver
from app.core.config import get_settings
from app.core.database import session_scope
from app.core.errors import (
    AutomationError,
    BrowserCrashed,
    ControlSignal,
    DownloadFailed,
    HumanInterventionRequired,
    LimitExceededError,
    LoginRequired,
    SceneRetryRequested,
    SceneSkipped,
    TaskStopped,
)
from app.core.logging import Timer, bind_log_context, clear_log_context, redact
from app.core.url_safety import sanitize_url
from app.models import (
    AgentAction,
    AgentRun,
    Asset,
    BrowserSession,
    GeneratedFile,
    GenerationState,
    Organization,
    Project,
    Scene,
    SceneStatus,
    Screenshot,
    Task,
    TaskControl,
    TaskStatus,
    TaskStep,
)
from app.platforms.base import AdapterContext, GenerationOutcome, PlatformAdapter, SceneSpec
from app.platforms.registry import create_adapter
from app.queue.control import ControlToken
from app.realtime.events import emit, publish_event
from app.services.assistant_service import memory_prompt_lines
from app.services.file_service import ensure_org_storage_capacity
from app.services.notification_service import notify
from app.services.org_settings import llm_overrides_for
from app.storage.files import inspect_media, sha256_file
from app.storage.paths import ensure_project_dirs, format_scene_filename, safe_filename, unique_path

if TYPE_CHECKING:
    from app.workers.session_host import SessionHost

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


class TaskExecutor:
    def __init__(self, worker: Any, task_id: uuid.UUID, control: ControlToken) -> None:
        self.worker = worker
        self.task_id = task_id
        self.control = control
        self.settings = get_settings()
        self.task: Task | None = None
        self.project: Project | None = None
        self.org: Organization | None = None
        self.host: SessionHost | None = None
        self.adapter: PlatformAdapter | None = None
        self.llm = None
        self.project_dir: Path | None = None
        self.started_at = time.monotonic()
        self.step_index = 0
        self.ephemeral_session = False
        self.scene_stats = {"completed": 0, "failed": 0, "skipped": 0}
        self.control.on_resume.append(self._on_resume)

    # ===================================================================== lifecycle
    async def run(self) -> None:
        bind_log_context(task_id=str(self.task_id), worker_id=self.worker.id)
        try:
            await self._load()
            assert self.task is not None
            await self._set_task(
                status=TaskStatus.running.value,
                started_at=self.task.started_at or _now(),
                error="",
                attention={},
                human_control=False,
            )
            await self._log(
                f"Task started on worker {self.worker.id}", event_type="task.started", status="running"
            )
            await self._acquire_browser()
            await self._screenshot("task_start")
            if self.task.kind == "video_generation":
                await self._run_scenes()
            else:
                await self._run_instruction_task()
            await self._finish()
        except TaskStopped as exc:
            await self._cancelled(str(exc))
        except ControlSignal as exc:  # stray skip/retry outside a scene: treat as a no-op resume
            logger.warning("Unhandled control signal %s at task level", type(exc).__name__)
            await self._fail(f"Internal control error: {type(exc).__name__}")
        except asyncio.CancelledError:
            await self._set_task(
                status=TaskStatus.queued.value,
                worker_id=None,
                heartbeat_at=None,
                control=TaskControl.none.value,
                current_action="",
            )
            raise
        except Exception as exc:  # last line of defence: the task must end in a visible state
            logger.exception("Task %s crashed", self.task_id)
            await self._fail(f"{type(exc).__name__}: {str(exc)[:500]}")
        finally:
            await self._release_browser()
            clear_log_context()

    async def _load(self) -> None:
        async with session_scope() as db:
            task = await db.get(Task, self.task_id)
            if task is None:
                raise TaskStopped("task vanished")
            self.task = task
            self.org = await db.get(Organization, task.organization_id)
            if task.project_id:
                self.project = await db.get(Project, task.project_id)
            db.expunge_all()
        self.llm = build_llm_client(llm_overrides_for(self.org))
        if self.project:
            self.project_dir = ensure_project_dirs(self.project.id)
        else:
            self.project_dir = self.settings.projects_dir / "_tasks" / str(self.task_id)
            for sub in ("generated", "logs", "references"):
                (self.project_dir / sub).mkdir(parents=True, exist_ok=True)
        config = dict(self.task.config or {})
        adapter_config = {
            "mock_options": config.get("mock_options") or {},
            "instructions": self.task.instructions,
        }
        if self.task.browser_session_id:
            async with session_scope() as db:
                session = await db.get(BrowserSession, self.task.browser_session_id)
                if session and session.start_url:
                    adapter_config["base_url"] = session.start_url
        if self.project and (self.project.output_settings or {}).get("platform_url"):
            adapter_config["base_url"] = self.project.output_settings["platform_url"]
        self.adapter = create_adapter(self.task.platform_key, adapter_config)

    async def _acquire_browser(self) -> None:
        assert self.task is not None
        session_id = self.task.browser_session_id
        if session_id is None:
            # Ephemeral profile for tasks without a saved session (e.g. the mock platform).
            session_id = await self._create_ephemeral_session()
            self.ephemeral_session = True
        self.host = await self.worker.get_or_start_host(session_id, self.task.organization_id)
        browser_policy = getattr(self.host.browser, "url_policy", None)
        if browser_policy is not None:
            browser_policy.set_allowed_domains((self.task.config or {}).get("allowed_domains") or [])
        self.host.acquire(self.task_id)
        # The agent drives the browser exclusively while a task runs: a session that was opened
        # manually (interactive) must not keep accepting human input until TAKE OVER is pressed.
        if self.host.interactive:
            await self.host.set_interactive(False)
        async with session_scope() as db:
            await db.execute(
                update(BrowserSession)
                .where(BrowserSession.id == session_id)
                .values(
                    locked_by_task_id=self.task_id, status="in_use", interactive=False, last_used_at=_now()
                )
            )
        await self.host.publish_status(force=True)
        await self._log("Browser session attached", event_type="browser.status", session_id=str(session_id))

    async def _create_ephemeral_session(self) -> uuid.UUID:
        assert self.task is not None
        session_id = uuid.uuid4()
        profile_dir = self.settings.profiles_dir / str(session_id)
        profile_dir.mkdir(parents=True, exist_ok=True)
        profile_dir.chmod(0o700)
        async with session_scope() as db:
            db.add(
                BrowserSession(
                    id=session_id,
                    organization_id=self.task.organization_id,
                    created_by=self.task.created_by,
                    name=f"Task browser · {self.task.title[:40]}",
                    platform_key=self.task.platform_key,
                    profile_dir=str(profile_dir),
                    status="launching",
                    desired_state="open",
                    metadata_={"ephemeral": True, "task_id": str(self.task_id)},
                )
            )
            await db.flush()
            await db.execute(
                update(Task).where(Task.id == self.task_id).values(browser_session_id=session_id)
            )
        self.task.browser_session_id = session_id
        await publish_event(
            "task.updated",
            self.task.organization_id,
            {"browser_session_id": str(session_id)},
            task_id=self.task_id,
        )
        return session_id

    async def _release_browser(self) -> None:
        if self.host is None or self.task is None:
            return
        host = self.host
        self.host = None
        browser_policy = getattr(host.browser, "url_policy", None)
        if browser_policy is not None:
            browser_policy.set_allowed_domains([])
        with contextlib.suppress(Exception):
            await host.set_interactive(False)
        host.release()
        async with session_scope() as db:
            await db.execute(
                update(BrowserSession)
                .where(BrowserSession.id == host.session_id, BrowserSession.locked_by_task_id == self.task_id)
                .values(locked_by_task_id=None, status="ready")
            )
        if self.ephemeral_session:
            await self.worker.close_host(host.session_id, reason="task finished")
            async with session_scope() as db:
                session = await db.get(BrowserSession, host.session_id)
                if session and (session.metadata_ or {}).get("ephemeral"):
                    await db.delete(session)
            import shutil

            shutil.rmtree(host.profile_dir, ignore_errors=True)
        else:
            with contextlib.suppress(Exception):
                await host.publish_status(force=True)

    # ======================================================================= scenes
    async def _pending_scenes(self) -> list[Scene]:
        assert self.task is not None and self.project is not None
        async with session_scope() as db:
            scenes = (
                (
                    await db.execute(
                        select(Scene).where(Scene.project_id == self.project.id).order_by(Scene.index)
                    )
                )
                .scalars()
                .all()
            )
            db.expunge_all()
        selected = set((self.task.config or {}).get("scene_ids") or [])
        pending = []
        for s in scenes:
            if selected and str(s.id) not in selected:
                continue
            if s.status in (
                SceneStatus.queued.value,
                SceneStatus.generating.value,
                SceneStatus.processing.value,
                SceneStatus.downloading.value,
                SceneStatus.waiting_for_human.value,
            ):
                pending.append(s)
        return pending

    async def _run_scenes(self) -> None:
        assert self.task is not None
        total = (self.task.progress or {}).get("total") or len(await self._pending_scenes())
        processed = {
            SceneStatus.completed.value: 0,
            SceneStatus.failed.value: 0,
            SceneStatus.skipped.value: 0,
        }
        async with session_scope() as db:
            rows = (
                (await db.execute(select(Scene.status).where(Scene.project_id == self.project.id)))
                .scalars()
                .all()
            )  # type: ignore[union-attr]
        # Resume support: scenes finished by a previous worker run count towards progress.
        done_before = sum(1 for r in rows if r == SceneStatus.completed.value)
        while True:
            self._check_task_timeout()
            pending = await self._pending_scenes()
            if not pending:
                break
            scene = pending[0]
            await self._update_progress(
                total=total,
                current=scene,
                completed=done_before
                + processed[SceneStatus.completed.value]
                - (self.task.progress or {}).get("completed_before", done_before),
                failed=processed[SceneStatus.failed.value],
                skipped=processed[SceneStatus.skipped.value],
            )
            outcome = await self._run_scene_with_recovery(scene)
            processed[outcome] += 1
            if outcome == SceneStatus.completed.value:
                self.scene_stats["completed"] += 1
            elif outcome == SceneStatus.failed.value:
                self.scene_stats["failed"] += 1
            else:
                self.scene_stats["skipped"] += 1
        await self._update_progress(
            total=total,
            current=None,
            completed=self.scene_stats["completed"],
            failed=self.scene_stats["failed"],
            skipped=self.scene_stats["skipped"],
            final=True,
        )

    def _check_task_timeout(self) -> None:
        limit = int(
            (self.task.config or {}).get("max_task_duration_seconds")
            or self.settings.max_task_duration_seconds
        )  # type: ignore[union-attr]
        if time.monotonic() - self.started_at > limit:
            raise RuntimeError(f"Task exceeded the maximum duration of {limit} seconds")

    async def _run_scene_with_recovery(self, scene: Scene) -> str:
        assert self.task is not None
        max_retries = int((self.task.config or {}).get("max_retries", self.settings.max_retries_default))
        attempt = 1
        policy = self.adapter.retry_policy if self.adapter else None
        while True:
            try:
                await self.control.checkpoint(raise_requests=False)
                self.control.clear_requests()
                await self._run_scene_attempt(scene, attempt)
                return SceneStatus.completed.value
            except TaskStopped:
                await self._set_scene(scene, status=SceneStatus.queued.value)
                raise
            except SceneSkipped:
                await self._set_scene(scene, status=SceneStatus.skipped.value)
                await self._clear_attention()
                await self._log(
                    f"{scene.code} skipped", event_type="scene.skipped", scene_id=scene.id, level="warning"
                )
                return SceneStatus.skipped.value
            except SceneRetryRequested:
                await self._clear_attention()
                await self._log(
                    f"Restarting {scene.code} on request", event_type="scene.updated", scene_id=scene.id
                )
                attempt = 1
                continue
            except (LoginRequired, HumanInterventionRequired) as exc:
                decision = await self._wait_for_human(
                    scene, str(exc), kind="login" if isinstance(exc, LoginRequired) else "verification"
                )
                if decision == "skip":
                    await self._set_scene(scene, status=SceneStatus.skipped.value)
                    return SceneStatus.skipped.value
                attempt = 1  # human intervention resets the retry budget
                continue
            except BrowserCrashed as exc:
                await self._log(
                    f"Browser crashed: {exc}. Relaunching browser.",
                    level="error",
                    event_type="browser.status",
                    scene_id=scene.id,
                )
                await self._relaunch_browser()
                attempt += 1
                if attempt > max_retries + 1:
                    decision = await self._needs_attention(scene, f"The browser crashed repeatedly ({exc})")
                    if decision == "skip":
                        await self._set_scene(scene, status=SceneStatus.skipped.value)
                        return SceneStatus.skipped.value
                    attempt = 1
                continue
            except AutomationError as exc:
                await self._set_scene(
                    scene, status=SceneStatus.failed.value, last_error=str(exc)[:1000], retry_count=attempt
                )
                await self._screenshot("failure", scene)
                await self._log(
                    f"{scene.code} attempt {attempt} failed: {exc}",
                    level="error",
                    event_type="scene.failed",
                    scene_id=scene.id,
                    attempt=attempt,
                    retryable=exc.retryable,
                )
                if exc.retryable and attempt <= max_retries:
                    delay = policy.delay_for(attempt) if policy else 5.0
                    await self._set_scene(scene, status=SceneStatus.queued.value)
                    await self._log(
                        f"Retrying {scene.code} in {int(delay)}s (attempt {attempt + 1} of {max_retries + 1})",
                        event_type="scene.updated",
                        scene_id=scene.id,
                        state=GenerationState.RETRYING.value,
                    )
                    await self._task_action(f"Retrying {scene.code} in {int(delay)}s…")
                    await self.control.sleep(delay)
                    attempt += 1
                    continue
                decision = await self._needs_attention(scene, str(exc), retryable=exc.retryable)
                if decision == "skip":
                    await self._set_scene(scene, status=SceneStatus.skipped.value)
                    return SceneStatus.skipped.value
                attempt = 1
                continue
            except asyncio.CancelledError:
                await self._set_scene(scene, status=SceneStatus.queued.value)
                raise
            except Exception as exc:
                logger.exception("Unexpected error in %s", scene.code)
                await self._set_scene(
                    scene,
                    status=SceneStatus.failed.value,
                    last_error=f"{type(exc).__name__}: {str(exc)[:800]}",
                    retry_count=attempt,
                )
                await self._log(
                    f"{scene.code} hit an unexpected error: {type(exc).__name__}: {str(exc)[:200]}",
                    level="error",
                    event_type="scene.failed",
                    scene_id=scene.id,
                )
                if attempt <= max_retries:
                    attempt += 1
                    await self.control.sleep(policy.delay_for(attempt) if policy else 5.0)
                    continue
                decision = await self._needs_attention(scene, f"{type(exc).__name__}: {str(exc)[:300]}")
                if decision == "skip":
                    await self._set_scene(scene, status=SceneStatus.skipped.value)
                    return SceneStatus.skipped.value
                attempt = 1
                continue

    async def _run_scene_attempt(self, scene: Scene, attempt: int) -> None:
        assert (
            self.task is not None
            and self.adapter is not None
            and self.host is not None
            and self.project is not None
            and self.project_dir is not None
        )
        timer = Timer()
        scene = await self._refresh_scene(scene)
        if scene.status == SceneStatus.skipped.value:
            raise SceneSkipped("scene marked as skipped")
        await self._set_scene(
            scene, status=SceneStatus.generating.value, started_at=_now(), retry_count=attempt - 1
        )
        await self._log(
            f"Starting {scene.code}" + (f" (attempt {attempt})" if attempt > 1 else ""),
            event_type="scene.started",
            scene_id=scene.id,
            attempt=attempt,
        )
        await self._task_action(f"{scene.code}: preparing")
        spec = await self._scene_spec(scene)
        step = await self._start_step(f"{scene.code}: generate", scene, attempt)
        download_dir = self.project_dir / "generated" / ".incoming"
        download_dir.mkdir(parents=True, exist_ok=True)

        async def state_cb(state: GenerationState, detail: str) -> None:
            mapping = {
                GenerationState.QUEUED: SceneStatus.generating,
                GenerationState.GENERATING: SceneStatus.generating,
                GenerationState.PROCESSING: SceneStatus.processing,
                GenerationState.DOWNLOADING: SceneStatus.downloading,
                GenerationState.DOWNLOAD_READY: SceneStatus.downloading,
            }
            if state in mapping:
                await self._set_scene(
                    scene,
                    status=mapping[state].value,
                    generation_metadata={
                        **(scene.generation_metadata or {}),
                        "state": state.value,
                        "detail": detail,
                    },
                )
            await self._task_action(
                f"{scene.code}: {state.value.lower().replace('_', ' ')}" + (f" – {detail}" if detail else ""),
                state=state.value,
            )
            await publish_event(
                "scene.updated",
                self.task.organization_id,
                {"state": state.value, "detail": detail, "status": scene.status},
                task_id=self.task_id,
                scene_id=scene.id,
                project_id=self.project.id,
            )

        adapter_driver = ApprovalGuardDriver(
            self.host.driver,
            approval_mode=str((self.task.config or {}).get("approval_mode", "confirm_writes")),
            approval_handler=self._request_tool_approval,
        )
        ctx = AdapterContext(
            driver=adapter_driver,
            control=self.control,
            scene=spec,
            log=self._adapter_log(scene),
            screenshot=lambda stage: self._screenshot(stage, scene),
            generation_timeout_seconds=int(
                (self.task.config or {}).get(
                    "generation_timeout_seconds", self.settings.generation_timeout_seconds
                )
            ),
            action_timeout_ms=int(
                (self.task.config or {}).get("action_timeout_seconds", self.settings.action_timeout_seconds)
            )
            * 1000,
            options={
                "agent_mode": (self.task.config or {}).get("agent_mode", "auto"),
                "instructions": self.task.instructions,
                "max_file_size_mb": int(
                    (self.task.config or {}).get("max_file_size_mb", self.settings.max_generated_file_size_mb)
                ),
                "mock_options": (self.task.config or {}).get("mock_options") or {},
            },
            run_agent=self._agent_runner(scene, spec),
            state_callback=state_cb,
            attempt=attempt,
        )
        try:
            outcome = await self.adapter.generate(ctx, download_dir)
        except BaseException as exc:
            await self._finish_step(
                step,
                "failed" if not isinstance(exc, ControlSignal) else "cancelled",
                error=type(exc).__name__,
            )
            raise
        await self._finish_step(
            step, "completed", result={"generation_seconds": round(outcome.generation_seconds, 1)}
        )
        await self._store_output(scene, outcome, attempt)
        await self._log(
            f"{scene.code} completed in {timer.ms // 1000}s",
            event_type="scene.completed",
            scene_id=scene.id,
            duration_ms=timer.ms,
        )

    async def _scene_spec(self, scene: Scene) -> SceneSpec:
        assert self.project is not None
        output = self.project.output_settings or {}
        refs: list[Path] = []
        ref_ids = list(scene.reference_asset_ids or [])
        if not ref_ids:
            # Fall back to character references for the characters in this scene.
            async with session_scope() as db:
                from app.models import Character

                chars = (
                    (await db.execute(select(Character).where(Character.project_id == self.project.id)))
                    .scalars()
                    .all()
                )
                names = {c.lower() for c in (scene.characters or [])}
                ref_ids = [
                    str(c.reference_asset_id)
                    for c in chars
                    if c.reference_asset_id and c.name.lower() in names
                ]
        if ref_ids:
            async with session_scope() as db:
                assets = (
                    (
                        await db.execute(
                            select(Asset).where(Asset.id.in_([uuid.UUID(r) for r in ref_ids if _is_uuid(r)]))
                        )
                    )
                    .scalars()
                    .all()
                )
                refs = [Path(a.path) for a in assets if Path(a.path).exists()]
        return SceneSpec(
            index=scene.index,
            code=scene.code,
            prompt=scene.prompt or scene.visual or scene.narration,
            negative_prompt=scene.negative_prompt,
            duration_seconds=float(scene.duration_seconds),
            aspect_ratio=output.get("aspect_ratio", "16:9"),
            resolution=output.get("resolution", "1080p"),
            reference_files=refs[:4],
            title=scene.title,
        )

    async def _store_output(self, scene: Scene, outcome: GenerationOutcome, attempt: int) -> None:
        assert (
            self.task is not None
            and self.project is not None
            and self.project_dir is not None
            and self.adapter is not None
        )
        src = outcome.download.path
        if not src.exists():
            raise DownloadFailed("Downloaded file disappeared before it could be verified")
        size = src.stat().st_size
        max_bytes = (
            int((self.task.config or {}).get("max_file_size_mb", self.settings.max_generated_file_size_mb))
            * 1024
            * 1024
        )
        if size > max_bytes:
            src.unlink(missing_ok=True)
            raise DownloadFailed(
                f"Downloaded file is larger than the {max_bytes // (1024 * 1024)} MB limit", retryable=False
            )
        if size == 0:
            src.unlink(missing_ok=True)
            raise DownloadFailed("Downloaded file is empty")
        media = None
        if outcome.output_kind == "video":
            media = inspect_media(src)
            if not media.valid:
                src.unlink(missing_ok=True)
                raise DownloadFailed(f"Downloaded file is not a valid video ({media.reason})")
            ext = ".webm" if media.container == "webm" else ".mp4"
        elif outcome.output_kind == "image":
            media = inspect_media(src)
            if not media.valid:
                src.unlink(missing_ok=True)
                raise DownloadFailed(f"Downloaded file is not a valid image ({media.reason})")
            ext = "." + (media.container or "png")
        else:
            ext = self.adapter.output_extension
        output = self.project.output_settings or {}
        folder_name = safe_filename(output.get("output_folder") or "generated", "generated")
        target_dir = self.project_dir / folder_name
        target_dir.mkdir(parents=True, exist_ok=True)
        filename = format_scene_filename(
            output.get("filename_pattern") or "scene_{index:03d}{ext}",
            index=scene.index,
            code=scene.code,
            project_name=self.project.name,
            platform=self.task.platform_key,
            ext=ext,
        )
        target = target_dir / filename
        archived_name: str | None = None
        try:
            async with session_scope() as db:
                # Keep the organization lock from quota calculation through file move and DB commit
                # so concurrent workers cannot both spend the same remaining disk quota.
                await ensure_org_storage_capacity(db, self.task.organization_id, size)
                if target.exists():
                    versions = target_dir / "versions"
                    versions.mkdir(exist_ok=True)
                    archived = unique_path(versions / f"{target.stem}_v1{target.suffix}")
                    target.rename(archived)
                    archived_name = archived.name
                src.replace(target)
                digest = sha256_file(target)
                # Deduplicate: a retry that produced the same bytes must not create a second record.
                existing = (
                    (
                        await db.execute(
                            select(GeneratedFile).where(
                                GeneratedFile.scene_id == scene.id, GeneratedFile.sha256 == digest
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
                if existing is not None:
                    existing.path = str(target)
                    existing.filename = target.name
                    record = existing
                else:
                    record = GeneratedFile(
                        organization_id=self.task.organization_id,
                        project_id=self.project.id,
                        task_id=self.task_id,
                        scene_id=scene.id,
                        filename=target.name,
                        path=str(target),
                        mime_type="video/mp4"
                        if ext == ".mp4"
                        else "video/webm"
                        if ext == ".webm"
                        else "text/markdown"
                        if ext == ".md"
                        else f"image/{ext.strip('.')}",
                        size_bytes=target.stat().st_size,
                        sha256=digest,
                        duration_seconds=media.duration_seconds if media else None,
                        width=media.width if media else None,
                        height=media.height if media else None,
                        platform_key=self.task.platform_key,
                        prompt=scene.prompt,
                        generation_seconds=round(outcome.generation_seconds, 1),
                        status="verified",
                        retry_count=attempt - 1,
                        metadata_={
                            "source_url": sanitize_url(outcome.download.url),
                            "suggested_filename": outcome.download.suggested_filename,
                            **outcome.details,
                        },
                    )
                    db.add(record)
                await db.flush()
                file_id = record.id
                scene_row = await db.get(Scene, scene.id)
                if scene_row:
                    scene_row.status = SceneStatus.completed.value
                    scene_row.generated_file_id = file_id
                    scene_row.completed_at = _now()
                    scene_row.last_error = ""
                    scene_row.retry_count = attempt - 1
                    scene_row.generation_metadata = {
                        **(scene_row.generation_metadata or {}),
                        "state": GenerationState.SUCCESS.value,
                        "generation_seconds": round(outcome.generation_seconds, 1),
                        "file": target.name,
                        "size_bytes": target.stat().st_size,
                        "duration_seconds": media.duration_seconds if media else None,
                        "resolution": f"{media.width}x{media.height}" if media and media.width else None,
                        "platform": self.task.platform_key,
                        "attempts": attempt,
                        "generated_at": _now().isoformat(),
                    }
        except LimitExceededError as exc:
            src.unlink(missing_ok=True)
            raise DownloadFailed(str(exc), retryable=False) from exc
        if archived_name:
            await self._log(
                f"Existing {filename} archived as versions/{archived_name}",
                event_type="file.created",
                scene_id=scene.id,
            )
        await self._write_scene_metadata(scene, target, media, outcome, attempt)
        await self._screenshot("download", scene)
        await self._log(
            f"{target.name} saved ({target.stat().st_size // 1024} KB)",
            event_type="download.completed",
            scene_id=scene.id,
            file_id=str(file_id),
            filename=target.name,
        )
        await publish_event(
            "file.created",
            self.task.organization_id,
            {"file_id": str(file_id), "filename": target.name},
            task_id=self.task_id,
            project_id=self.project.id,
            scene_id=scene.id,
        )

    async def _write_scene_metadata(
        self, scene: Scene, target: Path, media: Any, outcome: GenerationOutcome, attempt: int
    ) -> None:
        assert self.project_dir is not None and self.task is not None
        meta = {
            "scene_id": str(scene.id),
            "scene_code": scene.code,
            "prompt": scene.prompt,
            "platform": self.task.platform_key,
            "generation_seconds": round(outcome.generation_seconds, 1),
            "file_path": str(target),
            "file_size": target.stat().st_size,
            "duration_seconds": getattr(media, "duration_seconds", None),
            "resolution": f"{media.width}x{media.height}"
            if media and getattr(media, "width", None)
            else None,
            "status": "completed",
            "retry_count": attempt - 1,
            "generated_at": _now().isoformat(),
        }
        with contextlib.suppress(OSError):
            (self.project_dir / "scenes" / f"{scene.code}.json").write_text(
                json.dumps(meta, indent=2), encoding="utf-8"
            )

    # ============================================================= human / attention
    async def _wait_for_human(
        self, scene: Scene, reason: str, *, kind: str, persist_scene: bool = True
    ) -> str:
        assert self.task is not None
        if persist_scene:
            await self._set_scene(scene, status=SceneStatus.waiting_for_human.value, last_error=reason[:1000])
        attention = {
            "reason": reason,
            "kind": kind,
            "scene_id": str(scene.id),
            "scene_code": scene.code,
            "options": ["takeover", "retry", "skip", "stop"],
        }
        await self._set_task(
            status=TaskStatus.waiting_for_human.value,
            attention=attention,
            current_action=f"{scene.code}: waiting for human",
        )
        await self._log(
            f"{scene.code} needs you: {reason}",
            level="warning",
            event_type="human.required",
            scene_id=scene.id,
            kind=kind,
        )
        await notify(
            self.task.organization_id,
            "human_required",
            "Human intervention required",
            f"{self.task.title}: {reason}",
            task_id=self.task_id,
            severity="warning",
        )
        if self.host:
            await self.host.set_action(f"Waiting for human: {reason[:80]}", kind="human")
        self.control.request_pause(reason, human=False)
        return await self._await_decision(scene)

    async def _needs_attention(
        self, scene: Scene, reason: str, *, retryable: bool = True, persist_scene: bool = True
    ) -> str:
        assert self.task is not None
        attention = {
            "reason": reason,
            "kind": "failure",
            "scene_id": str(scene.id),
            "scene_code": scene.code,
            "retryable": retryable,
            "options": ["retry", "edit", "skip", "takeover", "stop"],
        }
        await self._set_task(
            status=TaskStatus.needs_attention.value,
            attention=attention,
            current_action=f"{scene.code} requires attention",
        )
        await self._log(
            f"{scene.code} requires attention: {reason}",
            level="error",
            event_type="task.attention",
            scene_id=scene.id,
        )
        await notify(
            self.task.organization_id,
            "task_attention",
            f"{scene.code} requires attention",
            f"{self.task.title}: {reason}",
            task_id=self.task_id,
            severity="error",
        )
        if self.host:
            await self.host.set_action(f"{scene.code} requires attention", kind="attention")
        self.control.request_pause(reason)
        return await self._await_decision(scene)

    async def _await_decision(self, scene: Scene) -> str:
        """Block until the user retries, skips, takes over & releases, or stops.

        Skip/retry arrive as control signals raised from the checkpoint; a plain resume (or a
        human takeover followed by release) means "re-inspect and try again".
        """
        try:
            await self.control.checkpoint()
        except SceneSkipped:
            return "skip"
        except SceneRetryRequested:
            await self._clear_attention()
            return "retry"
        await self._clear_attention()
        return "retry"

    async def _clear_attention(self) -> None:
        await self._set_task(status=TaskStatus.running.value, attention={}, human_control=False)

    async def _on_resume(self, was_human: bool) -> None:
        if was_human and self.host is not None:
            await self.host.set_interactive(False)
            await self._log(
                "Control returned to the agent; re-inspecting the page before continuing",
                event_type="human.released",
            )
            with contextlib.suppress(Exception):
                await self.host.driver.wait_for_load(state="domcontentloaded", timeout_ms=5000)
                snapshot = await self.host.driver.snapshot()
                await self._log(
                    f"Page re-inspected: {snapshot.title or snapshot.url}",
                    event_type="agent.thought",
                    url=sanitize_url(snapshot.url),
                )
            await self._screenshot("after_takeover")
        # The worker writes paused/waiting_for_human straight to the database, so the cached
        # ``self.task`` row is not authoritative here: always flip the task back to running.
        await self._set_task(status=TaskStatus.running.value, human_control=False, paused_at=None)
        await self._log("Task resumed", event_type="task.resumed")

    # ================================================================ generic agent
    async def _request_tool_approval(self, name: str, args: dict[str, Any]) -> bool:
        """Persist a privacy-safe action preview and wait for an organization member's decision."""
        assert self.task is not None
        if len(json.dumps(args, default=str)) > 100_000:
            raise AutomationError(
                "The proposed action is too large to review safely; split it into smaller actions.",
                retryable=False,
            )
        public_args = sanitize_action_args(args)
        if len(json.dumps(public_args, default=str)) > 6000:
            raise AutomationError(
                "The proposed action preview is too large to review safely; split it into smaller actions.",
                retryable=False,
            )
        self.control.begin_approval()
        attention = {
            "kind": "approval",
            "reason": f"Review the proposed {name} action before it runs.",
            "pending_action": {"tool": name, "args": public_args},
            "options": ["approve", "reject", "stop"],
            "since": _now().isoformat(),
        }
        await self._set_task(
            status=TaskStatus.waiting_for_human.value,
            attention=attention,
            current_action=f"Waiting for approval: {name}",
            human_control=False,
        )
        await self._log(
            f"Approval requested for {name}",
            level="warning",
            event_type="task.approval",
            action_name=name,
            action_args=public_args,
        )
        await notify(
            self.task.organization_id,
            "action_approval",
            "Browser action needs approval",
            f"{self.task.title}: review {name} in the task panel.",
            task_id=self.task_id,
            severity="warning",
        )
        if self.host:
            await self.host.set_action(f"Waiting for approval: {name}", kind="approval")
        approved = await self.control.wait_for_approval()
        if approved:
            await self._set_task(
                status=TaskStatus.running.value,
                attention={},
                current_action=f"Approved: {name}",
                paused_at=None,
            )
            await self._log(f"Approval granted for {name}", event_type="task.approval", approved=True)
            return True
        await self._log(
            f"Approval rejected for {name}", level="warning", event_type="task.approval", approved=False
        )
        return False

    def _agent_runner(self, scene: Scene | None, spec: SceneSpec | None):
        async def run(goal: str, inputs: dict[str, Any] | None) -> dict[str, Any]:
            assert self.host is not None and self.project_dir is not None and self.task is not None
            run_id = await self._create_agent_run(goal, scene)
            tool_ctx = ToolContext(
                driver=self.host.driver,
                control=self.control,
                project_dir=self.project_dir,
                downloads_dir=self.project_dir / "generated" / ".incoming",
                log=self._adapter_log(scene),
                screenshot=lambda stage: self._screenshot(stage, scene),
                status_update=lambda message: self._task_action(message),
                allowed_files=list(spec.reference_files) if spec else [],
                approval_mode=str((self.task.config or {}).get("approval_mode", "confirm_writes")),
                approval_handler=self._request_tool_approval,
            )
            agent = BrowserAgent(
                tool_ctx,
                llm=self.llm,
                max_steps=self.settings.agent_max_steps,
                use_vision=self.settings.agent_use_vision,
                on_step=lambda step: self._record_agent_step(run_id, step),
            )
            try:
                result = await agent.run(goal, inputs)
            except Exception as exc:
                await self._finish_agent_run(run_id, "failed", str(exc)[:500], 0)
                raise
            await self._finish_agent_run(run_id, result.status, result.message, len(result.steps))
            if not result.ok:
                raise AutomationError(
                    f"Agent could not complete the step: {result.message}",
                    retryable=result.status != "failed" or bool(result.output.get("retryable", True)),
                )
            return result.output

        return run

    async def _run_instruction_task(self) -> None:
        assert self.task is not None and self.host is not None and self.project_dir is not None
        goal = self.task.instructions.strip()
        session_url = ""
        async with session_scope() as db:
            if self.task.browser_session_id:
                session = await db.get(BrowserSession, self.task.browser_session_id)
                session_url = session.start_url if session else ""
        inputs: dict[str, Any] = {"url": session_url, "platform": self.task.platform_key}
        async with session_scope() as db:
            memory = await memory_prompt_lines(db, self.task.organization_id)
        if memory:
            inputs["memory"] = memory
        if (
            self.adapter
            and self.adapter.start_url()
            and (await self.host.driver.url()) in ("", "about:blank")
        ):
            with contextlib.suppress(Exception):
                await self.host.driver.goto(self.adapter.start_url())
        attempt = 1
        max_retries = int((self.task.config or {}).get("max_retries", self.settings.max_retries_default))
        while True:
            try:
                output = await self._agent_runner(None, None)(goal, inputs)
                (self.project_dir / "logs" / "result.json").write_text(
                    json.dumps(output, indent=2, default=str), encoding="utf-8"
                )
                self.task.result = {"output": output}
                return
            except (LoginRequired, HumanInterventionRequired) as exc:
                pseudo = Scene(
                    id=uuid.uuid4(),
                    project_id=uuid.uuid4(),
                    organization_id=self.task.organization_id,
                    index=0,
                    code="task",
                    status="draft",
                )
                decision = await self._wait_for_human(
                    pseudo, str(exc), kind="verification", persist_scene=False
                )
                if decision == "skip":
                    raise RuntimeError("Task skipped by user") from exc
            except SceneRetryRequested:
                attempt = 1
                continue
            except SceneSkipped as exc:
                raise RuntimeError("Task skipped by user") from exc
            except AutomationError as exc:
                if attempt <= max_retries and exc.retryable:
                    attempt += 1
                    await self.control.sleep(5)
                    continue
                pseudo = Scene(
                    id=uuid.uuid4(),
                    project_id=uuid.uuid4(),
                    organization_id=self.task.organization_id,
                    index=0,
                    code="task",
                    status="draft",
                )
                decision = await self._needs_attention(
                    pseudo, str(exc), retryable=exc.retryable, persist_scene=False
                )
                if decision == "skip":
                    raise RuntimeError(f"Task abandoned: {exc}") from exc
                attempt = 1

    async def _create_agent_run(self, goal: str, scene: Scene | None) -> uuid.UUID:
        assert self.task is not None
        async with session_scope() as db:
            run = AgentRun(
                organization_id=self.task.organization_id,
                task_id=self.task_id,
                scene_id=scene.id if scene else None,
                browser_session_id=self.task.browser_session_id,
                goal=goal[:4000],
                mode="llm" if self.llm else "heuristic",
                model=getattr(self.llm, "model", "") or "rule-based",
            )
            db.add(run)
            await db.flush()
            return run.id

    async def _record_agent_step(self, run_id: uuid.UUID, step: AgentStep) -> None:
        assert self.task is not None
        async with session_scope() as db:
            db.add(
                AgentAction(
                    agent_run_id=run_id,
                    task_id=self.task_id,
                    index=step.index,
                    tool=step.tool,
                    arguments=sanitize_action_args(step.args),
                    reasoning=step.reasoning,
                    result=redact(step.result.to_dict()),
                    success=step.result.ok,
                    duration_ms=step.duration_ms,
                    error="" if step.result.ok else step.result.summary[:500],
                )
            )
            await db.execute(update(AgentRun).where(AgentRun.id == run_id).values(steps_count=step.index))
        await self._log(
            f"{step.reasoning or step.tool}",
            event_type="agent.action",
            tool=step.tool,
            args=step.args,
            ok=step.result.ok,
            result=step.result.summary[:200],
            duration_ms=step.duration_ms,
        )
        if self.host:
            await self.host.set_action(step.reasoning or step.tool, kind="agent", detail={"tool": step.tool})

    async def _finish_agent_run(self, run_id: uuid.UUID, status: str, summary: str, steps: int) -> None:
        async with session_scope() as db:
            await db.execute(
                update(AgentRun)
                .where(AgentRun.id == run_id)
                .values(status=status, summary=summary[:2000], finished_at=_now(), steps_count=steps)
            )

    # ================================================================== finishing
    async def _finish(self) -> None:
        assert self.task is not None
        if self.task.kind == "video_generation":
            failed = self.scene_stats["failed"]
            skipped = self.scene_stats["skipped"]
            completed = self.scene_stats["completed"]
            summary = (
                f"{completed} scene(s) completed"
                + (f", {skipped} skipped" if skipped else "")
                + (f", {failed} failed" if failed else "")
            )
            status = TaskStatus.completed.value if failed == 0 else TaskStatus.failed.value
        else:
            summary = "Task completed"
            status = TaskStatus.completed.value
        await self._write_task_log(status, summary)
        await self._screenshot("task_completion")
        await self._set_task(
            status=status,
            finished_at=_now(),
            current_action="",
            control=TaskControl.none.value,
            worker_id=None,
            attention={},
            result={**(self.task.result or {}), "summary": summary, **self.scene_stats},
        )
        await self._log(
            f"Task finished: {summary}",
            event_type="task.completed" if status == TaskStatus.completed.value else "task.failed",
            status=status,
        )
        await notify(
            self.task.organization_id,
            "task_completed" if status == TaskStatus.completed.value else "task_failed",
            f"Task {'completed' if status == TaskStatus.completed.value else 'finished with failures'}",
            f"{self.task.title}: {summary}",
            task_id=self.task_id,
            severity="success" if status == TaskStatus.completed.value else "error",
        )

    async def _fail(self, error: str) -> None:
        if self.task is None:
            return
        await self._write_task_log("failed", error)
        await self._set_task(
            status=TaskStatus.failed.value,
            finished_at=_now(),
            error=error[:2000],
            current_action="",
            control=TaskControl.none.value,
            worker_id=None,
        )
        await self._log(
            f"Task failed: {error[:300]}", level="error", event_type="task.failed", status="failed"
        )
        await notify(
            self.task.organization_id,
            "task_failed",
            "Task failed",
            f"{self.task.title}: {error[:200]}",
            task_id=self.task_id,
            severity="error",
        )

    async def _cancelled(self, reason: str) -> None:
        if self.task is None:
            return
        await self._write_task_log("cancelled", reason)
        await self._set_task(
            status=TaskStatus.cancelled.value,
            finished_at=_now(),
            error=reason[:500],
            current_action="",
            control=TaskControl.none.value,
            worker_id=None,
            attention={},
            human_control=False,
        )
        if self.project:
            async with session_scope() as db:
                await db.execute(
                    update(Scene)
                    .where(
                        Scene.project_id == self.project.id,
                        Scene.status.in_(
                            [
                                SceneStatus.queued.value,
                                SceneStatus.generating.value,
                                SceneStatus.processing.value,
                                SceneStatus.downloading.value,
                                SceneStatus.waiting_for_human.value,
                            ]
                        ),
                    )
                    .values(status=SceneStatus.draft.value)
                )
        await self._log(
            f"Task cancelled: {reason}", level="warning", event_type="task.cancelled", status="cancelled"
        )

    async def _write_task_log(self, status: str, summary: str) -> None:
        if self.project_dir is None or self.task is None:
            return
        try:
            async with session_scope() as db:
                from app.models import ActivityLog

                rows = (
                    (
                        await db.execute(
                            select(ActivityLog)
                            .where(ActivityLog.task_id == self.task_id)
                            .order_by(ActivityLog.created_at)
                        )
                    )
                    .scalars()
                    .all()
                )
                lines = [f"{r.created_at.isoformat()} [{r.level}] {r.event_type}: {r.message}" for r in rows]
            log_path = self.project_dir / "logs" / f"task_{self.task_id}.log"
            log_path.write_text("\n".join(lines) + f"\n--- {status}: {summary}\n", encoding="utf-8")
        except Exception as exc:
            logger.debug("could not write task log: %s", exc)

    # ================================================================== utilities
    async def _set_task(self, **values: Any) -> None:
        assert self.task is not None
        async with session_scope() as db:
            await db.execute(update(Task).where(Task.id == self.task_id).values(**values))
        for k, v in values.items():
            setattr(self.task, k, v)
        await publish_event(
            "task.updated",
            self.task.organization_id,
            {k: (str(v) if isinstance(v, (uuid.UUID, datetime)) else v) for k, v in values.items()},
            task_id=self.task_id,
            project_id=self.task.project_id,
        )

    async def _task_action(self, message: str, **extra: Any) -> None:
        assert self.task is not None
        self.task.current_action = message[:300]
        async with session_scope() as db:
            await db.execute(update(Task).where(Task.id == self.task_id).values(current_action=message[:300]))
        if self.host:
            await self.host.set_action(message, detail=extra)
        await publish_event(
            "task.progress",
            self.task.organization_id,
            {"current_action": message, **extra, **(self.task.progress or {})},
            task_id=self.task_id,
            project_id=self.task.project_id,
        )

    async def _update_progress(
        self,
        *,
        total: int,
        current: Scene | None,
        completed: int,
        failed: int,
        skipped: int,
        final: bool = False,
    ) -> None:
        assert self.task is not None
        done = completed + failed + skipped
        progress = {
            "total": total,
            "completed": completed,
            "failed": failed,
            "skipped": skipped,
            "done": done,
            "percent": int(100 * done / total) if total else (100 if final else 0),
            "current_scene_id": str(current.id) if current else None,
            "current_scene_code": current.code if current else None,
            "current_scene_index": current.index if current else None,
        }
        self.task.progress = progress
        async with session_scope() as db:
            await db.execute(update(Task).where(Task.id == self.task_id).values(progress=progress))
        await publish_event(
            "task.progress",
            self.task.organization_id,
            {**progress, "current_action": self.task.current_action},
            task_id=self.task_id,
            project_id=self.task.project_id,
        )

    async def _set_scene(self, scene: Scene, **values: Any) -> None:
        assert self.task is not None
        async with session_scope() as db:
            await db.execute(update(Scene).where(Scene.id == scene.id).values(**values))
        for k, v in values.items():
            setattr(scene, k, v)
        await publish_event(
            "scene.updated",
            self.task.organization_id,
            {k: (str(v) if isinstance(v, (uuid.UUID, datetime)) else v) for k, v in values.items()},
            task_id=self.task_id,
            scene_id=scene.id,
            project_id=self.task.project_id,
        )

    async def _refresh_scene(self, scene: Scene) -> Scene:
        async with session_scope() as db:
            fresh = await db.get(Scene, scene.id)
            if fresh is None:
                raise SceneSkipped("scene was deleted")
            db.expunge(fresh)
            return fresh

    async def _start_step(self, name: str, scene: Scene | None, attempt: int) -> uuid.UUID:
        self.step_index += 1
        async with session_scope() as db:
            step = TaskStep(
                task_id=self.task_id,
                scene_id=scene.id if scene else None,
                index=self.step_index,
                name=name,
                status="running",
                attempt=attempt,
                started_at=_now(),
            )
            db.add(step)
            await db.flush()
            return step.id

    async def _finish_step(
        self, step_id: uuid.UUID, status: str, *, result: dict[str, Any] | None = None, error: str = ""
    ) -> None:
        async with session_scope() as db:
            step = await db.get(TaskStep, step_id)
            if step:
                step.status = status
                step.finished_at = _now()
                if step.started_at:
                    started = (
                        step.started_at if step.started_at.tzinfo else step.started_at.replace(tzinfo=UTC)
                    )
                    step.duration_ms = int((step.finished_at - started).total_seconds() * 1000)
                step.result = result or {}
                step.error = error

    def _adapter_log(self, scene: Scene | None):
        async def log(
            message: str, level: str = "info", event_type: str = "agent.thought", **data: Any
        ) -> None:
            await self._log(
                message, level=level, event_type=event_type, scene_id=scene.id if scene else None, **data
            )

        return log

    async def _log(
        self,
        message: str,
        *,
        level: str = "info",
        event_type: str = "agent.action",
        scene_id: Any = None,
        duration_ms: int | None = None,
        **data: Any,
    ) -> None:
        assert self.task is not None
        try:
            async with session_scope() as db:
                await emit(
                    db,
                    event_type,
                    self.task.organization_id,
                    message,
                    payload=data,
                    level=level,
                    task_id=self.task_id,
                    project_id=self.task.project_id,
                    session_id=self.task.browser_session_id,
                    scene_id=scene_id,
                    duration_ms=duration_ms,
                )
        except Exception as exc:
            logger.warning("activity log failed: %s", exc)
        getattr(logger, "warning" if level in ("warning", "error") else "info")(
            message, extra={"task_id": str(self.task_id), "action": event_type, "duration_ms": duration_ms}
        )

    async def _screenshot(self, stage: str, scene: Scene | None = None) -> None:
        if self.host is None or self.task is None:
            return
        try:
            data = await self.host.driver.screenshot(quality=55)
        except Exception as exc:
            logger.debug("screenshot failed (%s): %s", stage, exc)
            return
        folder = self.settings.screenshots_dir / str(self.task_id)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{int(time.time() * 1000)}_{safe_filename(stage)}.jpg"
        path.write_bytes(data)
        url, title = "", ""
        with contextlib.suppress(Exception):
            url = sanitize_url(await self.host.driver.url())
            title = await self.host.driver.title()
        async with session_scope() as db:
            shot = Screenshot(
                organization_id=self.task.organization_id,
                task_id=self.task_id,
                browser_session_id=self.task.browser_session_id,
                scene_id=scene.id if scene else None,
                stage=stage,
                path=str(path),
                url=url[:2000],
                title=title[:500],
                size_bytes=len(data),
            )
            db.add(shot)
            await db.flush()
            shot_id = shot.id
        await publish_event(
            "browser.screenshot",
            self.task.organization_id,
            {"screenshot_id": str(shot_id), "stage": stage, "url": url},
            task_id=self.task_id,
            scene_id=scene.id if scene else None,
        )

    async def _relaunch_browser(self) -> None:
        if self.host is None or self.task is None:
            return
        session_id = self.host.session_id
        await self.worker.close_host(session_id, reason="crash recovery")
        self.host = await self.worker.get_or_start_host(session_id, self.task.organization_id)
        self.host.acquire(self.task_id)


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError):
        return False
