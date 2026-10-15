"""Built-in mock video generation platform.

A tiny self-contained "website" that behaves like a real AI video generator (login wall,
prompt, reference upload, queue → generating → processing → completed, download). The
browser worker drives it exactly like an external site, which makes the whole pipeline
(queue, live view, takeover, retries, downloads, file management) testable without
spending real AI credits.

Job state is kept in memory – this is a development/testing aid, not a product feature.
"""

from __future__ import annotations

import secrets
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

router = APIRouter(prefix="/mock-platform", tags=["mock-platform"])

STATIC_DIR = Path(__file__).parent / "static"
SAMPLE_VIDEO = STATIC_DIR / "sample.mp4"
COOKIE = "mockvid_session"

SPEEDS = {"instant": 0.5, "fast": 2.0, "normal": 8.0, "slow": 25.0}

_jobs: dict[str, dict[str, Any]] = {}
_sessions: dict[str, str] = {}


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=20_000)
    duration: int = 8
    aspect_ratio: str = "16:9"
    speed: str = "normal"
    fail_rate: float = 0.0
    references: int = 0


class LoginRequest(BaseModel):
    email: str = Field(min_length=1, max_length=200)


def _prune_jobs() -> None:
    if len(_jobs) > 500:
        for key in sorted(_jobs, key=lambda k: _jobs[k]["created"])[:250]:
            _jobs.pop(key, None)


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index() -> HTMLResponse:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@router.get("/api/me", include_in_schema=False)
async def me(request: Request, login: str | None = None) -> JSONResponse:
    token = request.cookies.get(COOKIE)
    user = _sessions.get(token or "")
    # When ?login=1 is set the platform insists on an explicit login (simulates a login wall).
    if login == "1":
        return JSONResponse({"logged_in": user is not None, "user": user})
    return JSONResponse({"logged_in": True, "user": user or "guest"})


@router.post("/api/login", include_in_schema=False)
async def login(payload: LoginRequest, response: Response) -> dict[str, Any]:
    token = secrets.token_urlsafe(24)
    _sessions[token] = payload.email
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", max_age=7 * 24 * 3600)
    return {"ok": True, "user": payload.email}


@router.post("/api/logout", include_in_schema=False)
async def logout(request: Request, response: Response) -> dict[str, Any]:
    token = request.cookies.get(COOKIE)
    _sessions.pop(token or "", None)
    response.delete_cookie(COOKIE)
    return {"ok": True}


@router.post("/api/generate", include_in_schema=False)
async def generate(payload: GenerateRequest) -> dict[str, Any]:
    _prune_jobs()
    job_id = uuid.uuid4().hex[:10]
    total = SPEEDS.get(payload.speed, SPEEDS["normal"])
    fail = secrets.randbelow(10_000) < int(max(0.0, min(1.0, payload.fail_rate)) * 10_000)
    _jobs[job_id] = {
        "id": job_id,
        "prompt": payload.prompt,
        "duration": payload.duration,
        "aspect_ratio": payload.aspect_ratio,
        "created": time.time(),
        "total": total,
        "fail": fail,
        "references": payload.references,
    }
    return {"id": job_id}


@router.get("/api/jobs/{job_id}", include_in_schema=False)
async def job_status(job_id: str) -> JSONResponse:
    job = _jobs.get(job_id)
    if job is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    elapsed = time.time() - job["created"]
    total = job["total"]
    if elapsed < total * 0.1:
        status, progress = "queued", 0
    elif elapsed < total * 0.85:
        status, progress = "generating", int(min(95, (elapsed / total) * 100))
    elif elapsed < total:
        status, progress = "processing", 96
    else:
        status, progress = ("failed", 0) if job["fail"] else ("completed", 100)
    return JSONResponse(
        {
            **{k: v for k, v in job.items() if k not in ("fail", "created", "total")},
            "status": status,
            "progress": progress,
            "error": "server error, please retry" if status == "failed" else None,
        }
    )


@router.get("/api/download/{job_id}", include_in_schema=False)
async def download(job_id: str, inline: str | None = None) -> Response:
    job = _jobs.get(job_id)
    if job is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    if time.time() - job["created"] < job["total"] or job["fail"]:
        return JSONResponse({"error": "not ready"}, status_code=409)
    headers = {"Cache-Control": "no-store"}
    if inline != "1":
        headers["Content-Disposition"] = f'attachment; filename="mockvid_{job_id}.mp4"'
    return FileResponse(SAMPLE_VIDEO, media_type="video/mp4", headers=headers)


def reset_state() -> None:
    _jobs.clear()
    _sessions.clear()
