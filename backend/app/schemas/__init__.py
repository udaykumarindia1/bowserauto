"""Pydantic request/response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------------------- auth
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=256)
    full_name: str = Field(default="", max_length=200)
    organization_name: str | None = Field(default=None, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=256)


class UserOut(ORMModel):
    id: uuid.UUID
    email: str
    full_name: str
    is_active: bool
    is_superuser: bool
    current_organization_id: uuid.UUID | None
    created_at: datetime
    last_login_at: datetime | None
    preferences: dict[str, Any] = {}


class OrganizationOut(ORMModel):
    id: uuid.UUID
    name: str
    slug: str
    settings: dict[str, Any] = {}
    limits: dict[str, Any] = {}
    created_at: datetime


class MembershipOut(BaseModel):
    organization: OrganizationOut
    role: str


class AuthResponse(BaseModel):
    user: UserOut
    organization: OrganizationOut
    role: str
    csrf_token: str
    organizations: list[MembershipOut] = []


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    preferences: dict[str, Any] | None = None


# --------------------------------------------------------------------------- projects
class OutputSettings(BaseModel):
    resolution: str = "1080p"
    aspect_ratio: str = "16:9"
    clip_duration_seconds: float = 8.0
    filename_pattern: str = "scene_{index:03d}{ext}"
    output_folder: str = "generated"
    total_duration_seconds: float | None = None
    parallel_generation: bool = False


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    template_key: str = Field(default="ai_video_generation", max_length=60)
    script_text: str = Field(default="", max_length=200_000)
    visual_style: str = Field(default="cinematic", max_length=100)
    platform_key: str = Field(default="mock", max_length=60)
    browser_session_id: uuid.UUID | None = None
    output_settings: OutputSettings = Field(default_factory=OutputSettings)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    script_text: str | None = Field(default=None, max_length=200_000)
    visual_style: str | None = Field(default=None, max_length=100)
    platform_key: str | None = Field(default=None, max_length=60)
    browser_session_id: uuid.UUID | None = None
    output_settings: OutputSettings | None = None
    status: Literal["active", "archived"] | None = None


class CharacterIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    voice_style: str = Field(default="", max_length=200)
    reference_asset_id: uuid.UUID | None = None


class CharacterOut(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: str
    voice_style: str
    reference_asset_id: uuid.UUID | None
    created_at: datetime


class AssetOut(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID | None
    kind: str
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    created_at: datetime
    metadata_: dict[str, Any] = Field(default={}, validation_alias="metadata_", serialization_alias="metadata")
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class ProjectOut(ORMModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    description: str
    template_key: str
    status: str
    script_text: str
    visual_style: str
    platform_key: str
    browser_session_id: uuid.UUID | None
    output_settings: dict[str, Any]
    folder_path: str
    created_at: datetime
    updated_at: datetime


class ProjectDetail(ProjectOut):
    characters: list[CharacterOut] = []
    scenes: list[SceneOut] = []
    stats: dict[str, Any] = {}


class ProjectSummary(ProjectOut):
    scene_count: int = 0
    completed_scenes: int = 0
    active_task_id: uuid.UUID | None = None
    active_task_status: str | None = None


# ----------------------------------------------------------------------------- scenes
class SceneIn(BaseModel):
    title: str = Field(default="", max_length=200)
    duration_seconds: float = Field(default=8.0, ge=1, le=600)
    characters: list[str] = []
    location: str = Field(default="", max_length=300)
    visual: str = Field(default="", max_length=8000)
    camera: str = Field(default="", max_length=2000)
    dialogue: str = Field(default="", max_length=8000)
    narration: str = Field(default="", max_length=8000)
    prompt: str = Field(default="", max_length=12000)
    negative_prompt: str = Field(default="", max_length=4000)
    platform_key: str | None = Field(default=None, max_length=60)
    reference_asset_ids: list[uuid.UUID] = []


class SceneUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    duration_seconds: float | None = Field(default=None, ge=1, le=600)
    characters: list[str] | None = None
    location: str | None = Field(default=None, max_length=300)
    visual: str | None = Field(default=None, max_length=8000)
    camera: str | None = Field(default=None, max_length=2000)
    dialogue: str | None = Field(default=None, max_length=8000)
    narration: str | None = Field(default=None, max_length=8000)
    prompt: str | None = Field(default=None, max_length=12000)
    negative_prompt: str | None = Field(default=None, max_length=4000)
    platform_key: str | None = Field(default=None, max_length=60)
    reference_asset_ids: list[uuid.UUID] | None = None
    status: Literal["draft", "queued", "skipped"] | None = None


class SceneOut(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    index: int
    code: str
    title: str
    duration_seconds: float
    characters: list[Any]
    location: str
    visual: str
    camera: str
    dialogue: str
    narration: str
    prompt: str
    negative_prompt: str
    platform_key: str | None
    reference_asset_ids: list[Any]
    status: str
    retry_count: int
    last_error: str
    generated_file_id: uuid.UUID | None
    generation_metadata: dict[str, Any]
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime


class SceneReorder(BaseModel):
    scene_ids: list[uuid.UUID]


class AnalyzeScriptRequest(BaseModel):
    script_text: str | None = Field(default=None, max_length=200_000)
    target_duration_seconds: float | None = Field(default=None, ge=1, le=36_000)
    clip_duration_seconds: float | None = Field(default=None, ge=1, le=600)
    replace_existing: bool = True
    use_llm: bool | None = None


class AnalyzeScriptResponse(BaseModel):
    scenes: list[SceneOut]
    method: str
    warnings: list[str] = []


# ------------------------------------------------------------------------------ tasks
class TaskConfig(BaseModel):
    max_retries: int = Field(default=3, ge=0, le=10)
    generation_timeout_seconds: int = Field(default=900, ge=30, le=7200)
    action_timeout_seconds: int = Field(default=30, ge=5, le=300)
    parallel_generation: bool = False
    scene_ids: list[uuid.UUID] | None = None
    retry_failed_only: bool = False
    headless: bool | None = None
    agent_mode: Literal["auto", "adapter", "generic"] = "auto"
    mock_options: dict[str, Any] = {}


class TaskCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    kind: Literal["video_generation", "browser_workflow", "image_generation", "research", "data_entry", "website_testing", "content_creation"] = "video_generation"
    project_id: uuid.UUID | None = None
    instructions: str = Field(default="", max_length=20_000)
    platform_key: str | None = Field(default=None, max_length=60)
    browser_session_id: uuid.UUID | None = None
    priority: int = Field(default=5, ge=1, le=10)
    config: TaskConfig = Field(default_factory=TaskConfig)
    start: bool = True


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    priority: int | None = Field(default=None, ge=1, le=10)
    instructions: str | None = Field(default=None, max_length=20_000)
    config: TaskConfig | None = None


class TaskOut(ORMModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    project_id: uuid.UUID | None
    title: str
    kind: str
    instructions: str
    status: str
    control: str
    human_control: bool
    platform_key: str
    browser_session_id: uuid.UUID | None
    config: dict[str, Any]
    progress: dict[str, Any]
    attention: dict[str, Any]
    current_action: str
    priority: int
    retry_count: int
    worker_id: str | None
    queued_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    paused_at: datetime | None
    error: str
    result: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class TaskStepOut(ORMModel):
    id: uuid.UUID
    scene_id: uuid.UUID | None
    index: int
    name: str
    status: str
    attempt: int
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    result: dict[str, Any]
    error: str


class TaskDetail(TaskOut):
    steps: list[TaskStepOut] = []
    scenes: list[SceneOut] = []
    project_name: str | None = None
    session_name: str | None = None


class SceneActionRequest(BaseModel):
    scene_id: uuid.UUID | None = None


class ReorderQueueRequest(BaseModel):
    task_ids: list[uuid.UUID]


# -------------------------------------------------------------------- browser sessions
class BrowserSessionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    platform_key: str = Field(default="generic", max_length=60)
    start_url: str = Field(default="", max_length=2000)
    browser_type: Literal["chromium"] = "chromium"


class BrowserSessionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    platform_key: str | None = Field(default=None, max_length=60)
    start_url: str | None = Field(default=None, max_length=2000)
    login_status: Literal["unknown", "logged_in", "logged_out"] | None = None


class BrowserSessionOut(ORMModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    platform_key: str
    browser_type: str
    status: str
    desired_state: str
    interactive: bool
    login_status: str
    worker_id: str | None
    locked_by_task_id: uuid.UUID | None
    current_url: str
    current_title: str
    start_url: str
    last_used_at: datetime | None
    last_heartbeat_at: datetime | None
    expires_at: datetime | None
    error: str
    created_at: datetime
    updated_at: datetime
    has_saved_state: bool = False


class NavigateRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2000)

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        value = value.strip()
        if not value.lower().startswith(("http://", "https://", "about:blank")):
            value = "https://" + value
        if value.lower().startswith(("javascript:", "data:", "file:")):
            raise ValueError("unsupported URL scheme")
        return value


# --------------------------------------------------------------------------- platforms
class PlatformOut(BaseModel):
    key: str
    name: str
    category: str
    base_url: str
    adapter_available: bool
    enabled: bool
    capabilities: dict[str, Any] = {}
    supported_actions: list[str] = []
    description: str = ""
    requires_login: bool = True
    notes: str = ""


# --------------------------------------------------------------------------- templates
class TemplateIn(BaseModel):
    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9_\-]+$")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    kind: str = Field(default="browser_workflow", max_length=40)
    icon: str = Field(default="workflow", max_length=40)
    config: dict[str, Any] = {}


class TemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    kind: str | None = Field(default=None, max_length=40)
    icon: str | None = Field(default=None, max_length=40)
    config: dict[str, Any] | None = None


class TemplateOut(ORMModel):
    id: uuid.UUID
    organization_id: uuid.UUID | None
    key: str
    name: str
    description: str
    kind: str
    icon: str
    config: dict[str, Any]
    is_builtin: bool
    created_at: datetime


# ------------------------------------------------------------------------------ files
class GeneratedFileOut(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID | None
    task_id: uuid.UUID | None
    scene_id: uuid.UUID | None
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    duration_seconds: float | None
    width: int | None
    height: int | None
    platform_key: str
    prompt: str
    generation_seconds: float | None
    status: str
    retry_count: int
    created_at: datetime
    metadata_: dict[str, Any] = Field(default={}, validation_alias="metadata_", serialization_alias="metadata")
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class ScreenshotOut(ORMModel):
    id: uuid.UUID
    task_id: uuid.UUID | None
    browser_session_id: uuid.UUID | None
    scene_id: uuid.UUID | None
    stage: str
    url: str
    title: str
    size_bytes: int
    created_at: datetime


class SignedUrlOut(BaseModel):
    url: str
    expires_in: int


# --------------------------------------------------------------------------- activity
class ActivityLogOut(ORMModel):
    id: uuid.UUID
    task_id: uuid.UUID | None
    project_id: uuid.UUID | None
    browser_session_id: uuid.UUID | None
    scene_id: uuid.UUID | None
    level: str
    event_type: str
    message: str
    data: dict[str, Any]
    duration_ms: int | None
    created_at: datetime


class AgentActionOut(ORMModel):
    id: uuid.UUID
    agent_run_id: uuid.UUID
    index: int
    tool: str
    arguments: dict[str, Any]
    reasoning: str
    result: dict[str, Any]
    success: bool
    duration_ms: int
    error: str
    created_at: datetime


class AgentRunOut(ORMModel):
    id: uuid.UUID
    task_id: uuid.UUID | None
    scene_id: uuid.UUID | None
    goal: str
    mode: str
    model: str
    status: str
    steps_count: int
    summary: str
    error: str
    started_at: datetime
    finished_at: datetime | None


class NotificationOut(ORMModel):
    id: uuid.UUID
    type: str
    severity: str
    title: str
    body: str
    data: dict[str, Any]
    read_at: datetime | None
    created_at: datetime


class AuditLogOut(ORMModel):
    id: uuid.UUID
    user_id: uuid.UUID | None
    action: str
    resource_type: str
    resource_id: str
    ip_address: str
    data: dict[str, Any]
    created_at: datetime


# ---------------------------------------------------------------------------- settings
class OrgSettingsUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    settings: dict[str, Any] | None = None
    llm_provider: Literal["inherit", "none", "openai", "anthropic"] | None = None
    llm_model: str | None = Field(default=None, max_length=120)
    llm_base_url: str | None = Field(default=None, max_length=500)
    llm_api_key: str | None = Field(default=None, max_length=500)
    clear_llm_api_key: bool = False
    limits: dict[str, Any] | None = None


class OrgSettingsOut(BaseModel):
    organization: OrganizationOut
    llm: dict[str, Any]
    limits: dict[str, Any]
    effective_limits: dict[str, Any]
    system: dict[str, Any]


class Page(BaseModel):
    items: list[Any]
    total: int
    page: int
    page_size: int


ProjectDetail.model_rebuild()
