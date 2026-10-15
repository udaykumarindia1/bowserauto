"""Application error types mapped to HTTP responses."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str = "", *, code: str | None = None, details: dict[str, Any] | None = None) -> None:
        super().__init__(message or self.__class__.__name__)
        self.message = message or self.__class__.__name__
        if code:
            self.code = code
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class PermissionDeniedError(AppError):
    status_code = 403
    code = "forbidden"


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class ValidationFailed(AppError):
    status_code = 422
    code = "validation_failed"


class LimitExceededError(AppError):
    status_code = 429
    code = "limit_exceeded"


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "service_unavailable"


# ------------------------------------------------------------------ automation errors
class AutomationError(Exception):
    """Base class for errors raised during browser automation.

    ``retryable`` tells the executor whether another attempt makes sense.
    """

    retryable = True

    def __init__(self, message: str, *, retryable: bool | None = None, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        if retryable is not None:
            self.retryable = retryable
        self.details = details or {}


class ElementNotFound(AutomationError):
    retryable = True


class GenerationFailed(AutomationError):
    retryable = True


class GenerationTimeout(AutomationError):
    retryable = True


class DownloadFailed(AutomationError):
    retryable = True


class LoginRequired(AutomationError):
    """The platform requires the user to log in manually – never retry blindly."""

    retryable = False


class HumanInterventionRequired(AutomationError):
    """CAPTCHA / MFA / consent dialog detected: a human must complete it."""

    retryable = False


class BrowserCrashed(AutomationError):
    retryable = True


class ControlSignal(BaseException):
    """Base class for cooperative control signals.

    Derived from ``BaseException`` (like ``asyncio.CancelledError``) so that generic
    ``except Exception`` blocks inside adapters/tools never swallow a user's stop/skip request.
    """


class TaskStopped(ControlSignal):
    """Raised inside the executor when the user stops the task."""


class SceneSkipped(ControlSignal):
    """Raised at a checkpoint when the user asked to skip the current scene."""


class SceneRetryRequested(ControlSignal):
    """Raised at a checkpoint when the user asked to restart the current scene."""
