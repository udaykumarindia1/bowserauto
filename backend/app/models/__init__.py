"""SQLAlchemy ORM models.

Every tenant-owned table carries an ``organization_id`` so that all queries can be scoped
to the caller's organization (see ``app.core.deps``).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, JSONType, TimestampMixin, UUIDMixin, utcnow

__all__ = [
    "Base",
    "User",
    "Organization",
    "OrganizationMember",
    "AuthSession",
    "DevicePairing",
    "Project",
    "Character",
    "Asset",
    "Scene",
    "Task",
    "TaskStep",
    "TaskSchedule",
    "BrowserSession",
    "Platform",
    "AgentRun",
    "AgentAction",
    "GeneratedFile",
    "Screenshot",
    "ActivityLog",
    "Notification",
    "Template",
    "MemoryFact",
    "AuditLog",
    "TaskStatus",
    "TaskControl",
    "SceneStatus",
    "BrowserSessionStatus",
    "GenerationState",
    "OrgRole",
]


# ------------------------------------------------------------------------------ enums
class OrgRole(str, enum.Enum):
    owner = "owner"
    admin = "admin"
    member = "member"
    viewer = "viewer"


class TaskStatus(str, enum.Enum):
    draft = "draft"
    queued = "queued"
    running = "running"
    paused = "paused"
    waiting_for_human = "waiting_for_human"
    needs_attention = "needs_attention"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (TaskStatus.completed, TaskStatus.failed, TaskStatus.cancelled)

    @property
    def is_active(self) -> bool:
        return self in (
            TaskStatus.queued,
            TaskStatus.running,
            TaskStatus.paused,
            TaskStatus.waiting_for_human,
            TaskStatus.needs_attention,
        )


class TaskControl(str, enum.Enum):
    none = "none"
    pause = "pause"
    resume = "resume"
    stop = "stop"
    takeover = "takeover"
    release = "release"
    skip_scene = "skip_scene"
    retry_scene = "retry_scene"
    approve_action = "approve_action"
    reject_action = "reject_action"


class SceneStatus(str, enum.Enum):
    draft = "draft"
    queued = "queued"
    generating = "generating"
    processing = "processing"
    downloading = "downloading"
    completed = "completed"
    failed = "failed"
    skipped = "skipped"
    waiting_for_human = "waiting_for_human"


class GenerationState(str, enum.Enum):
    """Smart-wait states reported by platform adapters."""

    IDLE = "IDLE"
    QUEUED = "QUEUED"
    GENERATING = "GENERATING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    DOWNLOAD_READY = "DOWNLOAD_READY"
    DOWNLOADING = "DOWNLOADING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    BLOCKED = "BLOCKED"
    WAITING_FOR_HUMAN = "WAITING_FOR_HUMAN"
    UNKNOWN = "UNKNOWN"


class BrowserSessionStatus(str, enum.Enum):
    closed = "closed"
    launching = "launching"
    ready = "ready"
    in_use = "in_use"
    error = "error"
    expired = "expired"


# ------------------------------------------------------------------------------ users
class User(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    current_organization_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    preferences: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)

    memberships: Mapped[list[OrganizationMember]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Organization(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    # Encrypted secrets (e.g. LLM API key overrides). Values are Fernet ciphertext, never plaintext.
    secrets_encrypted: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    limits: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)

    members: Mapped[list[OrganizationMember]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )


class OrganizationMember(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "organization_members"
    __table_args__ = (UniqueConstraint("organization_id", "user_id", name="uq_org_member"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), default=OrgRole.member.value, nullable=False)

    organization: Mapped[Organization] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")


class AuthSession(Base, UUIDMixin, TimestampMixin):
    """Server-side login sessions (opaque cookie token, hashed at rest, revocable)."""

    __tablename__ = "auth_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user_agent: Mapped[str] = mapped_column(String(400), default="", nullable=False)
    ip_address: Mapped[str] = mapped_column(String(64), default="", nullable=False)


class DevicePairing(Base, UUIDMixin, TimestampMixin):
    """Short-lived, one-use QR pairing request for a same-origin mobile device."""

    __tablename__ = "device_pairings"
    __table_args__ = (Index("ix_device_pairings_user_status_expiry", "user_id", "status", "expires_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    device_name: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    user_agent: Mapped[str] = mapped_column(String(400), default="", nullable=False)
    claim_ip: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --------------------------------------------------------------------------- projects
class Project(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "projects"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    template_key: Mapped[str] = mapped_column(String(60), default="ai_video_generation", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="active", nullable=False)
    script_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    visual_style: Mapped[str] = mapped_column(String(100), default="cinematic", nullable=False)
    platform_key: Mapped[str] = mapped_column(String(60), default="mock", nullable=False)
    browser_session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    output_settings: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    folder_path: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    scenes: Mapped[list[Scene]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="Scene.index"
    )
    characters: Mapped[list[Character]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Character(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "characters"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    voice_style: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    reference_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("assets.id", ondelete="SET NULL"), nullable=True
    )

    project: Mapped[Project] = relationship(back_populates="characters")


class Asset(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "assets"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=True
    )
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(40), default="reference", nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    path: Mapped[str] = mapped_column(String(600), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream", nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONType, default=dict, nullable=False)


class Scene(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "scenes"
    __table_args__ = (Index("ix_scenes_project_index", "project_id", "index"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, default=8.0, nullable=False)
    characters: Mapped[list[Any]] = mapped_column(JSONType, default=list, nullable=False)
    location: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    visual: Mapped[str] = mapped_column(Text, default="", nullable=False)
    camera: Mapped[str] = mapped_column(Text, default="", nullable=False)
    dialogue: Mapped[str] = mapped_column(Text, default="", nullable=False)
    narration: Mapped[str] = mapped_column(Text, default="", nullable=False)
    prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    negative_prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    platform_key: Mapped[str | None] = mapped_column(String(60), nullable=True)
    reference_asset_ids: Mapped[list[Any]] = mapped_column(JSONType, default=list, nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), default=SceneStatus.draft.value, nullable=False, index=True
    )
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    generated_file_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    generation_metadata: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[Project] = relationship(back_populates="scenes")


# ------------------------------------------------------------------------------ tasks
class Task(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "tasks"
    __table_args__ = (Index("ix_tasks_queue", "status", "priority", "queued_at"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), default="video_generation", nullable=False)
    instructions: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), default=TaskStatus.draft.value, nullable=False, index=True
    )
    control: Mapped[str] = mapped_column(String(30), default=TaskControl.none.value, nullable=False)
    human_control: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    platform_key: Mapped[str] = mapped_column(String(60), default="mock", nullable=False)
    browser_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("browser_sessions.id", ondelete="SET NULL"), nullable=True
    )
    config: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    progress: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    attention: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    current_action: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    worker_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)

    steps: Mapped[list[TaskStep]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="TaskStep.index"
    )


class TaskStep(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "task_steps"

    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True, nullable=False
    )
    scene_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("scenes.id", ondelete="SET NULL"), nullable=True
    )
    index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)

    task: Mapped[Task] = relationship(back_populates="steps")


class TaskSchedule(Base, UUIDMixin, TimestampMixin):
    """Persistent, tenant-scoped schedule for an allowlisted generic browser workflow."""

    __tablename__ = "task_schedules"
    __table_args__ = (Index("ix_task_schedules_due", "enabled", "next_run_at"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), default="browser_workflow", nullable=False)
    instructions: Mapped[str] = mapped_column(Text, nullable=False)
    platform_key: Mapped[str] = mapped_column(String(60), default="generic", nullable=False)
    browser_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("browser_sessions.id", ondelete="SET NULL"), nullable=True
    )
    priority: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    repeat_interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    last_error: Mapped[str] = mapped_column(Text, default="", nullable=False)


# -------------------------------------------------------------------- browser sessions
class BrowserSession(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "browser_sessions"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    platform_key: Mapped[str] = mapped_column(String(60), default="generic", nullable=False)
    browser_type: Mapped[str] = mapped_column(String(30), default="chromium", nullable=False)
    profile_dir: Mapped[str] = mapped_column(String(600), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), default=BrowserSessionStatus.closed.value, nullable=False, index=True
    )
    desired_state: Mapped[str] = mapped_column(String(20), default="closed", nullable=False)
    interactive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    login_status: Mapped[str] = mapped_column(String(20), default="unknown", nullable=False)
    worker_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    locked_by_task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    current_url: Mapped[str] = mapped_column(String(2000), default="", nullable=False)
    current_title: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    start_url: Mapped[str] = mapped_column(String(2000), default="", nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    storage_state_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONType, default=dict, nullable=False)


class Platform(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "platforms"

    key: Mapped[str] = mapped_column(String(60), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(40), default="video", nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    adapter_available: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)


# ------------------------------------------------------------------------------ agent
class AgentRun(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "agent_runs"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True, nullable=True
    )
    scene_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("scenes.id", ondelete="SET NULL"), nullable=True
    )
    browser_session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    goal: Mapped[str] = mapped_column(Text, default="", nullable=False)
    mode: Mapped[str] = mapped_column(String(30), default="adapter", nullable=False)
    model: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="running", nullable=False)
    steps_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    actions: Mapped[list[AgentAction]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="AgentAction.index"
    )


class AgentAction(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "agent_actions"

    agent_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True, nullable=True
    )
    index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tool: Mapped[str] = mapped_column(String(80), nullable=False)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, default="", nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    screenshot_path: Mapped[str] = mapped_column(String(600), default="", nullable=False)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)

    run: Mapped[AgentRun] = relationship(back_populates="actions")


# ------------------------------------------------------------------------------ files
class GeneratedFile(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "generated_files"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), index=True, nullable=True
    )
    scene_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("scenes.id", ondelete="SET NULL"), nullable=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    path: Mapped[str] = mapped_column(String(600), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), default="video/mp4", nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    platform_key: Mapped[str] = mapped_column(String(60), default="", nullable=False)
    prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    generation_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="verified", nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONType, default=dict, nullable=False)


class Screenshot(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "screenshots"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True, nullable=True
    )
    browser_session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    scene_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    stage: Mapped[str] = mapped_column(String(60), nullable=False)
    path: Mapped[str] = mapped_column(String(600), nullable=False)
    url: Mapped[str] = mapped_column(String(2000), default="", nullable=False)
    title: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


# --------------------------------------------------------------------------- activity
class ActivityLog(Base, UUIDMixin):
    __tablename__ = "activity_logs"
    __table_args__ = (Index("ix_activity_org_created", "organization_id", "created_at"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True, nullable=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    browser_session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    scene_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    level: Mapped[str] = mapped_column(String(10), default="info", nullable=False)
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Notification(Base, UUIDMixin):
    __tablename__ = "notifications"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=True
    )
    type: Mapped[str] = mapped_column(String(60), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), default="info", nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class MemoryFact(Base, UUIDMixin, TimestampMixin):
    """A fact the assistant remembers for an organization (JARVIS-style recallable memory).

    Facts are plain text, user-visible and user-deletable. They are injected into the
    agent's context when relevant and never contain credentials (the API refuses obvious
    secrets)."""

    __tablename__ = "memory_facts"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )
    category: Mapped[str] = mapped_column(String(40), default="notes", nullable=False)
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(40), default="user", nullable=False)


class Template(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "templates"

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=True
    )
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    kind: Mapped[str] = mapped_column(String(40), default="browser_workflow", nullable=False)
    icon: Mapped[str] = mapped_column(String(40), default="workflow", nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class AuditLog(Base, UUIDMixin):
    __tablename__ = "audit_logs"

    organization_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True, nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True, nullable=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(60), default="", nullable=False)
    resource_id: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    ip_address: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    user_agent: Mapped[str] = mapped_column(String(400), default="", nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
