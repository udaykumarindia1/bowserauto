"""Platform adapter base classes.

An adapter encodes everything site-specific about one web platform: URLs, selectors, the
generation workflow, how to detect progress/completion/errors, how to download the output
and how to recognise login walls or human verification challenges. The executor drives the
*same* step sequence for every platform::

    ensure_ready → open_generation_tool → upload_references → enter_prompt → configure
    → start_generation → wait_for_completion → download_result

Selectors are declared as target specs (see ``app.browser.driver``) with several
candidates each, so small UI changes rarely break a step. When a step cannot be completed
with selectors the base implementation can hand the step over to the LLM-powered generic
agent (``AdapterContext.run_agent``) if one is available.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from app.browser.driver import BrowserDriver, DownloadResult, TargetSpec
from app.core.errors import (
    AutomationError,
    DownloadFailed,
    ElementNotFound,
    GenerationFailed,
    GenerationTimeout,
    HumanInterventionRequired,
    LoginRequired,
)
from app.models import GenerationState
from app.queue.control import ControlToken

logger = logging.getLogger(__name__)

LogFn = Callable[..., Awaitable[None]]
ScreenshotFn = Callable[[str], Awaitable[None]]


@dataclass
class SceneSpec:
    """Platform-neutral description of what to generate."""

    index: int
    code: str
    prompt: str
    negative_prompt: str = ""
    duration_seconds: float = 8.0
    aspect_ratio: str = "16:9"
    resolution: str = "1080p"
    reference_files: list[Path] = field(default_factory=list)
    title: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class GenerationOutcome:
    download: DownloadResult
    output_kind: str = "video"
    generation_seconds: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class AdapterContext:
    driver: BrowserDriver
    control: ControlToken
    scene: SceneSpec
    log: LogFn
    screenshot: ScreenshotFn
    generation_timeout_seconds: int = 900
    action_timeout_ms: int = 30_000
    options: dict[str, Any] = field(default_factory=dict)
    run_agent: Callable[[str, dict[str, Any] | None], Awaitable[dict[str, Any]]] | None = (
        None  # LLM generic agent for a sub-goal
    )
    state_callback: Callable[[GenerationState, str], Awaitable[None]] | None = None
    attempt: int = 1

    async def set_state(self, state: GenerationState, detail: str = "") -> None:
        if self.state_callback:
            await self.state_callback(state, detail)

    async def say(self, message: str, **data: Any) -> None:
        """Concise, user-facing reasoning line (no private chain-of-thought)."""
        await self.log(message, level="info", event_type="agent.thought", **data)


@dataclass
class RetryPolicy:
    max_attempts: int = 3
    backoff_seconds: float = 5.0
    backoff_multiplier: float = 2.0
    max_backoff_seconds: float = 120.0

    def delay_for(self, attempt: int) -> float:
        return min(
            self.max_backoff_seconds, self.backoff_seconds * (self.backoff_multiplier ** max(0, attempt - 1))
        )


class PlatformAdapter:
    """Base adapter with a generic selector-driven workflow."""

    key: ClassVar[str] = "base"
    name: ClassVar[str] = "Base platform"
    category: ClassVar[str] = "video"  # video | image | chat | generic
    base_url: ClassVar[str] = ""
    generation_url: ClassVar[str] = ""
    description: ClassVar[str] = ""
    requires_login: ClassVar[bool] = True
    output_kind: ClassVar[str] = "video"  # video | image | text
    output_extension: ClassVar[str] = ".mp4"
    notes: ClassVar[str] = ""
    verified: ClassVar[bool] = False  # selectors validated against the live site recently
    supported_actions: ClassVar[list[str]] = ["generate", "upload_reference", "download", "detect_state"]
    capabilities: ClassVar[dict[str, Any]] = {
        "max_clip_seconds": 8,
        "aspect_ratios": ["16:9", "9:16", "1:1"],
        "reference_images": True,
        "negative_prompt": False,
    }

    # Target specs; each entry may be a single spec or {"any": [...]}
    selectors: ClassVar[dict[str, TargetSpec]] = {}
    # Regex patterns (case-insensitive) matched against the visible page text.
    status_patterns: ClassVar[dict[str, list[str]]] = {
        GenerationState.QUEUED.value: [r"\bqueued\b", r"in queue", r"waiting in line"],
        GenerationState.GENERATING.value: [
            r"\bgenerating\b",
            r"creating your video",
            r"rendering",
            r"\b\d{1,3}%",
        ],
        GenerationState.PROCESSING.value: [r"\bprocessing\b", r"finalizing", r"finalising", r"upscaling"],
        GenerationState.COMPLETED.value: [
            r"generation complete",
            r"video is ready",
            r"\bcompleted\b",
            r"\bready to download",
        ],
    }
    error_patterns: ClassVar[list[str]] = [
        r"generation failed",
        r"something went wrong",
        r"an error occurred",
        r"try again later",
        r"content policy",
        r"violat(?:es|ion)",
        r"insufficient credits",
        r"out of credits",
        r"quota exceeded",
        r"rate limit",
    ]
    non_retryable_error_patterns: ClassVar[list[str]] = [
        r"content policy",
        r"violat(?:es|ion)",
        r"insufficient credits",
        r"out of credits",
        r"quota exceeded",
        r"upgrade your plan",
    ]
    human_check_patterns: ClassVar[list[str]] = [
        r"verify (?:that )?you(?:'re| are) (?:a )?human",
        r"\bcaptcha\b",
        r"i'm not a robot",
        r"unusual traffic",
        r"security check",
        r"two[- ]factor",
        r"verification code",
        r"enter the code",
        r"confirm it'?s you",
        r"accept (?:all )?cookies",
    ]
    login_patterns: ClassVar[list[str]] = [
        r"\bsign in\b",
        r"\blog in\b",
        r"\blogin\b",
        r"continue with google",
        r"create (?:an )?account",
    ]
    login_url_patterns: ClassVar[list[str]] = [
        r"/login",
        r"/signin",
        r"/sign-in",
        r"accounts\.google\.com",
        r"/auth",
    ]
    retry_policy: ClassVar[RetryPolicy] = RetryPolicy()
    poll_interval_seconds: ClassVar[float] = 2.0
    stall_timeout_seconds: ClassVar[int] = 240  # how long UNKNOWN state is tolerated before probing

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}

    # ------------------------------------------------------------------ metadata
    @classmethod
    def info(cls) -> dict[str, Any]:
        return {
            "key": cls.key,
            "name": cls.name,
            "category": cls.category,
            "base_url": cls.base_url,
            "description": cls.description,
            "requires_login": cls.requires_login,
            "output_kind": cls.output_kind,
            "supported_actions": list(cls.supported_actions),
            "capabilities": dict(cls.capabilities),
            "verified": cls.verified,
            "notes": cls.notes,
        }

    def selector(self, name: str) -> TargetSpec | None:
        override = (self.config.get("selectors") or {}).get(name)
        return override or self.selectors.get(name)

    def start_url(self) -> str:
        return (
            self.config.get("generation_url")
            or self.generation_url
            or self.config.get("base_url")
            or self.base_url
        )

    # ------------------------------------------------------------- full workflow
    async def generate(self, ctx: AdapterContext, target_dir: Path) -> GenerationOutcome:
        started = time.monotonic()
        await ctx.set_state(GenerationState.IDLE, "preparing")
        await self.ensure_ready(ctx)
        await ctx.control.checkpoint()
        await self.open_generation_tool(ctx)
        await ctx.control.checkpoint()
        if ctx.scene.reference_files:
            await self.upload_references(ctx)
            await ctx.control.checkpoint()
        await self.enter_prompt(ctx)
        await ctx.control.checkpoint()
        await self.configure(ctx)
        await ctx.control.checkpoint()
        await ctx.screenshot("before_generation")
        await self.start_generation(ctx)
        gen_started = time.monotonic()
        await ctx.set_state(GenerationState.GENERATING, "generation started")
        await self.wait_for_completion(ctx)
        generation_seconds = time.monotonic() - gen_started
        await ctx.screenshot("after_generation")
        await ctx.set_state(GenerationState.DOWNLOAD_READY, "output ready")
        download = await self.download_result(ctx, target_dir)
        await ctx.set_state(GenerationState.SUCCESS, "downloaded")
        return GenerationOutcome(
            download=download,
            output_kind=self.output_kind,
            generation_seconds=generation_seconds,
            details={"total_seconds": time.monotonic() - started},
        )

    # ------------------------------------------------------------ workflow steps
    async def ensure_ready(self, ctx: AdapterContext) -> None:
        url = self.start_url()
        current = await ctx.driver.url()
        if url and not current.startswith(url.split("?")[0].rstrip("/")):
            await ctx.say(f"Opening {self.name}")
            await ctx.driver.goto(url)
            await ctx.driver.wait_for_load(state="domcontentloaded")
        await asyncio.sleep(0.5)
        await self.check_blockers(ctx)

    async def check_blockers(self, ctx: AdapterContext) -> None:
        text = await ctx.driver.page_text(15_000)
        url = await ctx.driver.url()
        if await self.detect_human_check(ctx, text):
            raise HumanInterventionRequired(
                "The site is showing a verification challenge (CAPTCHA/MFA/consent). Please complete it manually."
            )
        if await self.detect_login_required(ctx, text, url):
            raise LoginRequired(
                f"{self.name} requires you to log in. Open the browser session, sign in, then resume the task."
            )

    async def detect_human_check(self, ctx: AdapterContext, text: str | None = None) -> bool:
        text = text if text is not None else await ctx.driver.page_text(15_000)
        return _matches_any(text, self.human_check_patterns) or await _exists(
            ctx, self.selector("human_check")
        )

    async def detect_login_required(
        self, ctx: AdapterContext, text: str | None = None, url: str | None = None
    ) -> bool:
        if not self.requires_login:
            return False
        text = text if text is not None else await ctx.driver.page_text(15_000)
        url = url if url is not None else await ctx.driver.url()
        if _matches_any(url, self.login_url_patterns):
            return True
        if await _exists(ctx, self.selector("login_indicator")):
            return True
        if await _exists(ctx, self.selector("logged_in_indicator")):
            return False
        if await _exists(ctx, self.selector("prompt_input")):
            return False
        # Login walls usually have very little text and a sign-in control.
        return _matches_any(text[:3000], self.login_patterns) and len(text) < 2500

    async def open_generation_tool(self, ctx: AdapterContext) -> None:
        spec = self.selector("open_tool")
        if spec and not await _exists(ctx, self.selector("prompt_input")):
            await ctx.say("Opening the generation tool")
            await ctx.driver.click(spec, timeout_ms=ctx.action_timeout_ms)
            await ctx.driver.wait_for(self.selector("prompt_input") or {"css": "textarea"}, timeout_ms=15_000)

    async def upload_references(self, ctx: AdapterContext) -> None:
        spec = self.selector("upload")
        if not self.capabilities.get("reference_images", True):
            await ctx.say("Platform does not support reference images; skipping upload")
            return
        await ctx.say(f"Uploading {len(ctx.scene.reference_files)} reference file(s)")
        try:
            await ctx.driver.upload(spec, ctx.scene.reference_files)
        except ElementNotFound:
            if ctx.run_agent:
                await ctx.run_agent(
                    "Upload the reference image(s) using the page's upload control.",
                    {"files": [str(f) for f in ctx.scene.reference_files]},
                )
            else:
                raise
        await asyncio.sleep(1.0)

    async def enter_prompt(self, ctx: AdapterContext) -> None:
        spec = self.selector("prompt_input") or {"any": [{"css": "textarea"}, {"role": "textbox"}]}
        await ctx.say("Entering scene prompt")
        try:
            await ctx.driver.type(spec, ctx.scene.prompt)
        except ElementNotFound:
            if ctx.run_agent:
                await ctx.run_agent(
                    "Find the prompt text box and enter the prompt exactly as given.",
                    {"prompt": ctx.scene.prompt},
                )
            else:
                raise
        neg = self.selector("negative_prompt_input")
        if neg and ctx.scene.negative_prompt and self.capabilities.get("negative_prompt"):
            if await _exists(ctx, neg):
                await ctx.driver.type(neg, ctx.scene.negative_prompt)

    async def configure(self, ctx: AdapterContext) -> None:
        """Apply duration/aspect ratio settings when the platform exposes them."""
        duration_spec = self.selector("duration_option")
        if duration_spec and isinstance(duration_spec, dict) and "{value}" in str(duration_spec):
            await self._select_templated(ctx, duration_spec, f"{int(ctx.scene.duration_seconds)}")
        ratio_spec = self.selector("aspect_ratio_option")
        if ratio_spec and isinstance(ratio_spec, dict) and "{value}" in str(ratio_spec):
            await self._select_templated(ctx, ratio_spec, ctx.scene.aspect_ratio)

    async def _select_templated(self, ctx: AdapterContext, spec: dict[str, Any], value: str) -> None:
        rendered = _render_spec(spec, value)
        opener = self.selector("settings_menu")
        try:
            if opener and not await _exists(ctx, rendered):
                await ctx.driver.click(opener, timeout_ms=5000)
            if await _exists(ctx, rendered, timeout_ms=2000):
                await ctx.driver.click(rendered, timeout_ms=5000)
        except ElementNotFound:
            await ctx.say(f"Could not apply setting '{value}'; continuing with platform defaults")

    async def start_generation(self, ctx: AdapterContext) -> None:
        spec = self.selector("generate_button") or {
            "any": [{"role": "button", "name": "/generate|create|run|submit/i"}]
        }
        await ctx.say("Generation button detected, starting generation")
        try:
            await ctx.driver.click(spec, timeout_ms=ctx.action_timeout_ms)
        except ElementNotFound:
            if ctx.run_agent:
                await ctx.run_agent("Click the button that starts the video generation.", None)
            else:
                raise
        await asyncio.sleep(1.5)
        await self.check_blockers(ctx)

    async def detect_state(self, ctx: AdapterContext) -> tuple[GenerationState, str]:
        text = await ctx.driver.page_text(20_000)
        if _matches_any(text, self.human_check_patterns):
            return GenerationState.WAITING_FOR_HUMAN, "verification challenge detected"
        error_spec = self.selector("error_indicator")
        if await _exists(ctx, error_spec) or _matches_any(text, self.error_patterns):
            detail = _first_match(text, self.error_patterns) or "error indicator visible"
            return GenerationState.FAILED, detail
        if await _exists(ctx, self.selector("download_button")):
            return GenerationState.DOWNLOAD_READY, "download control visible"
        if await _exists(ctx, self.selector("result_video")):
            return GenerationState.COMPLETED, "result video visible"
        for state_name in (
            GenerationState.COMPLETED.value,
            GenerationState.PROCESSING.value,
            GenerationState.GENERATING.value,
            GenerationState.QUEUED.value,
        ):
            match = _first_match(text, self.status_patterns.get(state_name, []))
            if match:
                return GenerationState(state_name), match
        return GenerationState.UNKNOWN, ""

    async def wait_for_completion(self, ctx: AdapterContext) -> None:
        """Smart wait: poll real page state instead of sleeping blindly."""
        deadline = time.monotonic() + ctx.generation_timeout_seconds
        last_state: GenerationState | None = None
        unknown_since: float | None = None
        interval = self.poll_interval_seconds
        last_progress_log = 0.0
        while True:
            await ctx.control.checkpoint()
            if time.monotonic() > deadline:
                raise GenerationTimeout(
                    f"Generation did not finish within {ctx.generation_timeout_seconds} seconds"
                )
            try:
                state, detail = await self.detect_state(ctx)
            except AutomationError:
                raise
            except Exception as exc:  # transient DOM errors during re-render
                logger.debug("detect_state error: %s", exc)
                state, detail = GenerationState.UNKNOWN, str(exc)[:100]
            if state != last_state:
                await ctx.set_state(state, detail)
                if state in (GenerationState.QUEUED, GenerationState.GENERATING, GenerationState.PROCESSING):
                    await ctx.say(
                        {
                            "QUEUED": "Request is queued on the platform",
                            "GENERATING": "Video is being generated",
                            "PROCESSING": "Video is still processing",
                        }[state.value]
                    )
                last_state = state
            if state in (GenerationState.COMPLETED, GenerationState.DOWNLOAD_READY, GenerationState.SUCCESS):
                await ctx.say("Generation completed")
                return
            if state == GenerationState.FAILED:
                retryable = not _matches_any(detail, self.non_retryable_error_patterns)
                raise GenerationFailed(f"The platform reported a failure: {detail}", retryable=retryable)
            if state == GenerationState.WAITING_FOR_HUMAN:
                raise HumanInterventionRequired(
                    "The platform is asking for human verification. Take over the browser to complete it."
                )
            if state == GenerationState.BLOCKED:
                raise GenerationFailed(f"Generation blocked: {detail}", retryable=False)
            if state == GenerationState.UNKNOWN:
                unknown_since = unknown_since or time.monotonic()
                if time.monotonic() - unknown_since > self.stall_timeout_seconds:
                    await ctx.say("No progress signal detected for a while; re-inspecting the page")
                    await self.on_stall(ctx)
                    unknown_since = time.monotonic()
            else:
                unknown_since = None
            now = time.monotonic()
            if now - last_progress_log > 30:
                last_progress_log = now
                await ctx.log(
                    f"Waiting for generation ({state.value.lower()})",
                    level="debug",
                    event_type="task.progress",
                    state=state.value,
                )
            await ctx.control.sleep(interval)
            interval = min(interval * 1.2, 6.0)

    async def on_stall(self, ctx: AdapterContext) -> None:
        await ctx.driver.scroll("down", 400)
        await asyncio.sleep(0.5)
        await ctx.driver.scroll("up", 400)

    async def download_result(self, ctx: AdapterContext, target_dir: Path) -> DownloadResult:
        await ctx.set_state(GenerationState.DOWNLOADING, "downloading")
        spec = self.selector("download_button")
        await ctx.say("Download button detected, downloading output")
        last_error: Exception | None = None
        if spec and await _exists(ctx, spec, timeout_ms=5000):
            try:
                return await ctx.driver.download(spec, timeout_ms=180_000)
            except (DownloadFailed, ElementNotFound) as exc:
                last_error = exc
        # Fallback: grab the media element source and fetch it with the browser's cookies.
        result = await self.download_via_media_src(ctx, target_dir)
        if result is not None:
            return result
        if ctx.run_agent:
            info = await ctx.run_agent(
                "Download the generated video using the page's download control.", None
            )
            path = info.get("download_path")
            if path and Path(path).exists():
                return DownloadResult(
                    path=Path(path), suggested_filename=Path(path).name, size_bytes=Path(path).stat().st_size
                )
        raise DownloadFailed(
            f"Could not download the generated output ({last_error or 'no download control found'})"
        )

    async def download_via_media_src(self, ctx: AdapterContext, target_dir: Path) -> DownloadResult | None:
        fetch = getattr(ctx.driver, "fetch_to_file", None)
        if fetch is None:
            return None
        try:
            src = await ctx.driver.evaluate(
                "(() => { const v = document.querySelector('video'); if (!v) return null; return v.currentSrc || v.src || (v.querySelector('source') && v.querySelector('source').src) || null; })()"
            )
        except Exception:
            src = None
        if not src or str(src).startswith("blob:"):
            return None
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"download_{int(time.time())}{self.output_extension}"
        try:
            size = await fetch(str(src), path)
        except Exception as exc:
            logger.info("media src download failed: %s", exc)
            return None
        return DownloadResult(path=path, suggested_filename=path.name, url=str(src), size_bytes=size)

    async def login_hint(self) -> str:
        return f"Open the '{self.name}' browser session, sign in manually (including any 2FA), then return control."


# ----------------------------------------------------------------------------- helpers
def _matches_any(text: str, patterns: list[str]) -> bool:
    return _first_match(text, patterns) is not None


def _first_match(text: str, patterns: list[str]) -> str | None:
    if not text:
        return None
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            return m.group(0)
    return None


async def _exists(ctx: AdapterContext, spec: TargetSpec | None, timeout_ms: int = 0) -> bool:
    if spec is None:
        return False
    try:
        return await ctx.driver.exists(spec, timeout_ms=timeout_ms)
    except Exception:
        return False


def _render_spec(spec: Any, value: str) -> Any:
    if isinstance(spec, str):
        return spec.replace("{value}", value)
    if isinstance(spec, dict):
        return {k: _render_spec(v, value) for k, v in spec.items()}
    if isinstance(spec, list):
        return [_render_spec(v, value) for v in spec]
    return spec
