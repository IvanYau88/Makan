"""Accounts over HTTP: who a request is for, the profile and taste endpoints, export, and delete.

Signing up, signing in, and signing out happen between the browser and Supabase Auth. This side
only checks the access token the browser then sends as `Authorization: Bearer <token>`.

- `optional_identity` is for the search endpoints. No header is a guest and nothing changes for
  them. A header with a token that does not check out is a 401, and not a silent downgrade to a
  guest, so a person whose session ran out is told to sign in again and does not just lose
  their remembered taste without knowing.
- `required_identity` is for the `/api/me` endpoints.
- With no accounts configured, the endpoints are not registered and every request is a guest.
"""

import functools
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from makan.accounts import AccountGone, Accounts, AuthUnavailable, InvalidProfile, InvalidToken
from makan.accounts.postgres import PostgresAccountStore
from makan.accounts.profile import MAX_RAW_NAME_CHARS
from makan.accounts.supabase import SupabaseAdmin
from makan.accounts.taste import MAX_TERM_CHARS, Taste
from makan.accounts.tokens import Identity, TokenVerifier
from makan.config import Config, ConfigError, SupabaseConfig
from makan.memory import Memory
from makan.memory.postgres import PostgresMemoryStore
from makan.models import Profile
from makan.visits import VisitError, Visits
from makan.visits.postgres import PostgresVisitStore
from makan.web.errors import ApiError, error

log = logging.getLogger("makan.web")

MAX_RAW_TERMS = 100  # entries accepted in one list before the service applies its own limit

RawTerm = Annotated[str, StringConstraints(max_length=MAX_TERM_CHARS * 5)]
RawTerms = Annotated[list[RawTerm], Field(max_length=MAX_RAW_TERMS)]


@dataclass(frozen=True)
class AccountServices:
    """Everything accounts need, built once at startup."""

    accounts: Accounts
    verifier: TokenVerifier
    supabase: SupabaseConfig
    visits: Visits

    def public_config(self) -> dict[str, Any]:
        """What the browser needs to talk to Supabase Auth. The anon key is public by design,
        and the service role key is never part of this."""
        return {
            "url": self.supabase.url,
            "anon_key": self.supabase.anon_key,
            "redirect_url": self.supabase.redirect_url or None,
        }


def build_account_services(config: Config) -> AccountServices | None:
    """Accounts on Postgres and Supabase when `SUPABASE_URL` is set, otherwise None.

    The connection is opened here, so a wrong URL fails at startup with no URL in the message.
    """
    supabase = config.supabase
    if supabase is None:
        return None
    try:
        import psycopg
    except ImportError as exc:
        raise ConfigError(
            'SUPABASE_URL is set but psycopg is not installed; run: pip install ".[postgres]"'
        ) from exc
    try:
        conn = psycopg.connect(config.database_url, autocommit=True)
    except psycopg.Error as exc:
        raise ConfigError(
            f"could not connect to MAKAN_DATABASE_URL ({type(exc).__name__})"
        ) from None
    lock = threading.Lock()  # one connection, so the stores take turns
    account_store = PostgresAccountStore(conn, lock=lock)
    accounts = Accounts(
        account_store,
        Memory(PostgresMemoryStore(conn, lock=lock)),
        SupabaseAdmin(supabase.url, supabase.service_role_key),
    )
    visits = Visits(PostgresVisitStore(conn, lock=lock), account_store)
    return AccountServices(
        accounts, TokenVerifier(supabase.jwks_url, supabase.issuer), supabase, visits
    )


def bearer_token(authorization: str | None) -> str | None:
    if authorization is None:
        return None
    scheme, _, token = authorization.partition(" ")
    return (token.strip() or None) if scheme.lower() == "bearer" else None


