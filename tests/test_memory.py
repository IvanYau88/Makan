"""Behavioral tests for structured memory and its retrieval gate."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import pytest

from makan.loop import run
from makan.memory import (
    DecayPolicy,
    InMemoryStore,
    Memory,
    MemoryPolicy,
    Owner,
    RuleGate,
    recall_for_turn,
    remember_fact,
)
from makan.models import MEMORY_KINDS, MemoryKind
from makan.providers import FakeProvider, ToolCall
from makan.providers.fake import call

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def make_memory() -> tuple[Memory, InMemoryStore, Clock, Owner]:
    store = InMemoryStore()
    clock = Clock()
    return Memory(store, clock=clock), store, clock, Owner(user_id=uuid4())


def test_every_soft_fact_kind_decays_by_age() -> None:
    memory, _, clock, owner = make_memory()
    for kind in MEMORY_KINDS:
        if kind == "constraint":
            continue  # a hard constraint never decays, see the next test
        content = cast(
            dict[str, Any],
            {
                "cuisine_like": {"cuisine": "thai"},
                "cuisine_dislike": {"cuisine": "thai"},
                "constraint": {"key": f"key_{kind}", "value": "value"},
                "place_rating": {"place_id": kind, "rating": 4},
            }[kind],
        )
        fact = memory.remember(owner, cast(MemoryKind, kind), content).fact
        clock.now += timedelta(days=400)
        recalled = next(r for r in memory.recall(owner) if r.fact.id == fact.id)
        assert recalled.confidence < fact.confidence
        assert recalled.stale == "low_confidence"


def test_a_hard_constraint_never_decays_or_goes_stale() -> None:
    memory, _, clock, owner = make_memory()
    fact = memory.remember(owner, "constraint", {"key": "allergy:peanut", "value": True}).fact
    clock.now += timedelta(days=365 * 20)

    recalled = memory.recall(owner)[0]

    assert recalled.confidence == fact.confidence
    assert recalled.stale is None


def test_reconfirm_resets_decay_and_starts_a_new_confirmation_period() -> None:
    memory, store, clock, owner = make_memory()
    remembered = memory.remember(owner, "cuisine_like", {"cuisine": "thai"})
    clock.now += timedelta(days=400)
    assert memory.recall(owner)[0].stale == "low_confidence"

    confirmed = memory.confirm(remembered.fact.id)
    recalled = memory.recall(owner)[0]

    assert confirmed.last_confirmed_at == clock.now
    assert confirmed.confidence == memory.policy.initial_confidence["stated"]
    assert recalled.confidence == confirmed.confidence
    assert recalled.stale is None
    assert store.get(remembered.fact.id) == confirmed


def test_new_contradiction_supersedes_and_links_the_old_fact() -> None:
    memory, store, _, owner = make_memory()
    old = memory.remember(owner, "cuisine_like", {"cuisine": "thai"}).fact
    newer = memory.remember(owner, "cuisine_dislike", {"cuisine": "THAI"})

    assert newer.outcome == "superseded"
    assert newer.superseded == (old,)
    assert store.get(old.id).superseded_by == newer.fact.id  # type: ignore[union-attr]
    assert [r.fact.id for r in memory.recall(owner)] == [newer.fact.id]


@pytest.mark.parametrize(
    ("first", "second"),
    [(True, 1), (1, True), (False, 0), (0, False), (True, 1.0)],
)
def test_boolean_and_number_constraints_are_different_values(first: Any, second: Any) -> None:
    memory, store, _, owner = make_memory()
    old = memory.remember(owner, "constraint", {"key": "allergy_peanut", "value": first}).fact
    newer = memory.remember(owner, "constraint", {"key": "allergy_peanut", "value": second})

    assert newer.outcome == "superseded"
    assert type(newer.fact.content["value"]) is type(second)
    assert store.get(old.id).superseded_by == newer.fact.id  # type: ignore[union-attr]


def test_equal_number_constraints_still_reconfirm() -> None:
    memory, _, _, owner = make_memory()
    memory.remember(owner, "constraint", {"key": "budget_max", "value": 20})

    again = memory.remember(owner, "constraint", {"key": "budget_max", "value": 20.0})

    assert again.outcome == "confirmed"


def test_in_memory_store_reconfirm_refuses_superseded_facts() -> None:
    memory, store, _, owner = make_memory()
    old = memory.remember(owner, "cuisine_like", {"cuisine": "thai"}).fact
    memory.remember(owner, "cuisine_dislike", {"cuisine": "thai"})

    assert store.reconfirm(old.id, confidence=0.9, at=NOW) is None
    assert store.get(old.id).confidence == old.confidence  # type: ignore[union-attr]
    with pytest.raises(ValueError):
        memory.confirm(old.id)


def test_stale_expired_facts_are_returned_as_stale() -> None:
    memory, _, clock, owner = make_memory()
    memory.remember(
        owner,
        "constraint",
        {"key": "allergy_peanut", "value": True},
        expires_at=clock.now + timedelta(days=2),
    )
    clock.now += timedelta(days=3)

    recalled = memory.recall(owner)[0]
    turn = recall_for_turn(memory, RuleGate(), owner, "Find somewhere for lunch")

    assert recalled.stale == "expired"
    assert "STALE (expired)" in (turn.text or "")


def test_default_policy_decays_every_kind_but_constraints_and_allows_overrides() -> None:
    policy = MemoryPolicy()
    assert policy.decay["constraint"].half_life is None
    assert all(
        policy.decay[cast(MemoryKind, kind)].half_life is not None
        for kind in MEMORY_KINDS
        if kind != "constraint"
    )
    configured = dict(policy.decay)
    configured["constraint"] = DecayPolicy(timedelta(days=365))
    custom = MemoryPolicy(decay=configured)
    assert custom.decay["constraint"].half_life == timedelta(days=365)


def test_rule_gate_skips_pleasantries_and_retrieves_for_food_requests() -> None:
    memory, _, _, owner = make_memory()
    gate = RuleGate()

    greeting = recall_for_turn(memory, gate, owner, "Hi, thanks!")
    request = recall_for_turn(memory, gate, owner, "Find a vegetarian place nearby")

    assert greeting.decision.lookup is False
    assert greeting.facts == ()
    assert request.decision.lookup is True


def test_remember_fact_is_available_through_the_existing_agent_tool_interface() -> None:
    memory, store, _, owner = make_memory()
    provider = FakeProvider(
        [
            call(
                ToolCall.of(
                    "remember_fact", "remember", kind="cuisine_like", content={"cuisine": "thai"}
                )
            ),
            call(ToolCall.of("finish", "finish", answer="Got it.")),
        ]
    )

    result = run(
        "I like Thai food",
        provider=provider,
        model="test/model",
        tools=[remember_fact(memory, owner)],
    )

    facts = memory.recall(owner)
    assert result.answer == "Got it."
    assert len(facts) == 1
    assert facts[0].fact.content == {"cuisine": "thai"}
    tool_result = provider.requests[1].messages[-1]
    assert json.loads(tool_result.content or "") == {
        "outcome": "created",
        "kind": "cuisine_like",
        "content": {"cuisine": "thai"},
        "replaced": [],
    }
    assert store.get(facts[0].fact.id) is not None


def test_postgres_store_round_trip_and_atomic_supersession(db: Any) -> None:
    pytest.importorskip("psycopg")
    from makan.memory.postgres import PostgresMemoryStore

    user_id = uuid4()
    db.execute("insert into users (id) values (%s)", (user_id,))
    owner = Owner(user_id=user_id)
    store = PostgresMemoryStore(db)
    memory = Memory(store, clock=Clock())

    first = memory.remember(owner, "cuisine_like", {"cuisine": "thai"}).fact
    second = memory.remember(owner, "cuisine_dislike", {"cuisine": "thai"}).fact

    superseded = store.get(first.id)
    assert superseded is not None and superseded.superseded_by == second.id
    assert [r.fact.id for r in memory.recall(owner)] == [second.id]
    assert store.active(owner)[0].content == {"cuisine": "thai"}
    confirmed = store.reconfirm(second.id, confidence=0.8, at=NOW)
    assert confirmed is not None and confirmed.confidence == 0.8
    assert store.reconfirm(first.id, confidence=0.9, at=NOW) is None
    assert store.get(first.id) == superseded


def test_postgres_store_respects_session_owner(db: Any) -> None:
    pytest.importorskip("psycopg")
    from makan.memory.postgres import PostgresMemoryStore

    session_id = uuid4()
    db.execute("insert into sessions (id) values (%s)", (session_id,))
    owner = Owner(session_id=session_id)
    memory = Memory(PostgresMemoryStore(db), clock=Clock())
    fact = memory.remember(owner, "constraint", {"key": "diet", "value": "vegan"}).fact

    assert fact.session_id == session_id
    assert fact.user_id is None
    assert [r.fact.id for r in memory.recall(owner)] == [fact.id]
