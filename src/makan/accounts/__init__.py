from makan.accounts.errors import (
    AccountError,
    AccountGone,
    AuthUnavailable,
    InvalidProfile,
    InvalidToken,
)
from makan.accounts.profile import MAX_DISPLAY_NAME_CHARS, clean_display_name
from makan.accounts.service import Accounts, AuthAdmin
from makan.accounts.store import AccountStore, InMemoryAccountStore
from makan.accounts.taste import Taste

# The Postgres store, the token verifier, and the Supabase admin client are in
# `makan.accounts.postgres`, `.tokens`, and `.supabase`, so importing this package, as the solo
# workflow does, never needs psycopg or PyJWT.

__all__ = [
    "MAX_DISPLAY_NAME_CHARS",
    "AccountError",
    "AccountGone",
    "AccountStore",
    "Accounts",
    "AuthAdmin",
    "AuthUnavailable",
    "InMemoryAccountStore",
    "InvalidProfile",
    "InvalidToken",
    "Taste",
    "clean_display_name",
]