def optional_identity(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> Identity | None:
    """For the search endpoints. A guest, or any request while accounts are off, is None."""
    if services_of(request) is None or authorization is None:
        return None
    return _verified(request, authorization)


def required_identity(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> Identity:
    """For `/api/me`. There is no way to be a guest here."""
    return _verified(request, authorization)


def services_of(request: Request) -> AccountServices | None:
    """The app's accounts, which `create_app` keeps in `app.state.accounts`."""
    services: AccountServices | None = request.app.state.accounts
    return services


def _verified(request: Request, authorization: str | None) -> Identity:
    services = services_of(request)
    token = bearer_token(authorization)
    if services is None:
        raise ApiError(404, "not_found", "Not found.")
    if token is None:
        raise ApiError(401, "invalid_token", _SIGN_IN_AGAIN, _CHALLENGE)
    try:
        return services.verifier.verify(token)
    except InvalidToken as exc:
        code = "token_expired" if exc.expired else "invalid_token"
        raise ApiError(401, code, _SIGN_IN_AGAIN, _CHALLENGE) from None
    except AuthUnavailable:
        raise _unavailable() from None


_SIGN_IN_AGAIN = "Your session ended. Sign in again to continue."
_CHALLENGE = {"WWW-Authenticate": "Bearer"}


def _unavailable() -> ApiError:
    return ApiError(
        503, "auth_unavailable", "Sign-in is unavailable right now. Try again in a minute."
    )


class ProfileBody(BaseModel):
    """The name is required on every save, because a profile cannot exist without one."""

    model_config = ConfigDict(extra="forbid")

    display_name: Annotated[str, StringConstraints(max_length=MAX_RAW_NAME_CHARS)]
    location_history_opt_in: bool | None = None


class TasteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    likes: RawTerms = []
    dislikes: RawTerms = []
    allergies: RawTerms = []
    diets: RawTerms = []
    never_places: RawTerms = []


def _profile_json(profile: Profile | None) -> dict[str, Any] | None:
    if profile is None:
        return None
    return {
        "display_name": profile.display_name,
        "location_history_opt_in": profile.location_history_opt_in,
        "created_at": profile.created_at.isoformat(),
        "updated_at": profile.updated_at.isoformat(),
    }


def register_account_routes(app: FastAPI, services: AccountServices) -> None:
    """Add `/api/me` and its error handling to `app`, before any static mount."""
    accounts = services.accounts
    router = APIRouter(prefix="/api/me")

    @app.exception_handler(InvalidProfile)
    async def invalid_profile(_: Any, exc: InvalidProfile) -> JSONResponse:
        return error(422, "invalid_request", str(exc))

    @app.exception_handler(AccountGone)
    async def account_gone(_: Any, exc: AccountGone) -> JSONResponse:
        return error(401, "account_deleted", "This account was deleted. Create a new one.")

    @app.exception_handler(AuthUnavailable)
    async def auth_unavailable(_: Any, exc: AuthUnavailable) -> JSONResponse:
        problem = _unavailable()
        return error(problem.status, problem.code, str(problem))

    def current(identity: Annotated[Identity, Depends(required_identity)]) -> Identity:
        """The signed-in person, with their `users` row made on the first call."""
        accounts.ensure_user(identity.user_id)
        return identity

    # These are plain functions, which FastAPI runs in its thread pool, so the stores and the
    # blocking calls to Supabase never block the event loop.

    @router.get("")
    @guarded
    def me(identity: Annotated[Identity, Depends(current)]) -> dict[str, Any]:
        return {
            "user": {"id": str(identity.user_id), "email": identity.email},
            "profile": _profile_json(accounts.profile(identity.user_id)),
            "taste": accounts.taste(identity.user_id).to_json(),
        }

    @router.put("/profile")
    @guarded
    def save_profile(
        body: ProfileBody, identity: Annotated[Identity, Depends(current)]
    ) -> dict[str, Any]:
        profile = accounts.save_profile(
            identity.user_id,
            display_name=body.display_name,
            location_history_opt_in=body.location_history_opt_in,
        )
        return {"profile": _profile_json(profile)}

    @router.put("/taste")
    @guarded
    def save_taste(
        body: TasteBody, identity: Annotated[Identity, Depends(current)]
    ) -> dict[str, Any]:
        taste = accounts.save_taste(
            identity.user_id,
            Taste(
                likes=tuple(body.likes),
                dislikes=tuple(body.dislikes),
                allergies=tuple(body.allergies),
                diets=tuple(body.diets),
                never_places=tuple(body.never_places),
            ),
        )
        return {"taste": taste.to_json()}

    @router.get("/export")
    @guarded
    def export(identity: Annotated[Identity, Depends(current)]) -> JSONResponse:
        return JSONResponse(
            accounts.export(identity.user_id),
            headers={
                "Content-Disposition": 'attachment; filename="makan-data.json"',
                "Cache-Control": "no-store",
            },
        )

    @router.delete("")
    @guarded
    def delete(identity: Annotated[Identity, Depends(required_identity)]) -> Response:
        accounts.delete(identity.user_id)
        return Response(status_code=204, headers={"Cache-Control": "no-store"})

    app.include_router(router)


def guarded[**P](route: Callable[P, Any]) -> Callable[P, Any]:
    """Report a failure no handler answers for as the documented 500 error, without its text."""

    @functools.wraps(route)
    def run(*args: P.args, **kwargs: P.kwargs) -> Any:
        try:
            return route(*args, **kwargs)
        except (AccountGone, AuthUnavailable, InvalidProfile, ApiError, VisitError):
            raise
        except Exception:
            log.exception("account route failed")
            return error(500, "server_error", "Something went wrong on our side. Try again.")

    return run
