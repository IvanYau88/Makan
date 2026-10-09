"""What can go wrong with a visit. Messages here are safe to show the person.

Each error carries the HTTP status and code the web channel answers with, so a route needs one
handler for all of them.
"""

from __future__ import annotations


class VisitError(Exception):
    """Base class, so a route can catch every visit failure."""

    status = 400
    code = "visit_error"


class SignInRequired(VisitError):
    """Visits belong to accounts, and a guest has none."""

    status = 401
    code = "sign_in_required"


class InvalidVisit(VisitError, ValueError):
    """What the person sent cannot be saved. The message says what to change."""

    status = 422
    code = "invalid_request"


class NotFound(VisitError):
    """No such row for this person. Another person's row looks exactly the same."""

    status = 404
    code = "not_found"


class Conflict(VisitError):
    """The request is fine but the row is not in a state that allows it."""

    status = 409
    code = "conflict"
