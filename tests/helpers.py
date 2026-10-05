import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from makan.places import Place
from makan.tools import Tool

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS.glob("*.sql"))


def _echo(arguments: dict[str, Any]) -> str:
    return str(arguments["text"])


ECHO = Tool(
    name="echo",
    description="Return the given text unchanged.",
    parameters={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
    run=_echo,
)


def _boom(arguments: dict[str, Any]) -> str:
    raise RuntimeError("the kitchen is on fire")


BOOM = Tool(
    name="boom",
    description="Always fails.",
    parameters={"type": "object", "properties": {}},
    run=_boom,
)


# Places

FIXTURES = Path(__file__).parent / "fixtures"
KLCC = (3.148, 101.695)  # the center of the Overture fixture, rounded the way the tool rounds


def overture_rows() -> list[dict[str, Any]]:
    """Real Overture places near Kuala Lumpur city center, shaped like the provider's SQL output."""
    rows: list[dict[str, Any]] = json.loads((FIXTURES / "overture_kl.json").read_text("utf-8"))
    return rows


def place(name: str, category: str, lat: float, lon: float, *labels: str) -> Place:
    return Place(
        id=name.lower().replace(" ", "-"),
        name=name,
        category=category,
        categories=(*labels, category),
        lat=lat,
        lon=lon,
    )


class InterleavingStore:
    """Wraps a session store and runs a hook just before one of its methods, or just after a read.

    A hook stands for something that lands after the service checked the session and before the
    write, such as the host closing it or the clock reaching `expires_at`, so a test can force the
    exact interleaving that is otherwise a race. `before["name"]` runs before that method, and
    `before["after_get_by_token"]` or `["after_get_participant"]` runs after that read returns,
    which is the last thing the service does before it asks for the write. Each hook runs once.
    """

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.before: dict[str, Callable[[], object]] = {}

    def _hook(self, name: str) -> None:
        hook = self.before.pop(name, None)
        if hook is not None:
            hook()

    def create(self, session: Any, host: Any) -> None:
        self._hook("create")
        self.inner.create(session, host)

    def get_by_token(self, link_token: Any) -> Any:
        self._hook("get_by_token")
        found = self.inner.get_by_token(link_token)
        self._hook("after_get_by_token")
        return found

    def get_participant(self, participant_id: Any) -> Any:
        self._hook("get_participant")
        found = self.inner.get_participant(participant_id)
        self._hook("after_get_participant")
        return found

    def participants(self, session_id: Any) -> Any:
        self._hook("participants")
        return self.inner.participants(session_id)

    def add_participant(self, participant: Any, *, limit: int, now: Any) -> Any:
        self._hook("add_participant")
        return self.inner.add_participant(participant, limit=limit, now=now)

    def update_participant(self, participant_id: Any, **kwargs: Any) -> Any:
        self._hook("update_participant")
        return self.inner.update_participant(participant_id, **kwargs)

    def close(self, session_id: Any, now: Any) -> Any:
        self._hook("close")
        return self.inner.close(session_id, now)

    def purge_expired(self, now: Any) -> int:
        self._hook("purge_expired")
        count: int = self.inner.purge_expired(now)
        return count


def synthetic_database_url(password: str) -> str:
    """A connection URL that carries `password`, built at run time from separate pieces.

    Tests that check a secret never reaches a message need a URL with a credential in it, but no
    complete credential-bearing URL should be committed, so it is assembled here and never written
    out as one literal.
    """
    scheme, user, host = "postgresql", "makan_user", "db.invalid:5432/none"
    return "://".join([scheme, "@".join([":".join([user, password]), host])])
