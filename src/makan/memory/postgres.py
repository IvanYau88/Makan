"""A `MemoryStore` over the `memory_facts` table, using `psycopg`.

It takes an open connection and does not manage it, so the caller decides on pooling and on
the role: a signed-in user's connection is bound by row-level security, and a guest's session
goes through the backend's privileged connection. The driver is included in the project's `dev`
extra.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from makan.memory.store import Owner
from makan.models import MemoryFact, MemoryKind

try:
    import psycopg
    from psycopg.types.json import Jsonb
except ImportError:  # pragma: no cover - only without the extra
    raise ImportError('psycopg is not installed; run: pip install -e ".[dev]"') from None

_COLUMNS = (
    "id, user_id, session_id, kind, content, source, confidence, "
    "observed_at, last_confirmed_at, created_at, expires_at, superseded_by"
)


class PostgresMemoryStore:
    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self._conn = conn

    def add(self, fact: MemoryFact, *, supersedes: Sequence[UUID] = ()) -> None:
        with self._conn.transaction():
            self._conn.execute(
                f"insert into memory_facts ({_COLUMNS}) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    fact.id,
                    fact.user_id,
                    fact.session_id,
                    fact.kind,
                    Jsonb(fact.content),
                    fact.source,
                    fact.confidence,
                    fact.observed_at,
                    fact.last_confirmed_at,
                    fact.created_at,
                    fact.expires_at,
                    fact.superseded_by,
                ),
            )
            if supersedes:
                updated = self._conn.execute(
                    "update memory_facts set superseded_by = %s "
                    "where id = any(%s) and superseded_by is null",
                    (fact.id, list(supersedes)),
                )
                if updated.rowcount != len(set(supersedes)):
                    # Raising inside the transaction block rolls the insert back too.
                    raise ValueError("a fact to supersede is missing or already superseded")

    def get(self, fact_id: UUID) -> MemoryFact | None:
        row = self._conn.execute(
            f"select {_COLUMNS} from memory_facts where id = %s",
            (fact_id,),
        ).fetchone()
        return None if row is None else _fact(row)

    def active(self, owner: Owner, kinds: Collection[MemoryKind] | None = None) -> list[MemoryFact]:
        owned: list[str] = []
        params: list[Any] = []
        if owner.user_id is not None:
            owned.append("user_id = %s")
            params.append(owner.user_id)
        if owner.session_id is not None:
            owned.append("session_id = %s")
            params.append(owner.session_id)
        sql = (
            f"select {_COLUMNS} from memory_facts "
            f"where superseded_by is null and ({' or '.join(owned)})"
        )
        if kinds is not None:
            sql += " and kind = any(%s)"
            params.append(list(kinds))
        sql += " order by observed_at desc, id"
        return [_fact(row) for row in self._conn.execute(sql, params).fetchall()]

    def reconfirm(self, fact_id: UUID, *, confidence: float, at: datetime) -> MemoryFact | None:
        row = self._conn.execute(
            "update memory_facts set confidence = %s, last_confirmed_at = %s "
            f"where id = %s and superseded_by is null returning {_COLUMNS}",
            (confidence, at, fact_id),
        ).fetchone()
        return None if row is None else _fact(row)


def _fact(row: tuple[Any, ...]) -> MemoryFact:
    return MemoryFact(
        id=row[0],
        user_id=row[1],
        session_id=row[2],
        kind=row[3],
        content=row[4],
        source=row[5],
        confidence=row[6],
        observed_at=row[7],
        last_confirmed_at=row[8],
        created_at=row[9],
        expires_at=row[10],
        superseded_by=row[11],
    )
