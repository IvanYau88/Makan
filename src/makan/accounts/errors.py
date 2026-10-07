"""What can go wrong with an account. Messages here are safe to show the person."""

from __future__ import annotations


class AccountError(Exception):
    """Base class, so a route can catch every account failure."""


class InvalidProfile(AccountError, ValueError):
    """What the person typed cannot be saved. The message says what to change."""


class AccountGone(AccountError):
    """The token is valid but the account behind it was deleted."""


class InvalidToken(AccountError):
    """The access token is missing a claim, expired, or not signed by the project."""

    def __init__(self, message: str, *, expired: bool = False) -> None:
        super().__init__(message)
        self.expired = expired


class AuthUnavailable(AccountError):
    """Supabase could not be reached, so a token could not be checked or an account removed."""
