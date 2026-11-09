"""The listed-cuisine policy and its narrow owner-scoped input boundary, entirely offline."""

from dataclasses import replace
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest

from makan.config import Config
from makan.cuisines import CUISINE_IDS
from makan.fit import CLEF_CUISINES, FitAssessment, FitReason, cuisine_fit
from makan.fit_inputs import owner_fit_inputs
from makan.memory.hard import hard_content
from tests.test_memory import make_memory


@pytest.mark.parametrize(
    "score", [True, False, -1, 101, 0.0, 100.0, float("nan"), float("inf"), "50"]
)
def test_scores_reject_non_integer_and_out_of_range_values(score: Any) -> None:
    with pytest.raises(ValueError):
        FitAssessment(score, FitReason.LIKE)


@pytest.mark.parametrize("score", [0, 50, 100])
def test_valid_score_bounds_and_public_allowlist(score: int) -> None:
    assessment = FitAssessment(score, FitReason.LIKE)
    public = assessment.to_json()
    assert public["score"] == score
    assert public["status"] == "rated"
    assert set(public) == {
        "score",
        "status",
        "basis",
        "policy_version",
        "reason",
        "explanation",
        "cached",
    }


def test_null_and_rated_reasons_cannot_disagree() -> None:
    with pytest.raises(ValueError):
        FitAssessment(50)
    with pytest.raises(ValueError):
        FitAssessment(reason=FitReason.LIKE)
    for reason in FitReason:
        if reason not in (FitReason.LIKE, FitReason.DISLIKE, FitReason.MODEL):
            public = FitAssessment(reason=reason).to_json()
            assert public["score"] is None
            assert public["status"] != "rated"
            assert public["explanation"]


def test_opt_in_is_independent_of_the_fixed_answer_scorer() -> None:
    assert Config.from_env({"MAKAN_MODEL": "test"}).fit_enabled is False
    cfg = Config.from_env({"MAKAN_MODEL": "test", "MAKAN_FIT_ENABLED": "1"})
    assert cfg.fit_enabled and cfg.scorer.backend == "none"
    assert cuisine_fit(("thai",), ("thai",), ()).reason == FitReason.DISABLED
    assert set(CLEF_CUISINES) < CUISINE_IDS


@pytest.mark.parametrize(
    ("cuisines", "likes", "dislikes", "score", "reason"),
    [
        (("thai",), (), (), None, FitReason.NO_TASTE),
        (("thai",), ("thai",), (), 100, FitReason.LIKE),
        (("thai",), (), ("thai",), 0, FitReason.DISLIKE),
        (("thai",), ("thai",), ("italian",), 100, FitReason.LIKE),
        (("italian",), ("thai",), ("italian",), 0, FitReason.DISLIKE),
        (("thai",), ("thai",), ("thai",), None, FitReason.CONFLICT),
        (("thai",), ("thai", "private arbitrary prose"), (), None, FitReason.UNSUPPORTED),
        ((), ("thai",), (), None, FitReason.NO_CUISINE),
        (("thai",), ("japanese",), (), None, FitReason.NO_MATCH),
        (("ramen",), ("japanese",), (), None, FitReason.NO_MATCH),
        (("greek",), ("mediterranean",), (), None, FitReason.NO_MATCH),
        # Wider cuisines on both sides now meet.
        (("burgers",), ("burgers",), (), 100, FitReason.LIKE),
        (("seafood",), (), ("seafood",), 0, FitReason.DISLIKE),
        (("mediterranean",), ("mediterranean",), (), 100, FitReason.LIKE),
        # The source's own parent category counts: its pizza place is Italian.
        (("pizza", "italian"), ("italian",), (), 100, FitReason.LIKE),
        (("pizza", "italian"), (), ("pizza",), 0, FitReason.DISLIKE),
        # A like and a skip on one place leave it Unrated, whatever the order.
        (("pizza", "italian"), ("pizza",), ("italian",), None, FitReason.MIXED),
    ],
)
def test_baseline_matches_only_cuisines_the_place_lists(
    cuisines: tuple[str, ...],
    likes: tuple[str, ...],
    dislikes: tuple[str, ...],
    score: int | None,
    reason: FitReason,
) -> None:
    result = cuisine_fit(cuisines, likes, dislikes, enabled=True)
    assert (result.score, result.reason) == (score, reason)
    assert cuisine_fit(cuisines[::-1], likes[::-1], dislikes[::-1], enabled=True) == result


