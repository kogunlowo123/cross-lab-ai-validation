"""Domain errors mapped to HTTP status codes and OAuth-style error bodies by the API layer."""

from __future__ import annotations


class AssuranceError(Exception):
    code = "invalid_request"
    status = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidAggregateError(AssuranceError):
    """Submitted aggregate statistics do not match the registered model specification."""

    code = "invalid_aggregate"
    status = 422


class InsufficientDataError(AssuranceError):
    """A statistic is undefined for the data supplied (for example no positive cases)."""

    code = "insufficient_data"
    status = 422


class UnsafeTextError(AssuranceError):
    """Free text contained patient identifiers or credential material."""

    code = "unsafe_text"
    status = 422


class NotFoundError(AssuranceError):
    code = "not_found"
    status = 404


class ConflictError(AssuranceError):
    code = "conflict"
    status = 409


class InvalidStateError(AssuranceError):
    code = "invalid_state"
    status = 409


class UnauthorizedError(AssuranceError):
    code = "unauthorized"
    status = 401


class ForbiddenError(AssuranceError):
    code = "forbidden"
    status = 403


class ApprovalRequiredError(AssuranceError):
    """Policy requires a human decision; the caller may not perform the action itself."""

    code = "approval_required"
    status = 403


class DependencyUnavailableError(AssuranceError):
    """A dependency needed to make a safe decision is unavailable; the request fails closed."""

    code = "temporarily_unavailable"
    status = 503
