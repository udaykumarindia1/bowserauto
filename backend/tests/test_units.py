"""Unit tests: scene parsing, state detection, retry policy, file naming/verification, security."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from app.browser.fake_driver import FakeDriver, FakeVideoSite
from app.core.security import (
    decrypt_secret,
    encrypt_secret,
    hash_password,
    sign_payload,
    verify_password,
    verify_signed_payload,
)
from app.models import GenerationState
from app.platforms.base import AdapterContext, GenerationTimeout, RetryPolicy, SceneSpec
from app.platforms.mock import MockPlatformAdapter as MockVideoAdapter
from app.queue.control import ControlToken
from app.services.scene_engine import AnalysisContext, HeuristicSceneAnalyzer, analyze_script
from app.storage.files import inspect_media, inspect_mp4, sniff_kind
from app.storage.paths import format_scene_filename, resolve_inside, safe_filename, unique_path

SAMPLE_MP4 = Path(__file__).resolve().parents[1] / "app" / "mock_platform" / "static" / "sample.mp4"


# ------------------------------------------------------------------------- scene parsing
def test_heuristic_parser_honours_explicit_scene_markers() -> None:
    script = """Scene 1: Dawn over the harbour. Mira walks along the pier.
Scene 2: Interior, lighthouse. Mira finds an old map.
Scene 3: Night. A storm rolls in over the sea."""
    ctx = AnalysisContext(visual_style="watercolor", characters=[{"name": "Mira", "description": "young sailor"}])
    result = HeuristicSceneAnalyzer().analyze(script, ctx)
    assert result.method == "heuristic"
    assert len(result.scenes) == 3
    first = result.scenes[0]
    assert "Mira" in first.characters
    assert "watercolor" in first.prompt.lower()
    assert first.negative_prompt  # default negative prompt applied
    assert all(s.title for s in result.scenes)
    assert all(1 <= s.duration_seconds <= 600 for s in result.scenes)


def test_heuristic_parser_splits_free_text_and_respects_limits() -> None:
    paragraphs = "\n\n".join(f"A long paragraph number {i} describing a new location and a new action happening." for i in range(12))
    result = HeuristicSceneAnalyzer().analyze(paragraphs, AnalysisContext(max_scenes=5))
    assert len(result.scenes) == 5
    assert any("truncated" in w for w in result.warnings)
    empty = HeuristicSceneAnalyzer().analyze("   \n ", AnalysisContext())
    assert empty.scenes == [] and empty.warnings


def test_heuristic_parser_fits_target_duration() -> None:
    script = "Scene 1: Opening shot.\nScene 2: Middle.\nScene 3: Finale."
    short = HeuristicSceneAnalyzer().analyze(script, AnalysisContext(clip_duration_seconds=5, target_duration_seconds=60))
    # Three one-line scenes cannot fill 60s with 5s clips: durations are stretched to the clip
    # length and the user is warned instead of silently inventing content.
    assert all(s.duration_seconds == 5 for s in short.scenes)
    assert any("target" in w for w in short.warnings)
    long_script = "\n".join(f"Scene {i}: " + " ".join(["The camera drifts slowly across the valley while birds circle."] * 6) for i in range(1, 4))
    fitted = HeuristicSceneAnalyzer().analyze(long_script, AnalysisContext(clip_duration_seconds=8, target_duration_seconds=20))
    assert sum(s.duration_seconds for s in fitted.scenes) < sum(s.duration_seconds for s in HeuristicSceneAnalyzer().analyze(long_script, AnalysisContext(clip_duration_seconds=8)).scenes)


async def test_analyze_script_without_llm_uses_heuristics() -> None:
    result = await analyze_script("Scene 1: A kite.\nScene 2: A storm.", AnalysisContext(), None)
    assert result.method == "heuristic" and len(result.scenes) == 2


# ---------------------------------------------------------------------- state detection
def _context(site: FakeVideoSite) -> tuple[AdapterContext, FakeDriver]:
    driver = FakeDriver(site)
    logs: list[str] = []

    async def log(message: str, **_: object) -> None:
        logs.append(message)

    async def screenshot(_stage: str) -> None:
        return None

    ctx = AdapterContext(driver=driver, control=ControlToken(), scene=SceneSpec(index=1, code="scene_001", prompt="a kite"), log=log, screenshot=screenshot, generation_timeout_seconds=5)
    return ctx, driver


async def test_detect_state_follows_real_page_state_not_timers() -> None:
    site = FakeVideoSite(generation_seconds=0.2, queue_seconds=0.2)
    ctx, driver = _context(site)
    adapter = MockVideoAdapter({})
    await driver.goto("http://mock.test/mock-platform/")
    assert (await adapter.detect_state(ctx))[0] in (GenerationState.UNKNOWN, GenerationState.IDLE)
    site.prompt = "a kite"
    site.generate()
    assert (await adapter.detect_state(ctx))[0] == GenerationState.QUEUED
    await asyncio.sleep(0.25)
    assert (await adapter.detect_state(ctx))[0] == GenerationState.GENERATING
    await asyncio.sleep(0.25)
    state, _detail = await adapter.detect_state(ctx)
    assert state in (GenerationState.COMPLETED, GenerationState.DOWNLOAD_READY)


async def test_detect_state_reports_failures_and_human_checks() -> None:
    site = FakeVideoSite(generation_seconds=0.05, fail_first_attempts=1)
    ctx, driver = _context(site)
    adapter = MockVideoAdapter({})
    await driver.goto("http://mock.test/mock-platform/")
    site.prompt = "x"
    site.generate()
    await asyncio.sleep(0.1)
    state, detail = await adapter.detect_state(ctx)
    assert state == GenerationState.FAILED and "fail" in detail.lower()

    checked = FakeVideoSite(human_check_on_attempt=1)
    ctx2, driver2 = _context(checked)
    await driver2.goto("http://mock.test/mock-platform/")
    checked.prompt = "x"
    checked.generate()
    assert checked.human_check_pending is True
    assert (await adapter.detect_state(ctx2))[0] == GenerationState.WAITING_FOR_HUMAN


async def test_wait_for_completion_times_out_instead_of_hanging() -> None:
    site = FakeVideoSite(generation_seconds=60)  # never finishes within the timeout
    ctx, driver = _context(site)
    ctx.generation_timeout_seconds = 1
    adapter = MockVideoAdapter({})
    await driver.goto("http://mock.test/mock-platform/")
    site.prompt = "x"
    site.generate()
    started = time.monotonic()
    with pytest.raises(GenerationTimeout):
        await adapter.wait_for_completion(ctx)
    assert time.monotonic() - started < 5


def test_retry_policy_backoff_is_bounded() -> None:
    policy = RetryPolicy(max_attempts=4, backoff_seconds=2, backoff_multiplier=3, max_backoff_seconds=10)
    assert [policy.delay_for(a) for a in (1, 2, 3, 4)] == [2, 6, 10, 10]


# ---------------------------------------------------------------------------- files
def test_scene_filename_patterns() -> None:
    assert format_scene_filename("", index=1, code="scene_001", project_name="My Film", platform="mock", ext="mp4") == "scene_001.mp4"
    assert format_scene_filename("{project}_{code}{ext}", index=2, code="scene_002", project_name="My Film!", platform="mock", ext=".mp4") == "My_Film_scene_002.mp4"
    # Broken patterns fall back to the default naming instead of raising.
    assert format_scene_filename("{nope}", index=3, code="scene_003", project_name="p", platform="mock", ext="mp4") == "scene_003.mp4"


def test_safe_filename_and_path_traversal_protection(tmp_path: Path) -> None:
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename("my video (final).mp4") == "my_video_final.mp4"
    assert safe_filename("") == "file"
    base = tmp_path / "project"
    base.mkdir()
    assert resolve_inside(base, "generated/scene_001.mp4") == (base / "generated/scene_001.mp4").resolve()
    with pytest.raises(PermissionError):
        resolve_inside(base, "../outside.txt")
    with pytest.raises(PermissionError):
        resolve_inside(base, "/etc/passwd")


def test_unique_path_versions_existing_files(tmp_path: Path) -> None:
    target = tmp_path / "scene_001.mp4"
    assert unique_path(target) == target
    target.write_bytes(b"x")
    assert unique_path(target).name == "scene_001_v2.mp4"
    (tmp_path / "scene_001_v2.mp4").write_bytes(b"x")
    assert unique_path(target).name == "scene_001_v3.mp4"


def test_mp4_verification_rejects_html_and_truncated_files(tmp_path: Path) -> None:
    info = inspect_mp4(SAMPLE_MP4)
    assert info.valid and info.duration_seconds and info.duration_seconds > 0 and info.width and info.height
    html = tmp_path / "scene_001.mp4"
    html.write_bytes(b"<html><body>Please sign in to download this file</body></html>" * 4)
    assert inspect_mp4(html).valid is False
    assert not sniff_kind(html).startswith("video")
    truncated = tmp_path / "trunc.mp4"
    truncated.write_bytes(SAMPLE_MP4.read_bytes()[:40])
    assert inspect_mp4(truncated).valid is False
    assert inspect_media(SAMPLE_MP4).valid


# -------------------------------------------------------------------------- security
def test_password_hashing_and_secret_encryption() -> None:
    digest = hash_password("correct horse battery staple")
    assert digest.startswith("$argon2") and "correct horse" not in digest
    assert verify_password("correct horse battery staple", digest)
    assert not verify_password("wrong", digest)
    token = encrypt_secret("cookie=secret-value")
    assert "secret-value" not in token
    assert decrypt_secret(token) == "cookie=secret-value"


def test_signed_payloads_expire_and_reject_tampering() -> None:
    token = sign_payload({"k": "generated", "f": "abc"}, ttl_seconds=60)
    payload = verify_signed_payload(token)
    assert payload and payload["k"] == "generated" and payload["f"] == "abc" and payload["exp"] > payload["iat"]
    assert verify_signed_payload(token[:-2] + "zz") is None
    expired = sign_payload({"k": "generated"}, ttl_seconds=-1)
    assert verify_signed_payload(expired) is None
