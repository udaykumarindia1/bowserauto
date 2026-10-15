"""JARVIS command interpreter.

Turns a natural-language command typed (or dictated) in the HUD console into a concrete
workspace action, an answer, or a request for confirmation. Resolution order:

1. **Deterministic intents** – a compact regex grammar covers the operator vocabulary
   (status, stop everything, pause/resume/retry/skip/take over a task, open a session,
   navigate, create a browser task, remember/forget, open a page, theme/name changes).
   These work with no AI provider configured and are fully unit-tested.
2. **LLM routing** – when an LLM is configured for the organization, anything the grammar
   does not recognise is sent to the model with the same tool catalogue in JSON mode; the
   model may either pick a tool or answer directly. The model never executes anything
   itself – it only selects one of the tools below, which are then run by this module.
3. **Fallback** – without an LLM, unrecognised imperatives are offered as a browser task
   (the user confirms) and everything else gets a short, honest "I did not catch an action".

Irreversible or expensive actions (stop everything, stop/delete, creating tasks that spend
AI credits) return a ``confirm`` payload instead of acting. The client shows a confirm
banner and re-submits the identical command with ``confirmed=True``.

Replies are written in the Mark-LV voice: first sentence is the assessment, concrete numbers
over adjectives, one steady register for success and failure alike.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.llm import LLMMessage, LLMUnavailable, build_llm_client
from app.core.deps import AuthContext
from app.core.errors import AppError, ValidationFailed
from app.models import BrowserSession, GeneratedFile, MemoryFact, OrgRole, Project, Task, TaskStatus
from app.platforms.registry import get_adapter_class, list_platform_info
from app.services import session_service, task_service
from app.services.org_settings import llm_overrides_for

logger = logging.getLogger(__name__)

ACTIVE = [s.value for s in TaskStatus if s.is_active]
ATTENTION = (TaskStatus.needs_attention.value, TaskStatus.waiting_for_human.value)
SECRET_PATTERN = re.compile(r"(password|passwd|api[_ -]?key|secret|token|sk-[a-z0-9]{8,}|bearer\s)", re.IGNORECASE)
URL_PATTERN = re.compile(r"((?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s]*)?)", re.IGNORECASE)
HEX_PATTERN = re.compile(r"#?([0-9a-f]{6})\b", re.IGNORECASE)

PAGES = {
    "dashboard": "/dashboard",
    "command center": "/dashboard",
    "home": "/dashboard",
    "new task": "/new-task",
    "task composer": "/new-task",
    "projects": "/projects",
    "sessions": "/sessions",
    "browser sessions": "/sessions",
    "browsers": "/sessions",
    "tasks": "/tasks",
    "queue": "/tasks",
    "files": "/files",
    "generated files": "/files",
    "downloads": "/files",
    "platforms": "/platforms",
    "plugins": "/platforms",
    "templates": "/templates",
    "activity": "/activity",
    "activity log": "/activity",
    "logs": "/activity",
    "settings": "/settings",
    "memory": "/settings?tab=memory",
}

COLOUR_NAMES = {
    "cyan": "#00d4ff",
    "blue": "#2f7bff",
    "teal": "#00d4ff",
    "green": "#00ff88",
    "red": "#ff3355",
    "orange": "#ff6b00",
    "amber": "#ffcc00",
    "yellow": "#ffcc00",
    "gold": "#ffcc00",
    "purple": "#a855f7",
    "violet": "#8b5cf6",
    "pink": "#ff4fa3",
    "magenta": "#ff00c8",
    "white": "#d8f8ff",
    "grey": "#9aa4ad",
    "gray": "#9aa4ad",
}

TOOL_CATALOGUE: list[dict[str, Any]] = [
    {"tool": "status", "args": {}, "description": "Report what is running, queued and waiting for the operator."},
    {"tool": "stop_everything", "args": {}, "description": "Emergency stop: cancel every active task in the organization."},
    {"tool": "task_control", "args": {"action": "pause|resume|stop|retry|skip|takeover|release|start", "task": "task id, title fragment or 'current'"}, "description": "Control a task."},
    {"tool": "open_session", "args": {"session": "session name or platform key", "url": "optional url"}, "description": "Open a persistent browser session in the live viewer."},
    {"tool": "new_session", "args": {"platform": "platform key", "name": "optional"}, "description": "Create a new persistent browser session for a platform."},
    {"tool": "navigate", "args": {"url": "https://...", "session": "optional session name"}, "description": "Navigate the current or named browser session to a URL."},
    {"tool": "create_task", "args": {"instructions": "what the browser agent should do", "platform": "optional platform key"}, "description": "Queue a browser-agent task that performs instructions in a browser."},
    {"tool": "generate_project", "args": {"project": "project name fragment"}, "description": "Start video generation for all scenes of a project."},
    {"tool": "remember", "args": {"key": "short label", "value": "fact"}, "description": "Store a fact in memory."},
    {"tool": "recall", "args": {}, "description": "List stored memory facts."},
    {"tool": "forget", "args": {"query": "key or fragment"}, "description": "Delete matching memory facts."},
    {"tool": "open_page", "args": {"page": "dashboard|new task|projects|sessions|tasks|files|platforms|templates|activity|settings"}, "description": "Navigate the workspace UI."},
    {"tool": "set_theme", "args": {"color": "#hex or colour name"}, "description": "Recolour the HUD."},
    {"tool": "set_name", "args": {"assistant_name": "optional", "user_name": "optional"}, "description": "Rename the assistant or how it addresses the operator."},
    {"tool": "answer", "args": {"text": "reply"}, "description": "Answer directly when no action is needed."},
]


@dataclass
class CommandContext:
    task_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    page: str = ""
    local_hour: int | None = None


@dataclass
class CommandResult:
    reply: str
    intent: str
    ok: bool = True
    actions: list[dict[str, Any]] = field(default_factory=list)
    navigate: str | None = None
    confirm: dict[str, Any] | None = None
    client: dict[str, Any] | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "reply": self.reply,
            "intent": self.intent,
            "ok": self.ok,
            "actions": self.actions,
            "navigate": self.navigate,
            "confirm": self.confirm,
            "client": self.client,
            "data": self.data,
        }


# ----------------------------------------------------------------------------- helpers
def honorific(auth: AuthContext) -> str:
    prefs = (auth.user.preferences or {}).get("jarvis") or {}
    name = (prefs.get("user_name") or "").strip()
    return name or "sir"


def assistant_name(auth: AuthContext) -> str:
    prefs = (auth.user.preferences or {}).get("jarvis") or {}
    return (prefs.get("assistant_name") or "JARVIS").strip() or "JARVIS"


def _short(task: Task) -> str:
    return task.title or f"task {str(task.id)[:8]}"


async def _find_task(db: AsyncSession, org_id: uuid.UUID, ref: str | None, ctx: CommandContext, *, prefer_active: bool = True) -> Task | None:
    ref = (ref or "").strip().strip("\"'")
    if ref.lower() in ("", "current", "this", "it", "the current task", "this task") and ctx.task_id:
        task = await db.get(Task, ctx.task_id)
        return task if task and task.organization_id == org_id else None
    if ref:
        try:
            task = await db.get(Task, uuid.UUID(ref))
            if task and task.organization_id == org_id:
                return task
        except ValueError:
            pass
        rows = (await db.execute(select(Task).where(Task.organization_id == org_id).order_by(Task.created_at.desc()).limit(200))).scalars().all()
        lowered = ref.lower()
        by_prefix = [t for t in rows if str(t.id).startswith(lowered)]
        if by_prefix:
            return by_prefix[0]
        by_title = [t for t in rows if lowered in (t.title or "").lower()]
        if by_title:
            active_first = [t for t in by_title if t.status in ACTIVE]
            return (active_first or by_title)[0]
        if ref.lower() not in ("task", "the task", "latest", "last", "running", "the running task"):
            return None
    if ctx.task_id:
        task = await db.get(Task, ctx.task_id)
        if task and task.organization_id == org_id:
            return task
    if prefer_active:
        rows = (await db.execute(select(Task).where(Task.organization_id == org_id, Task.status.in_(ACTIVE)).order_by(Task.updated_at.desc()).limit(1))).scalars().all()
        if rows:
            return rows[0]
    rows = (await db.execute(select(Task).where(Task.organization_id == org_id).order_by(Task.created_at.desc()).limit(1))).scalars().all()
    return rows[0] if rows else None


async def _find_session(db: AsyncSession, org_id: uuid.UUID, ref: str | None, ctx: CommandContext) -> BrowserSession | None:
    ref = (ref or "").strip().strip("\"'").lower()
    sessions = await session_service.list_sessions(db, org_id)
    if ref in ("", "current", "this", "the browser", "browser") and ctx.session_id:
        return next((s for s in sessions if s.id == ctx.session_id), None)
    if ref:
        for s in sessions:
            if str(s.id).startswith(ref) or ref == s.name.lower():
                return s
        words = set(ref.replace("-", " ").replace("_", " ").split())
        for s in sessions:
            platform_words = set(s.platform_key.replace("_", " ").split())
            if ref in s.name.lower() or ref.replace(" ", "_") == s.platform_key or platform_words <= words:
                return s
        wanted = _platform_key_for(ref)
        if wanted:
            matching = [s for s in sessions if s.platform_key == wanted]
            live = [s for s in matching if s.status in ("ready", "in_use")]
            if matching:
                return (live or matching)[0]
        return None
    if ctx.session_id:
        return next((s for s in sessions if s.id == ctx.session_id), None)
    live = [s for s in sessions if s.status in ("ready", "in_use")]
    return (live or sessions)[0] if sessions else None


def _platform_key_for(text: str) -> str | None:
    lowered = text.lower().replace("-", " ").replace("_", " ")
    for info in list_platform_info():
        key = info["key"]
        if key.replace("_", " ") in lowered or info.get("name", "").lower() in lowered:
            return key
    if "flow" in lowered or "veo" in lowered:
        return "google_flow" if get_adapter_class("google_flow") else None
    return None


def _normalise_url(raw: str) -> str:
    url = raw.strip().rstrip(".,;")
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url
    return url


def _confirm(title: str, detail: str, command: str) -> dict[str, Any]:
    return {"title": title, "detail": detail, "command": command}


# ----------------------------------------------------------------------------- status / briefing
async def status_snapshot(db: AsyncSession, org_id: uuid.UUID) -> dict[str, Any]:
    counts = dict((await db.execute(select(Task.status, func.count(Task.id)).where(Task.organization_id == org_id).group_by(Task.status))).all())
    attention = (await db.execute(select(Task).where(Task.organization_id == org_id, Task.status.in_(ATTENTION)).order_by(Task.updated_at.desc()).limit(5))).scalars().all()
    running = (await db.execute(select(Task).where(Task.organization_id == org_id, Task.status == TaskStatus.running.value).order_by(Task.updated_at.desc()).limit(5))).scalars().all()
    sessions = await session_service.list_sessions(db, org_id)
    since = datetime.now(UTC) - timedelta(hours=24)
    files_24h = (await db.execute(select(func.count(GeneratedFile.id)).where(GeneratedFile.organization_id == org_id, GeneratedFile.created_at >= since))).scalar_one()
    failed_24h = (await db.execute(select(func.count(Task.id)).where(Task.organization_id == org_id, Task.status == TaskStatus.failed.value, Task.updated_at >= since))).scalar_one()
    return {
        "running": [{"id": str(t.id), "title": _short(t), "action": t.current_action, "percent": (t.progress or {}).get("percent", 0)} for t in running],
        "queued": counts.get(TaskStatus.queued.value, 0),
        "paused": counts.get(TaskStatus.paused.value, 0),
        "attention": [{"id": str(t.id), "title": _short(t), "reason": (t.attention or {}).get("reason", ""), "kind": (t.attention or {}).get("kind", "")} for t in attention],
        "completed": counts.get(TaskStatus.completed.value, 0),
        "failed": counts.get(TaskStatus.failed.value, 0),
        "sessions": {
            "total": len(sessions),
            "live": sum(1 for s in sessions if s.status in ("ready", "in_use")),
            "logged_out": [s.name for s in sessions if s.login_status in ("logged_out", "unknown") and s.status != "closed"][:5],
            "expired": [s.name for s in sessions if s.status == "expired"][:5],
        },
        "files_24h": int(files_24h),
        "failed_24h": int(failed_24h),
    }


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def status_sentence(snap: dict[str, Any], who: str) -> str:
    parts: list[str] = []
    running = snap["running"]
    if running:
        lead = running[0]
        detail = f" — {lead['action']}" if lead.get("action") else ""
        parts.append(f"{_plural(len(running), 'task')} running, {who}: {lead['title']}{detail}.")
    else:
        parts.append(f"Nothing is running, {who}.")
    if snap["queued"]:
        parts.append(f"{_plural(snap['queued'], 'task')} queued.")
    if snap["paused"]:
        parts.append(f"{_plural(snap['paused'], 'task')} paused.")
    if snap["attention"]:
        first = snap["attention"][0]
        reason = f": {first['reason']}" if first.get("reason") else ""
        parts.append(f"{_plural(len(snap['attention']), 'task')} waiting on you — {first['title']}{reason}.")
    sess = snap["sessions"]
    if sess["expired"]:
        parts.append(f"Session {sess['expired'][0]} has expired and needs reconnecting.")
    elif sess["logged_out"]:
        parts.append(f"{_plural(len(sess['logged_out']), 'session')} not logged in: {', '.join(sess['logged_out'][:3])}.")
    return " ".join(parts)


async def briefing(db: AsyncSession, auth: AuthContext, local_hour: int | None) -> dict[str, Any]:
    snap = await status_snapshot(db, auth.org_id)
    who = honorific(auth)
    hour = local_hour if local_hour is not None else datetime.now().hour
    greeting = "Good morning" if 5 <= hour < 12 else "Good afternoon" if 12 <= hour < 18 else "Good evening"
    lines = [f"{greeting}, {who}. {assistant_name(auth)} online."]
    if snap["files_24h"] or snap["failed_24h"]:
        lines.append(f"Last 24 hours: {_plural(snap['files_24h'], 'file')} generated, {_plural(snap['failed_24h'], 'task')} failed.")
    lines.append(status_sentence(snap, who))
    return {"text": " ".join(lines), "status": snap, "assistant_name": assistant_name(auth), "user_name": who}


# ----------------------------------------------------------------------------- memory
async def list_memory(db: AsyncSession, org_id: uuid.UUID) -> list[MemoryFact]:
    return list((await db.execute(select(MemoryFact).where(MemoryFact.organization_id == org_id).order_by(MemoryFact.created_at.desc()))).scalars().all())


async def remember(db: AsyncSession, auth: AuthContext, *, key: str, value: str, category: str = "notes", source: str = "user") -> MemoryFact:
    value = value.strip()
    key = (key or "").strip()[:120] or value[:60]
    if not value:
        raise ValidationFailed("Nothing to remember")
    if SECRET_PATTERN.search(value) or SECRET_PATTERN.search(key):
        raise ValidationFailed("Memory refuses to store credentials or tokens; log in through the browser session instead")
    existing = (await db.execute(select(MemoryFact).where(MemoryFact.organization_id == auth.org_id, func.lower(MemoryFact.key) == key.lower()))).scalars().first()
    if existing:
        existing.value = value[:4000]
        existing.category = category
        existing.source = source
        await db.flush()
        return existing
    count = (await db.execute(select(func.count(MemoryFact.id)).where(MemoryFact.organization_id == auth.org_id))).scalar_one()
    if count >= 500:
        raise ValidationFailed("Memory is full (500 facts); forget something first")
    fact = MemoryFact(organization_id=auth.org_id, user_id=auth.user_id, key=key, value=value[:4000], category=category[:40] or "notes", source=source)
    db.add(fact)
    await db.flush()
    return fact


async def forget(db: AsyncSession, org_id: uuid.UUID, query: str) -> int:
    query = (query or "").strip().lower()
    facts = await list_memory(db, org_id)
    if query in ("", "everything", "all", "all of it"):
        targets = facts
    else:
        targets = [f for f in facts if query in f.key.lower() or query in f.value.lower()]
    for fact in targets:
        await db.delete(fact)
    await db.flush()
    return len(targets)


async def memory_prompt_lines(db: AsyncSession, org_id: uuid.UUID, limit: int = 30) -> list[str]:
    facts = (await db.execute(select(MemoryFact).where(MemoryFact.organization_id == org_id).order_by(MemoryFact.updated_at.desc()).limit(limit))).scalars().all()
    return [f"{f.key}: {f.value}" for f in facts]


# ----------------------------------------------------------------------------- grammar
_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("help", re.compile(r"^\s*(help|what can you do|capabilities|commands)\b", re.I)),
    ("status", re.compile(r"\b(status|sitrep|report|what(?:'s| is) (?:running|happening|going on)|how are (?:things|we doing)|briefing|update me)\b", re.I)),
    ("stop_everything", re.compile(r"\b(stop everything|stop all|emergency stop|halt all|kill everything|abort all|shut (?:it )?all down)\b", re.I)),
    ("recall", re.compile(r"\b(what do you remember|show (?:your |my )?memory|list (?:your |my )?memor(?:y|ies)|what have you (?:stored|remembered))\b", re.I)),
    ("forget", re.compile(r"^\s*forget\s+(?:about\s+|that\s+)?(?P<query>.+)$", re.I)),
    ("remember", re.compile(r"^\s*(?:please\s+)?(?:remember|note|keep in mind|memori[sz]e)\s+(?:that\s+)?(?P<value>.+)$", re.I)),
    ("set_theme", re.compile(r"\b(?:(?:change|set|switch|make)\s+(?:the\s+)?(?:ui\s+|hud\s+|theme\s+)?colou?r|theme|recolou?r)\b.*?(?:to\s+)?(?P<color>#?[0-9a-f]{6}\b|" + "|".join(COLOUR_NAMES) + r")\b", re.I)),
    ("set_name", re.compile(r"\b(?:call me|my name is|address me as)\s+(?P<user_name>[A-Za-z][\w .'-]{0,40})$", re.I)),
    ("set_assistant", re.compile(r"\b(?:your name is|call yourself|rename yourself(?: to)?|i(?:'ll| will) call you)\s+(?P<assistant_name>[A-Za-z][\w .'-]{0,30})$", re.I)),
    ("create_task", re.compile(r"^\s*(?:run|do|execute|task|agent|browser)\s*[:\-]\s*(?P<instructions>.+)$", re.I)),
    ("task_control", re.compile(r"^\s*(?P<action>pause|resume|continue|unpause|stop|cancel|abort|retry|skip|take ?over|takeover|release|return control|hand back|start|run)\b\s*(?:the\s+)?(?:(?:current|this|running|latest|last)\s+)?(?:task\s*)?(?:[\"']?(?P<task>[^\"']{1,80})[\"']?)?\s*$", re.I)),
    ("generate_project", re.compile(r"\b(?:generate|render|produce)\s+(?:the\s+)?(?:video(?:s)?|scenes?|clips?)\s*(?:for|of|in)?\s*(?:project\s+)?(?P<project>.+)?$", re.I)),
    ("new_session", re.compile(r"\b(?:create|new|add)\s+(?:a\s+)?(?:browser\s+)?session\s*(?:for|on)?\s*(?P<platform>.+)?$", re.I)),
    ("navigate", re.compile(r"^\s*(?:navigate|go|browse|take (?:me|the browser))\s+to\s+(?P<url>\S+)(?:\s+in\s+(?P<session>.+))?\s*$", re.I)),
    ("open_page", re.compile(r"^\s*(?:open|show|go to|display|take me to)\s+(?:the\s+)?(?:my\s+)?(?P<page>" + "|".join(sorted(PAGES, key=len, reverse=True)) + r")(?:\s+page|\s+tab)?\s*$", re.I)),
    ("open_session", re.compile(r"^\s*(?:open|launch|bring up|show)\s+(?:the\s+)?(?:browser\s+)?(?:session\s+)?(?P<session>.+?)(?:\s+session|\s+browser)?\s*$", re.I)),
    ("create_task", re.compile(r"^\s*(?:run|do|execute|task|agent|please)\s*[:,-]?\s+(?P<instructions>.+)$", re.I)),
]

_HELP = (
    "Commands I act on, {who}: 'status' · 'stop everything' · 'pause|resume|stop|retry|skip|take over|release [task]' · "
    "'open <session or platform>' · 'go to <url>' · 'new session for <platform>' · 'run: <instructions for the browser agent>' · "
    "'generate videos for <project>' · 'remember <fact>' · 'what do you remember' · 'forget <fact>' · "
    "'open settings|files|projects|…' · 'change colour to <name or hex>' · 'call me <name>'. "
    "I never bypass logins, CAPTCHAs or payment steps — those stay with you."
)


def parse_command(text: str) -> tuple[str, dict[str, str]]:
    """Map a command to ``(intent, args)`` using the deterministic grammar; ``("unknown", {})`` when none match."""
    stripped = text.strip()
    for intent, pattern in _RULES:
        m = pattern.search(stripped)
        if m:
            return intent, {k: (v or "").strip() for k, v in m.groupdict().items()}
    return "unknown", {}


# ----------------------------------------------------------------------------- executor
class Assistant:
    def __init__(self, db: AsyncSession, auth: AuthContext) -> None:
        self.db = db
        self.auth = auth
        self.who = honorific(auth)

    async def handle(self, text: str, ctx: CommandContext, *, confirmed: bool = False) -> CommandResult:
        text = (text or "").strip()
        if not text:
            return CommandResult(f"Say the word, {self.who}.", "empty", ok=False)
        if len(text) > 4000:
            raise ValidationFailed("Command too long")
        intent, args = parse_command(text)
        if intent == "unknown":
            routed = await self._route_with_llm(text, ctx)
            if routed is not None:
                intent, args = routed
        try:
            return await self._dispatch(intent, args, text, ctx, confirmed)
        except AppError as exc:
            return CommandResult(f"Could not do that, {self.who}: {exc.message}", intent, ok=False, data={"code": exc.code})

    # -- routing -------------------------------------------------------------------------
    async def _route_with_llm(self, text: str, ctx: CommandContext) -> tuple[str, dict[str, str]] | None:
        client = build_llm_client(llm_overrides_for(self.auth.organization))
        if client is None:
            return None
        catalogue = "\n".join(f"- {t['tool']}: {t['description']} args={t['args']}" for t in TOOL_CATALOGUE)
        system = (
            f"You are {assistant_name(self.auth)}, the operator console of a browser-automation workspace. "
            "Map the operator's request to exactly one tool from the catalogue and respond with JSON only: "
            '{"tool": "<name>", "args": {...}}. Use "answer" with a short reply (max 2 sentences, no markdown) when no action is needed. '
            "Never claim to have done something; the system executes the tool you choose."
        )
        user = f"TOOLS:\n{catalogue}\n\nCONTEXT: page={ctx.page or '-'} task={ctx.task_id or '-'} session={ctx.session_id or '-'} project={ctx.project_id or '-'}\n\nREQUEST: {text}"
        try:
            response = await client.complete([LLMMessage("system", system), LLMMessage("user", user)], json_mode=True, max_tokens=300, temperature=0.1)
            payload = response.json()
        except (LLMUnavailable, Exception) as exc:  # noqa: BLE001 - any provider failure degrades to the fallback
            logger.warning("assistant LLM routing failed: %s", exc)
            return None
        if not isinstance(payload, dict) or not isinstance(payload.get("tool"), str):
            return None
        tool = payload["tool"].strip()
        known = {t["tool"] for t in TOOL_CATALOGUE}
        if tool not in known:
            return None
        args = {k: str(v) for k, v in (payload.get("args") or {}).items() if v is not None}
        if tool == "set_name":
            return ("set_assistant" if args.get("assistant_name") else "set_name"), args
        return tool, args

    # -- dispatch ------------------------------------------------------------------------
    async def _dispatch(self, intent: str, args: dict[str, str], text: str, ctx: CommandContext, confirmed: bool) -> CommandResult:
        handler = getattr(self, f"_do_{intent}", None)
        if handler is None:
            return await self._do_unknown(args, text, ctx, confirmed)
        return await handler(args, text, ctx, confirmed)

    def _require_write(self) -> None:
        self.auth.require_role(OrgRole.member.value)

    async def _do_help(self, args, text, ctx, confirmed) -> CommandResult:
        return CommandResult(_HELP.format(who=self.who), "help")

    async def _do_answer(self, args, text, ctx, confirmed) -> CommandResult:
        return CommandResult((args.get("text") or "Noted.").strip(), "answer")

    async def _do_status(self, args, text, ctx, confirmed) -> CommandResult:
        snap = await status_snapshot(self.db, self.auth.org_id)
        return CommandResult(status_sentence(snap, self.who), "status", data={"status": snap})

    async def _do_stop_everything(self, args, text, ctx, confirmed) -> CommandResult:
        self._require_write()
        active = (await self.db.execute(select(func.count(Task.id)).where(Task.organization_id == self.auth.org_id, Task.status.in_(ACTIVE)))).scalar_one()
        if not active:
            return CommandResult(f"Nothing to stop, {self.who}. No active tasks.", "stop_everything")
        if not confirmed:
            return CommandResult(
                f"This cancels {_plural(int(active), 'active task')}. Confirm, {self.who}?",
                "stop_everything",
                confirm=_confirm("STOP EVERYTHING", f"Cancel {_plural(int(active), 'active task')} and release every browser.", text),
            )
        count = await task_service.stop_everything(self.db, self.auth.org_id)
        return CommandResult(f"Done. {_plural(count, 'task')} stopped, browsers released.", "stop_everything", actions=[{"type": "task.stop_all", "count": count}], navigate="/tasks")

    async def _do_task_control(self, args, text, ctx, confirmed) -> CommandResult:
        self._require_write()
        verb = args.get("action", "").lower().replace(" ", "")
        action = {
            "pause": "pause", "resume": "resume", "continue": "resume", "unpause": "resume",
            "stop": "stop", "cancel": "stop", "abort": "stop", "retry": "retry", "skip": "skip",
            "takeover": "takeover", "release": "release", "returncontrol": "release", "handback": "release",
            "start": "start", "run": "start",
        }.get(verb)
        if action is None:
            return await self._do_unknown(args, text, ctx, confirmed)
        ref = args.get("task", "")
        if action == "start" and ref and not await self._looks_like_task_ref(ref, ctx):
            # "run: open example.com" is a task creation, not a control verb
            return await self._do_create_task({"instructions": ref}, text, ctx, confirmed)
        task = await _find_task(self.db, self.auth.org_id, ref, ctx, prefer_active=action != "start")
        if task is None:
            return CommandResult(f"I cannot find a task matching '{ref or 'current'}', {self.who}.", "task_control", ok=False)
        if action == "stop" and not confirmed:
            return CommandResult(f"Stop {_short(task)}? It is {task.status.replace('_', ' ')}.", "task_control", confirm=_confirm("STOP TASK", f"Cancel '{_short(task)}'. Completed scenes are kept.", text), data={"task_id": str(task.id)})
        fn = {
            "pause": task_service.pause_task, "resume": task_service.resume_task, "stop": task_service.stop_task,
            "retry": task_service.retry_task, "skip": task_service.skip_scene, "takeover": task_service.takeover_task,
            "release": task_service.release_task, "start": task_service.start_task,
        }[action]
        await fn(self.db, task)
        verbs = {"pause": "Pausing", "resume": "Resuming", "stop": "Stopping", "retry": "Retrying", "skip": "Skipping the current scene of", "takeover": "Browser control is yours for", "release": "Control returned to the agent for", "start": "Starting"}
        return CommandResult(f"{verbs[action]} {_short(task)}.", f"task.{action}", actions=[{"type": f"task.{action}", "task_id": str(task.id)}], navigate=f"/tasks/{task.id}", data={"task_id": str(task.id)})

    async def _looks_like_task_ref(self, ref: str, ctx: CommandContext) -> bool:
        if len(ref.split()) > 4:
            return False
        return await _find_task(self.db, self.auth.org_id, ref, ctx) is not None and ref.lower() not in ("", "task")

    async def _do_open_session(self, args, text, ctx, confirmed) -> CommandResult:
        self._require_write()
        ref = args.get("session", "")
        session = await _find_session(self.db, self.auth.org_id, ref, ctx)
        if session is None:
            platform = _platform_key_for(ref)
            if platform:
                return await self._do_new_session({"platform": platform}, text, ctx, confirmed)
            return CommandResult(f"No browser session called '{ref}', {self.who}. Say 'new session for <platform>' to create one.", "open_session", ok=False, navigate="/sessions")
        url = args.get("url") or None
        await session_service.request_open(self.db, session, interactive=True, url=_normalise_url(url) if url else None)
        return CommandResult(f"Opening {session.name}. Log in there yourself if it asks — I never handle credentials.", "open_session", actions=[{"type": "session.open", "session_id": str(session.id)}], navigate=f"/sessions/{session.id}", data={"session_id": str(session.id)})

    async def _do_new_session(self, args, text, ctx, confirmed) -> CommandResult:
        self._require_write()
        raw = args.get("platform", "") or ""
        key = _platform_key_for(raw) or (raw.strip().lower().replace(" ", "_") if raw else "generic")
        if get_adapter_class(key) is None:
            return CommandResult(f"I do not have a platform adapter for '{raw}', {self.who}. Installed: {', '.join(p['key'] for p in list_platform_info())}.", "new_session", ok=False)
        info = next((p for p in list_platform_info() if p["key"] == key), {"name": key})
        name = args.get("name") or f"{info['name']} session"
        session = await session_service.create_session(self.db, self.auth, {"name": name, "platform_key": key, "start_url": ""})
        await session_service.request_open(self.db, session, interactive=True)
        return CommandResult(f"Created {name} and opening it now. Sign in manually when the page loads.", "new_session", actions=[{"type": "session.create", "session_id": str(session.id)}], navigate=f"/sessions/{session.id}", data={"session_id": str(session.id)})

    async def _do_navigate(self, args, text, ctx, confirmed) -> CommandResult:
        self._require_write()
        url = args.get("url", "")
        if not URL_PATTERN.search(url):
            return CommandResult(f"That does not look like a URL, {self.who}.", "navigate", ok=False)
        session = await _find_session(self.db, self.auth.org_id, args.get("session"), ctx)
        if session is None:
            return CommandResult("There is no browser session to navigate. Create one first.", "navigate", ok=False, navigate="/sessions")
        target = _normalise_url(url)
        if session.status in ("ready", "in_use"):
            await session_service.navigate(self.db, session, target)
        else:
            await session_service.request_open(self.db, session, interactive=True, url=target)
        return CommandResult(f"Taking {session.name} to {target}.", "navigate", actions=[{"type": "session.navigate", "session_id": str(session.id), "url": target}], navigate=f"/sessions/{session.id}", data={"session_id": str(session.id)})

    async def _do_create_task(self, args, text, ctx, confirmed) -> CommandResult:
        self._require_write()
        instructions = (args.get("instructions") or "").strip()
        if len(instructions) < 4:
            return CommandResult(f"Tell me what the browser should do, {self.who}.", "create_task", ok=False)
        platform = args.get("platform") or _platform_key_for(instructions) or None
        if not confirmed:
            return CommandResult(
                "I will queue a browser-agent task for that. Confirm?",
                "create_task",
                confirm=_confirm("QUEUE BROWSER TASK", instructions[:300], text),
                navigate=None,
            )
        session = await _find_session(self.db, self.auth.org_id, None, ctx) if ctx.session_id else None
        data = {
            "title": instructions[:80],
            "kind": "browser_workflow",
            "project_id": ctx.project_id,
            "instructions": instructions,
            "platform_key": platform or (session.platform_key if session else None) or "generic",
            "browser_session_id": session.id if session else None,
            "priority": 5,
            "config": {"agent_mode": "generic"},
            "start": True,
        }
        task = await task_service.create_task(self.db, self.auth, data)
        return CommandResult(f"Task queued: {_short(task)}. I will report when it needs you.", "create_task", actions=[{"type": "task.create", "task_id": str(task.id)}], navigate=f"/tasks/{task.id}", data={"task_id": str(task.id)})

    async def _do_generate_project(self, args, text, ctx, confirmed) -> CommandResult:
        self._require_write()
        ref = (args.get("project") or "").strip().strip("\"'").lower()
        projects = (await self.db.execute(select(Project).where(Project.organization_id == self.auth.org_id, Project.status == "active").order_by(Project.updated_at.desc()))).scalars().all()
        project = None
        if ref in ("", "this", "current", "this project") and ctx.project_id:
            project = next((p for p in projects if p.id == ctx.project_id), None)
        elif ref:
            project = next((p for p in projects if ref in p.name.lower()), None)
        elif ctx.project_id:
            project = next((p for p in projects if p.id == ctx.project_id), None)
        if project is None:
            return CommandResult(f"Which project, {self.who}? I did not find one matching '{ref}'.", "generate_project", ok=False, navigate="/projects")
        if not confirmed:
            return CommandResult(f"Start video generation for {project.name}? This uses the platform's generation credits.", "generate_project", confirm=_confirm("START GENERATION", f"Generate every scene of '{project.name}' on {project.platform_key}.", text), data={"project_id": str(project.id)})
        task = await task_service.create_task(self.db, self.auth, {"title": f"Generate {project.name}", "kind": "video_generation", "project_id": project.id, "instructions": "", "priority": 5, "config": {}, "start": True})
        return CommandResult(f"Generation queued for {project.name}: {_short(task)}.", "generate_project", actions=[{"type": "task.create", "task_id": str(task.id)}], navigate=f"/tasks/{task.id}", data={"task_id": str(task.id)})

    async def _do_remember(self, args, text, ctx, confirmed) -> CommandResult:
        self._require_write()
        value = (args.get("value") or "").strip().rstrip(".")
        key = (args.get("key") or "").strip()
        if not key:
            m = re.match(r"^(?:my|the|our)\s+([\w ]{2,40}?)\s+(?:is|are|=)\s+(.+)$", value, re.I)
            if m:
                key, value = m.group(1).strip(), m.group(2).strip()
        fact = await remember(self.db, self.auth, key=key, value=value)
        return CommandResult(f"Noted: {fact.key} — {fact.value}.", "remember", actions=[{"type": "memory.add", "id": str(fact.id)}], data={"fact": {"id": str(fact.id), "key": fact.key, "value": fact.value}})

    async def _do_recall(self, args, text, ctx, confirmed) -> CommandResult:
        facts = await list_memory(self.db, self.auth.org_id)
        if not facts:
            return CommandResult(f"Nothing stored yet, {self.who}. Say 'remember …' and I will keep it.", "recall", data={"facts": []})
        listed = "; ".join(f"{f.key}: {f.value}" for f in facts[:8])
        more = f" (+{len(facts) - 8} more)" if len(facts) > 8 else ""
        return CommandResult(f"{_plural(len(facts), 'fact')} on file{more}: {listed}.", "recall", data={"facts": [{"id": str(f.id), "key": f.key, "value": f.value} for f in facts]}, navigate="/settings?tab=memory")

    async def _do_forget(self, args, text, ctx, confirmed) -> CommandResult:
        self._require_write()
        query = (args.get("query") or "").strip().rstrip(".")
        if query.lower() in ("everything", "all", "all of it") and not confirmed:
            return CommandResult("Wipe every stored fact? Confirm.", "forget", confirm=_confirm("CLEAR MEMORY", "Delete all memory facts for this organization.", text))
        removed = await forget(self.db, self.auth.org_id, query)
        if not removed:
            return CommandResult(f"Nothing matched '{query}'.", "forget", ok=False)
        return CommandResult(f"Forgotten. {_plural(removed, 'fact')} removed.", "forget", actions=[{"type": "memory.forget", "count": removed}])

    async def _do_open_page(self, args, text, ctx, confirmed) -> CommandResult:
        page = (args.get("page") or "").strip().lower()
        path = PAGES.get(page)
        if not path:
            return CommandResult(f"I do not have a page called '{page}'.", "open_page", ok=False)
        return CommandResult(f"Opening {page}.", "open_page", navigate=path)

    async def _do_set_theme(self, args, text, ctx, confirmed) -> CommandResult:
        raw = (args.get("color") or "").strip().lower()
        hex_match = HEX_PATTERN.fullmatch(raw) or HEX_PATTERN.search(raw)
        colour = f"#{hex_match.group(1).lower()}" if hex_match else COLOUR_NAMES.get(raw)
        if not colour:
            return CommandResult(f"I do not know the colour '{raw}'. Try a name or a hex value.", "set_theme", ok=False)
        return CommandResult(f"Retinting the HUD to {raw if raw in COLOUR_NAMES else colour}.", "set_theme", client={"action": "set_color", "value": colour})

    async def _do_set_name(self, args, text, ctx, confirmed) -> CommandResult:
        name = (args.get("user_name") or "").strip().rstrip(".")[:40]
        if not name:
            return CommandResult("Give me a name to use.", "set_name", ok=False)
        await self._save_prefs(user_name=name)
        self.who = name
        return CommandResult(f"Understood, {name}.", "set_name", client={"action": "set_user_name", "value": name})

    async def _do_set_assistant(self, args, text, ctx, confirmed) -> CommandResult:
        name = (args.get("assistant_name") or "").strip().rstrip(".")[:30]
        if not name:
            return CommandResult("Give me a name.", "set_assistant", ok=False)
        await self._save_prefs(assistant_name=name)
        return CommandResult(f"{name} it is, {self.who}.", "set_assistant", client={"action": "set_assistant_name", "value": name})

    async def _save_prefs(self, **values: str) -> None:
        prefs = dict(self.auth.user.preferences or {})
        jarvis = dict(prefs.get("jarvis") or {})
        jarvis.update(values)
        prefs["jarvis"] = jarvis
        self.auth.user.preferences = prefs
        await self.db.flush()

    async def _do_unknown(self, args, text, ctx, confirmed) -> CommandResult:
        if URL_PATTERN.search(text) and self.auth.role != OrgRole.viewer.value:
            return await self._do_create_task({"instructions": text}, text, ctx, confirmed)
        return CommandResult(
            f"I did not catch an action in that, {self.who}. Say 'help' for the command list, or 'run: …' to hand it to the browser agent.",
            "unknown",
            ok=False,
        )
