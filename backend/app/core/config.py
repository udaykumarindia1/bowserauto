"""Application configuration.

All runtime configuration is read from environment variables (or a `.env` file in
development). Nothing security-sensitive is ever hard-coded here; in production the
process refuses to start when critical secrets are missing or left at their defaults.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["development", "test", "staging", "production"]

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_DIR / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------ general
    app_name: str = "BowserAuto"
    app_env: Environment = "development"
    debug: bool = False
    log_level: str = "INFO"
    log_json: bool = False
    public_base_url: str = "http://localhost:3000"
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # ----------------------------------------------------------------- security
    secret_key: str = Field(default="dev-insecure-secret-key-change-me")
    encryption_key: str | None = None  # urlsafe base64 32-byte Fernet key
    session_ttl_hours: int = 24 * 7
    cookie_name: str = "ba_session"
    csrf_cookie_name: str = "ba_csrf"
    cookie_secure: bool | None = None  # None => derived from environment
    cookie_domain: str | None = None
    allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"])
    trusted_hosts: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["*"])
    rate_limit_per_minute: int = 300
    auth_rate_limit_per_minute: int = 20
    allow_registration: bool = True
    signed_url_ttl_seconds: int = 900

    # ----------------------------------------------------------------- database
    database_url: str = "sqlite+aiosqlite:///./data/bowserauto.db"
    database_echo: bool = False
    auto_migrate: bool = True  # run alembic upgrade head on API start

    # ------------------------------------------------------------- redis/queue
    redis_url: str | None = None
    embedded_worker: bool | None = None  # None => True when redis is not configured
    worker_capabilities: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["browser", "general"])
    worker_poll_interval_seconds: float = 1.0
    worker_heartbeat_seconds: float = 5.0
    worker_stale_after_seconds: int = 45
    max_concurrent_tasks_per_worker: int = 1

    # ------------------------------------------------------------------ storage
    data_dir: Path = Path("./data")
    max_upload_size_mb: int = 50
    max_generated_file_size_mb: int = 2048
    allowed_upload_extensions: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".mov", ".webm", ".txt", ".md", ".json", ".wav", ".mp3"]
    )

    # ------------------------------------------------------------------ browser
    browser_backend: Literal["playwright", "fake"] = "playwright"  # "fake" = in-memory browser for tests/CI
    chromium_executable_path: str | None = None
    chromium_lib_path: str | None = None  # extra LD_LIBRARY_PATH entries for bundled chromium builds
    browser_headless: bool = True
    browser_extra_args: Annotated[list[str], NoDecode] = Field(default_factory=list)
    browser_viewport_width: int = 1280
    browser_viewport_height: int = 800
    browser_launch_timeout_seconds: int = 60
    session_idle_timeout_seconds: int = 20 * 60
    session_expiry_days: int = 30
    max_browser_sessions_per_org: int = 10
    stream_max_fps: int = 8
    stream_quality: int = 55
    stream_max_width: int = 1280
    mock_platform_url: str | None = None  # browser-reachable URL of the built-in mock platform

    # --------------------------------------------------------------------- tasks
    max_task_duration_seconds: int = 6 * 3600
    max_retries_default: int = 3
    max_retries_limit: int = 10
    max_scenes_per_task: int = 200
    generation_timeout_seconds: int = 15 * 60
    action_timeout_seconds: int = 30
    agent_max_steps: int = 40

    # ----------------------------------------------------------------------- llm
    llm_provider: Literal["none", "openai", "anthropic"] = "none"
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_base_url: str | None = None
    llm_timeout_seconds: int = 90

    # ------------------------------------------------------------- notifications
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_tls: bool = True
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    notification_webhook_url: str | None = None

    # ------------------------------------------------------------------ helpers
    @field_validator("allowed_origins", "trusted_hosts", "worker_capabilities", "browser_extra_args", "allowed_upload_extensions", mode="before")
    @classmethod
    def _split_csv(cls, value):  # noqa: ANN001
        """Accept ``a,b,c`` as well as a JSON list in environment variables."""
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                import json

                try:
                    return [str(v) for v in json.loads(text)]
                except ValueError:
                    pass
            return [v.strip() for v in text.split(",") if v.strip()]
        return value

    @model_validator(mode="after")
    def _validate_production(self) -> Settings:
        if self.app_env == "production":
            problems = []
            if self.secret_key.startswith("dev-insecure") or len(self.secret_key) < 32:
                problems.append("SECRET_KEY must be set to a random value of at least 32 characters")
            if not self.encryption_key:
                problems.append("ENCRYPTION_KEY must be set (generate one with `python -m app.core.security gen-key`)")
            if self.database_url.startswith("sqlite"):
                problems.append("DATABASE_URL must point to PostgreSQL in production")
            if problems:
                raise ValueError("Refusing to start in production: " + "; ".join(problems))
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_test(self) -> bool:
        return self.app_env == "test"

    @property
    def cookies_secure(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.app_env in ("staging", "production")

    @property
    def worker_is_embedded(self) -> bool:
        if self.embedded_worker is not None:
            return self.embedded_worker
        return self.redis_url is None

    @property
    def fernet_key(self) -> bytes:
        """Return a valid Fernet key.

        In production an explicit ENCRYPTION_KEY is mandatory (validated above). In other
        environments we derive a stable key from SECRET_KEY so that developers get a working
        setup without extra configuration.
        """
        if self.encryption_key:
            return self.encryption_key.encode()
        digest = hashlib.sha256(("encryption:" + self.secret_key).encode()).digest()
        return base64.urlsafe_b64encode(digest)

    @property
    def resolved_data_dir(self) -> Path:
        path = self.data_dir if self.data_dir.is_absolute() else (BACKEND_DIR / self.data_dir)
        return path.resolve()

    @property
    def projects_dir(self) -> Path:
        return self.resolved_data_dir / "projects"

    @property
    def profiles_dir(self) -> Path:
        return self.resolved_data_dir / "profiles"

    @property
    def screenshots_dir(self) -> Path:
        return self.resolved_data_dir / "screenshots"

    @property
    def tmp_dir(self) -> Path:
        return self.resolved_data_dir / "tmp"

    @property
    def effective_mock_platform_url(self) -> str:
        return self.mock_platform_url or f"http://127.0.0.1:{self.api_port}/mock-platform/"

    @property
    def sqlalchemy_database_url(self) -> str:
        url = self.database_url
        # Normalise plain postgres URLs to the asyncpg driver.
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        if url.startswith("sqlite+aiosqlite:///./"):
            rel = url[len("sqlite+aiosqlite:///./"):]
            url = f"sqlite+aiosqlite:///{(BACKEND_DIR / rel).resolve()}"
        return url


def generate_secret(length: int = 48) -> str:
    return secrets.token_urlsafe(length)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()


def ensure_directories(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    for path in (settings.resolved_data_dir, settings.projects_dir, settings.profiles_dir, settings.screenshots_dir, settings.tmp_dir):
        path.mkdir(parents=True, exist_ok=True)
    # Browser profiles contain authenticated cookies; keep them private to the service user.
    try:
        os.chmod(settings.profiles_dir, 0o700)
    except OSError:
        pass
