"""The account stores behave the same in memory and on Postgres, including cascade and export.

The Postgres variants run when `MAKAN_TEST_DATABASE_URL` is set, and skip otherwise.
"""

from typing import Any
from uuid import uuid4

import pytest

from makan.accounts import Accounts, Taste
from makan.memory import Memory, Owner
from tests.auth_helpers import FakeSupabase, services


def accounts_on(account_store: Any) -> tuple[Accounts, FakeSupabase]:
    built, fake, _ = services(store=None)
    return Accounts(account_store, Memory(account_store.memory), built.accounts.admin), fake


def test_profile_upsert_and_opt_in_default(account_store: Any) -> None:
    accounts, fake = accounts_on(account_store)
    user = fake.sign_up()
    accounts.ensure_user(user)

    created = accounts.save_profile(user, display_name="Bob")
    assert (created.display_name, created.location_history_opt_in) == ("Bob", False)

    on = accounts.save_profile(user, display_name="Bobby", location_history_opt_in=True)
    assert on.location_history_opt_in is True
    kept = accounts.save_profile(user, display_name="Rob")
    assert (kept.display_name, kept.location_history_opt_in) == ("Rob", True)
    assert kept.created_at == created.created_at
    assert kept.updated_at >= created.updated_at
    assert accounts.profile(user) == kept


def test_the_user_row_is_made_once(account_store: Any) -> None:
    _, fake = accounts_on(account_store)
    user = fake.sign_up()
    assert not account_store.user_exists(user)
    account_store.create_user(user)
    account_store.create_user(user)
    assert account_store.user_exists(user)


def test_forget_deletes_a_facts_whole_history(account_store: Any) -> None:
    accounts, fake = accounts_on(account_store)
    user = fake.sign_up()
    accounts.ensure_user(user)
    owner = Owner(user_id=user)
    other = fake.sign_up()
    accounts.ensure_user(other)

    accounts.memory.remember(owner, "cuisine_like", {"cuisine": "thai"})
    now = accounts.memory.remember(owner, "cuisine_dislike", {"cuisine": "thai"}).fact
    mine = accounts.memory.remember(Owner(user_id=other), "cuisine_like", {"cuisine": "thai"}).fact

    assert accounts.memory.forget(other, now.id) is False  # not theirs
    assert accounts.memory.forget(user, now.id) is True
    assert accounts.memory.forget(user, now.id) is False

    assert accounts.export(user)["memory_facts"] == []  # the superseded like went with it
    assert [r.fact.id for r in accounts.memory.recall(Owner(user_id=other))] == [mine.id]


def test_export_and_delete_cover_every_table(
    account_store: Any, request: pytest.FixtureRequest
) -> None:
    if "postgres" not in request.node.name:
        pytest.skip("the other tables exist only on Postgres")
    db = request.getfixturevalue("db")
    accounts, fake = accounts_on(account_store)
    user, other = fake.sign_up(), fake.sign_up()
    for u in (user, other):
        accounts.ensure_user(u)
        accounts.save_profile(u, display_name="Bob" if u == user else "Alice")
        accounts.save_taste(u, Taste(likes=("thai",), allergies=("peanut",)))
    mine, theirs = uuid4(), uuid4()
    for session, owner in ((mine, user), (theirs, other)):
        db.execute("insert into sessions (id, user_id) values (%s, %s)", (session, owner))
        db.execute(
            "insert into participants (session_id, user_id, is_host, display_name) "
            "values (%s, %s, true, 'x')",
            (session, owner),
        )
        db.execute(
            "insert into trace_events (run_id, seq, v, ts, type, session_id, user_id) "
            "values (%s, 1, 1, now(), 'run_start', %s, %s)",
            (f"run-{session}", session, owner),
        )
    guest_session = uuid4()
    db.execute("insert into sessions (id) values (%s)", (guest_session,))
    db.execute("insert into participants (session_id, is_host) values (%s, true)", (guest_session,))

    data = accounts.export(user)

    assert data["user"]["id"] == str(user)
    assert data["profile"]["display_name"] == "Bob"
    assert len(data["memory_facts"]) == 2
    assert [s["id"] for s in data["sessions"]] == [str(mine)]
    assert len(data["participants"]) == len(data["trace_events"]) == 1
    assert "Alice" not in str(data)

    accounts.delete(user)

    counts = {
        table: db.execute(f"select count(*) from {table} where {column} = %s", (user,)).fetchone()[
            0
        ]
        for table, column in (
            ("users", "id"),
            ("profiles", "user_id"),
            ("memory_facts", "user_id"),
            ("sessions", "user_id"),
            ("participants", "user_id"),
            ("trace_events", "user_id"),
        )
    }
    assert counts == dict.fromkeys(counts, 0)
    assert (
        db.execute("select count(*) from participants where session_id = %s", (mine,)).fetchone()[0]
        == 0
    )
    assert (
        db.execute("select count(*) from memory_facts where user_id = %s", (other,)).fetchone()[0]
        == 2
    )
    assert (
        db.execute("select count(*) from sessions where id = %s", (guest_session,)).fetchone()[0]
        == 1
    )