def test_saved_free_text_tastes_keep_working_and_read_as_vocabulary_terms() -> None:
    memory, _, _, owner = make_memory()
    assert owner.user_id is not None
    for text in ("thai", "Ramen", "burgers", "BBQ"):
        memory.remember(owner, "cuisine_like", {"cuisine": text})
    memory.remember(owner, "cuisine_dislike", {"cuisine": "Pizza"})
    inputs = owner_fit_inputs(memory, owner.user_id)
    assert sorted(inputs.likes) == ["barbecue", "burgers", "ramen", "thai"]
    assert inputs.dislikes == ("pizza",) and inputs.problem is None
    assert cuisine_fit(("barbecue",), inputs.likes, inputs.dislikes, enabled=True).score == 100
    assert cuisine_fit(("pizza", "italian"), inputs.likes, inputs.dislikes, enabled=True).score == 0


def test_saved_text_naming_no_cuisine_still_makes_the_whole_fit_unsupported() -> None:
    memory, _, _, owner = make_memory()
    assert owner.user_id is not None
    memory.remember(owner, "cuisine_like", {"cuisine": "thai"})
    memory.remember(owner, "cuisine_like", {"cuisine": "spicy noodles"})
    inputs = owner_fit_inputs(memory, owner.user_id)
    assert "spicy noodles" in inputs.likes
    result = cuisine_fit(("thai",), inputs.likes, inputs.dislikes, enabled=True)
    assert result.reason == FitReason.UNSUPPORTED


def test_owner_inputs_do_not_include_other_owners_observations_or_ratings() -> None:
    memory, _, _, owner = make_memory()
    assert owner.user_id is not None
    other = replace(owner, user_id=uuid4())
    memory.remember(owner, "cuisine_like", {"cuisine": "thai"})
    memory.remember(owner, "cuisine_like", {"cuisine": "mexican"}, source="observed")
    memory.remember(owner, "place_rating", {"place_id": "private", "rating": 5})
    memory.remember(other, "cuisine_dislike", {"cuisine": "thai"})
    inputs = owner_fit_inputs(memory, owner.user_id)
    assert inputs.likes == ("thai",) and inputs.dislikes == ()
    assert inputs.problem is None


def test_stale_facts_abstain_until_reconfirmed_and_forgotten_tastes_disappear() -> None:
    memory, _, clock, owner = make_memory()
    assert owner.user_id is not None
    fact = memory.remember(owner, "cuisine_like", {"cuisine": "thai"}).fact
    clock.now += timedelta(days=400)
    assert owner_fit_inputs(memory, owner.user_id).problem == FitReason.STALE
    memory.confirm(fact.id)
    assert owner_fit_inputs(memory, owner.user_id).likes == ("thai",)
    memory.forget(owner.user_id, fact.id)
    assert owner_fit_inputs(memory, owner.user_id).likes == ()


def test_expired_tastes_and_conflicting_legacy_rows_do_not_become_scores() -> None:
    memory, store, clock, owner = make_memory()
    assert owner.user_id is not None
    fact = memory.remember(
        owner, "cuisine_like", {"cuisine": "thai"}, expires_at=clock.now + timedelta(days=1)
    ).fact
    clock.now += timedelta(days=2)
    assert owner_fit_inputs(memory, owner.user_id).problem == FitReason.STALE
    memory.confirm(fact.id)
    # Simulate conflicting rows from an old writer that bypassed memory's supersession.
    store.add(replace(fact, id=uuid4(), kind="cuisine_dislike", expires_at=None), supersedes=[])
    memory.remember(owner, "cuisine_like", {"cuisine": "thai"})
    # The normal writer repairs the conflict; a direct legacy pair is still rejected.
    current = memory.recall(owner)[0].fact
    store.add(replace(current, id=uuid4(), kind="cuisine_dislike"), supersedes=[])
    inputs = owner_fit_inputs(memory, owner.user_id)
    assert (
        cuisine_fit(("thai",), inputs.likes, inputs.dislikes, enabled=True).reason
        == FitReason.CONFLICT
    )


def test_hard_rules_remain_plain_code_even_with_stale_or_unsupported_soft_tastes() -> None:
    memory, _, clock, owner = make_memory()
    assert owner.user_id is not None
    memory.remember(owner, "cuisine_like", {"cuisine": "thai"})
    for group, term in (("never_place", "Pizza Hut"), ("allergy", "peanut"), ("diet", "vegan")):
        memory.remember(owner, "constraint", hard_content(group, term))  # type: ignore[arg-type]
    clock.now += timedelta(days=400)
    inputs = owner_fit_inputs(memory, owner.user_id)
    assert inputs.problem == FitReason.STALE
    assert inputs.excludes("Pizza Hut Express") and not inputs.excludes("Pizza Palace")
    assert len(inputs.warnings) == 2
    assert all("does not establish safety" in warning for warning in inputs.warnings)
