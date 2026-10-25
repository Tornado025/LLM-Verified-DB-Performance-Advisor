"""Structured exceptions shared by the backend and API."""


class AdvisorError(Exception):
    """Base application error with safe structured details."""

    http_status = 500
    code = "internal"

    def __init__(self, message: str, detail: dict | None = None):
        super().__init__(message)
        self.detail = {} if detail is None else detail


class NotFound(AdvisorError):
    """Not found error."""

    http_status = 404
    code = "not_found"


class BadRequest(AdvisorError):
    """Bad request error."""

    http_status = 400
    code = "bad_request"


class Conflict(AdvisorError):
    """Conflict error."""

    http_status = 409
    code = "conflict"


class SafetyRejected(AdvisorError):
    """Safety rejected error."""

    http_status = 422
    code = "safety_rejected"


class SandboxError(AdvisorError):
    """Sandbox error error."""

    http_status = 500
    code = "sandbox_error"


class LLMError(AdvisorError):
    """Llm error error."""

    http_status = 502
    code = "llm_error"


class BudgetExceeded(AdvisorError):
    """Budget exceeded error."""

    http_status = 408
    code = "budget_exceeded"
