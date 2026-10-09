"""The visit rules, against the in-memory store and, when a database is set, Postgres.

`world` runs every test twice, so the two stores are held to the same behavior.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from makan.visits import (
    TAG_SHARING_NOTICE,
    UNSET,
    Conflict,
    DishAnswer,
    DishInput,
    InvalidVisit,
    NotFound,
    RatingAnswer,
    SignInRequired,
    VisitView,
)
from makan.visits import service as visit_service
from tests.visit_helpers import CURRY, RAMEN, SOUP, START, THAI, World

# Signed in only


def test_a_guest_cannot_do_anything(world: World) -> None:
    v = world.visits
    some = uuid4()
    calls: list[Callable[[], object]] = [
        lambda: v.going_here(None, THAI),
        lambda: v.open_going_here(None),
        lambda: v.due_reminders(None),
        lambda: v.mark_reminded(None, some),
        lambda: v.cancel_going_here(None, some),
        lambda: v.log_visit(None, place=THAI, rating=5, party="solo"),
        lambda: v.visit(None, some),
        lambda: v.history(None),
        lambda: v.edit_visit(None, some, rating=5),
        lambda: v.delete_visit(None, some),
        lambda: v.tag(None, some, some, acknowledged_sharing=True),
        lambda: v.withdraw_tag(None, some, some),
        lambda: v.tag_requests(None),
        lambda: v.accept_tag(None, some),
        lambda: v.decline_tag(None, some),
        lambda: v.confirm(None, some),
    ]
    for call in calls:
        with pytest.raises(SignInRequired):
            call()


# Logging a visit


def test_a_solo_visit_with_no_dishes_is_a_visit(world: World) -> None:
    alice = world.user()
    view = world.visits.log_visit(alice, place=THAI, rating=7, party="solo")

    assert view.dishes == ()
    assert (view.visit.rating, view.visit.rating_origin) == (Decimal("7.0"), "fresh")
    assert view.visit.visited_on == START.date()
    assert view.visit.party == "solo"
    [summary] = world.visits.history(alice)
    assert (summary.visit.id, summary.dish_count) == (view.visit.id, 0)


def test_a_visit_keeps_its_details(world: World) -> None:
    alice = world.user()
    view = world.visit(
        alice,
        rating="9.25",
        description="  Loud,\nbut worth it.  ",
        visited_on=date(2026, 10, 1),
    )
    again = world.visits.visit(alice, view.visit.id)
    assert again.visit.rating == Decimal("9.3")  # half up, to one decimal
    assert again.visit.description == "Loud,\nbut worth it."
    assert again.visit.visited_on == date(2026, 10, 1)
    assert (again.visit.place_source, again.visit.place_id) == ("overture", "g-thai")
    assert (again.visit.place_name, again.visit.place_address) == ("Mid Thai", "1 Jalan Satu")


def test_dishes_keep_their_text_tags_and_order(world: World) -> None:
    alice = world.user()
    dishes = (
        DishInput("  tom   YUM ", 7.5, ["spicy", "not spicy", "Spicy", "lunch", "spicy"], "meh"),
        DishInput("Pad Thai", "5.7"),
        DishInput("Tom Yum", Decimal("10")),
    )
    view = world.visit(alice, dishes=dishes)
    [first, second, third] = world.visits.visit(alice, view.visit.id).dishes

    assert first.name == "tom   YUM"  # verbatim: only the ends are trimmed
    assert first.tags == ("spicy", "not spicy", "Spicy", "lunch", "spicy")
    assert (first.comment, first.rating, first.rating_origin) == ("meh", Decimal("7.5"), "fresh")
    assert (second.name, second.rating, second.tags, second.comment) == (
        "Pad Thai",
        Decimal("5.7"),
        (),
        None,
    )
    assert (third.name, third.rating) == ("Tom Yum", Decimal("10.0"))
    assert [d.position for d in (first, second, third)] == [0, 1, 2]


def test_dish_names_are_kept_as_typed_apart_from_the_ends(world: World) -> None:
    alice = world.user()
    view = world.visit(alice, dishes=(DishInput("  Nasi  Lemak‮ ", 5),))
    assert view.dishes[0].name == "Nasi  Lemak"  # the override is removed, the ends trimmed


def test_a_bad_rating_stores_nothing(world: World) -> None:
    alice = world.user()
    for bad in (10.5, -0.5, "abc", True, None, float("nan"), "", [5]):
        with pytest.raises(InvalidVisit):
            world.visit(alice, rating=bad)
    with pytest.raises(InvalidVisit, match="Tom Yum"):
        world.visit(alice, dishes=(CURRY, DishInput("Tom Yum", 11)))
    assert world.visits.history(alice) == []


def test_other_checks_on_a_new_visit(world: World) -> None:
    alice = world.user()
    with pytest.raises(InvalidVisit):
        world.visit(alice, party="alone")
    with pytest.raises(InvalidVisit):
        world.visit(alice, visited_on=START.date() + timedelta(days=2))
    with pytest.raises(InvalidVisit):
        world.visit(alice, dishes=(DishInput("   ", 5),))
    with pytest.raises(InvalidVisit):
        world.visit(alice, dishes=(DishInput("Soup", 5, ["ok", " "]),))
    with pytest.raises(InvalidVisit):
        world.visit(alice, dishes=(DishInput("x" * 121, 5),))
    with pytest.raises(InvalidVisit):
        world.visit(alice, dishes=(DishInput("Soup", 5, id=uuid4()),))
    assert world.visits.history(alice) == []


def test_two_venues_with_one_name_stay_two_places(world: World) -> None:
    alice = world.user()
    a = world.visit(alice, place=THAI)
    other = THAI.__class__("overture", "g-other", "Mid Thai")
    b = world.visit(alice, place=other)
    foreign = THAI.__class__("google", "g-thai", "Mid Thai")
    c = world.visit(alice, place=foreign)
    keys = {(v.visit.place_source, v.visit.place_id) for v in (a, b, c)}
    assert keys == {("overture", "g-thai"), ("overture", "g-other"), ("google", "g-thai")}


def test_history_is_newest_meal_first(world: World) -> None:
    alice = world.user()
    old = world.visit(alice, visited_on=date(2026, 9, 1))
    new = world.visit(alice, visited_on=date(2026, 10, 2))
    assert [s.visit.id for s in world.visits.history(alice)] == [new.visit.id, old.visit.id]


# Going here


def test_going_here_starts_a_48_hour_window(world: World) -> None:
    alice = world.user()
    marker = world.visits.going_here(alice, THAI)
    assert marker.remind_at - marker.started_at == timedelta(hours=48)
    assert marker.planned_on == START.date()
    assert (marker.place_source, marker.place_id) == ("overture", "g-thai")

    again = world.visits.going_here(alice, THAI)  # the same place twice is one marker
    assert again.id == marker.id
    assert world.visits.going_here(alice, RAMEN).id != marker.id
    assert len(world.visits.open_going_here(alice)) == 2


def test_a_reminder_is_due_once_after_the_window(world: World) -> None:
    alice = world.user()
    marker = world.visits.going_here(alice, THAI)
    world.clock.advance(timedelta(hours=47, minutes=59))
    assert world.visits.due_reminders(alice) == []

    world.clock.advance(timedelta(minutes=1))
    assert [m.id for m in world.visits.due_reminders(alice)] == [marker.id]
    assert [m.id for m in world.visits.due_reminders(alice)] == [marker.id]  # listing sends nothing

    done = world.visits.mark_reminded(alice, marker.id)
    assert done.reminded_at == world.clock.now
    assert world.visits.due_reminders(alice) == []  # one reminder, then dropped
    assert world.visits.mark_reminded(alice, marker.id).reminded_at == done.reminded_at

    world.clock.advance(timedelta(days=30))
    assert world.visits.due_reminders(alice) == []
    assert world.visits.open_going_here(alice) == []


def test_a_reminder_is_dropped_when_it_is_long_overdue(world: World) -> None:
    alice = world.user()
    world.visits.going_here(alice, THAI)
    world.clock.advance(timedelta(hours=48) + visit_service.REMINDER_GRACE - timedelta(seconds=1))
    assert len(world.visits.due_reminders(alice)) == 1
    world.clock.advance(timedelta(seconds=1))
    assert world.visits.due_reminders(alice) == []


def test_a_marked_place_can_start_again_after_its_reminder(world: World) -> None:
    alice = world.user()
    first = world.visits.going_here(alice, THAI)
    world.visits.mark_reminded(alice, first.id)
    assert world.visits.going_here(alice, THAI).id != first.id


def test_logging_from_a_marker_defaults_to_its_day_and_ends_the_reminder(world: World) -> None:
    alice = world.user()
    marker = world.visits.going_here(alice, THAI, planned_on=date(2026, 10, 7))
    world.clock.advance(timedelta(hours=49))
    assert len(world.visits.due_reminders(alice)) == 1

    view = world.visit(alice, going_here_id=marker.id)
    assert view.visit.visited_on == date(2026, 10, 7)
    assert world.visits.due_reminders(alice) == []
    assert world.visits.open_going_here(alice) == []
    with pytest.raises(Conflict):
        world.visit(alice, going_here_id=marker.id)  # one marker, one visit
    with pytest.raises(Conflict):
        world.visits.cancel_going_here(alice, marker.id)


def test_a_marker_can_be_overridden_with_another_date(world: World) -> None:
    alice = world.user()
    marker = world.visits.going_here(alice, THAI, planned_on=date(2026, 10, 7))
    view = world.visit(alice, going_here_id=marker.id, visited_on=date(2026, 10, 5))
    assert view.visit.visited_on == date(2026, 10, 5)


def test_a_marker_must_be_for_the_same_place(world: World) -> None:
    alice = world.user()
    marker = world.visits.going_here(alice, THAI)
    with pytest.raises(InvalidVisit):
        world.visit(alice, place=RAMEN, going_here_id=marker.id)
    assert world.visits.history(alice) == []


def test_logging_by_hand_ends_the_reminder_for_that_place_only(world: World) -> None:
    alice = world.user()
    thai = world.visits.going_here(alice, THAI)
    ramen = world.visits.going_here(alice, RAMEN)
    world.visit(alice, place=THAI)
    open_ids = [m.id for m in world.visits.open_going_here(alice)]
    assert open_ids == [ramen.id]
    assert thai.id not in open_ids


def test_a_cancelled_marker_is_not_due(world: World) -> None:
    alice = world.user()
    marker = world.visits.going_here(alice, THAI)
    assert world.visits.cancel_going_here(alice, marker.id).cancelled_at == START
    world.clock.advance(timedelta(days=3))
    assert world.visits.due_reminders(alice) == []
    with pytest.raises(Conflict):
        world.visit(alice, going_here_id=marker.id)


def test_a_visit_can_always_be_logged_by_hand_after_the_reminder(world: World) -> None:
    alice = world.user()
    marker = world.visits.going_here(alice, THAI)
    world.visits.mark_reminded(alice, marker.id)
    assert world.visit(alice, place=THAI)


def test_nobody_else_can_see_or_use_a_marker(world: World) -> None:
    alice, bob = world.user(), world.user("Bob")
    marker = world.visits.going_here(alice, THAI)
    world.clock.advance(timedelta(hours=49))
    assert world.visits.due_reminders(bob) == []
    assert world.visits.open_going_here(bob) == []
    with pytest.raises(NotFound):
        world.visits.mark_reminded(bob, marker.id)
    with pytest.raises(NotFound):
        world.visits.cancel_going_here(bob, marker.id)
    with pytest.raises(NotFound):
        world.visit(bob, going_here_id=marker.id)
    assert len(world.visits.due_reminders(alice)) == 1


# Privacy between people


def test_one_person_cannot_read_change_or_delete_anothers_visit(world: World) -> None:
    alice, bob = world.user(), world.user("Bob")
    view = world.visit(alice, dishes=(SOUP,))
    vid = view.visit.id

    with pytest.raises(NotFound):
        world.visits.visit(bob, vid)
    with pytest.raises(NotFound):
        world.visits.edit_visit(bob, vid, rating=1)
    with pytest.raises(NotFound):
        world.visits.edit_visit(bob, vid, dishes=[])
    with pytest.raises(NotFound):
        world.visits.delete_visit(bob, vid)
    with pytest.raises(NotFound):
        world.visits.tag(bob, vid, alice, acknowledged_sharing=True)
    with pytest.raises(NotFound):
        world.visits.confirm(bob, vid, rating=RatingAnswer("same"))
    assert world.visits.history(bob) == []
    assert world.visits.tag_requests(bob, None) == []

    untouched = world.visits.visit(alice, vid)
    assert untouched.visit == view.visit
    assert [d.id for d in untouched.dishes] == [d.id for d in view.dishes]


def test_a_dish_id_from_another_visit_is_not_usable(world: World) -> None:
    alice, bob = world.user(), world.user("Bob")
    theirs = world.visit(alice, dishes=(SOUP,))
    mine = world.visit(bob)
    stolen = DishInput("Tom Yum", 1, id=theirs.dishes[0].id)
    with pytest.raises(InvalidVisit):
        world.visits.edit_visit(bob, mine.visit.id, dishes=[stolen])
    assert world.visits.visit(alice, theirs.visit.id).dishes[0].rating == Decimal("7.5")
    assert world.visits.visit(bob, mine.visit.id).dishes == ()


# Editing and deleting


def test_editing_changes_only_what_is_sent(world: World) -> None:
    alice = world.user()
    view = world.visit(alice, dishes=(SOUP, CURRY), description="first")
    soup, curry = view.dishes

    edited = world.visits.edit_visit(alice, view.visit.id, description=None)
    assert edited.visit.description is None
    assert (edited.visit.rating, edited.visit.party, edited.visit.place_id) == (
        Decimal("8.5"),
        "with_others",
        "g-thai",
    )
    assert [d.id for d in edited.dishes] == [soup.id, curry.id]

    moved = world.visits.edit_visit(
        alice, view.visit.id, place=RAMEN, visited_on=date(2026, 10, 3), rating=3
    )
    assert (moved.visit.place_name, moved.visit.visited_on) == ("Near Ramen", date(2026, 10, 3))
    assert moved.visit.updated_at >= view.visit.updated_at


def test_a_dish_list_replaces_the_dishes_and_keeps_named_ones(world: World) -> None:
    alice = world.user()
    view = world.visit(alice, dishes=(SOUP, CURRY))
    soup, curry = view.dishes

    edited = world.visits.edit_visit(
        alice,
        view.visit.id,
        dishes=[
            DishInput("Satay", 8),
            DishInput("Tom Yum", 7.5, ["spicy"], id=soup.id),  # same rating, so still fresh
        ],
    )
    assert [d.name for d in edited.dishes] == ["Satay", "Tom Yum"]
    assert edited.dishes[1].id == soup.id
    assert edited.dishes[1].tags == ("spicy",)
    assert curry.id not in {d.id for d in edited.dishes}

    empty = world.visits.edit_visit(alice, view.visit.id, dishes=[])
    assert empty.dishes == ()
    assert world.visits.history(alice)[0].dish_count == 0  # still a visit


def test_rating_origin_survives_an_edit_that_keeps_the_rating(world: World) -> None:
    alice = world.user()
    view = world.visit(alice, rating=8.5)
    kept = world.visits.edit_visit(alice, view.visit.id, rating="8.50")
    assert kept.visit.rating_origin == "fresh"
    with pytest.raises(InvalidVisit):
        world.visits.edit_visit(alice, view.visit.id, rating=None)
    assert world.visits.edit_visit(alice, view.visit.id, rating=UNSET).visit.rating == Decimal(
        "8.5"
    )


def test_deleting_removes_the_visit_and_its_dishes(world: World) -> None:
    alice = world.user()
    view = world.visit(alice, dishes=(SOUP, CURRY))
    world.visits.delete_visit(alice, view.visit.id)
    assert world.visits.history(alice) == []
    with pytest.raises(NotFound):
        world.visits.visit(alice, view.visit.id)
    with pytest.raises(NotFound):
        world.visits.delete_visit(alice, view.visit.id)
    if world.db is not None:
        assert world.count("visit_dishes") == 0


# Tagging


def test_tagging_needs_others_a_platform_user_and_the_sharing_notice(world: World) -> None:
    alice, bob = world.user(), world.user("Bob")
    solo = world.visit(alice, party="solo")
    group = world.visit(alice)

    with pytest.raises(Conflict):
        world.visits.tag(alice, solo.visit.id, bob, acknowledged_sharing=True)
    with pytest.raises(InvalidVisit, match="will see your ratings, dishes and notes"):
        world.visits.tag(alice, group.visit.id, bob, acknowledged_sharing=False)
    assert "ratings, dishes and notes" in TAG_SHARING_NOTICE
    with pytest.raises(InvalidVisit, match="on Makan"):
        world.visits.tag(alice, group.visit.id, uuid4(), acknowledged_sharing=True)
    with pytest.raises(InvalidVisit, match="yourself"):
        world.visits.tag(alice, group.visit.id, alice, acknowledged_sharing=True)
    with pytest.raises(InvalidVisit):
        world.visit(alice, companions=[uuid4()], acknowledged_sharing=True)
    with pytest.raises(InvalidVisit, match="will see"):
        world.visit(alice, companions=[bob])
    with pytest.raises(Conflict):
        world.visit(alice, party="solo", companions=[bob], acknowledged_sharing=True)
    assert len(world.visits.history(alice)) == 2  # nothing was left behind by the refusals

    tag = world.visits.tag(alice, group.visit.id, bob, acknowledged_sharing=True)
    assert (tag.tag.status, tag.name) == ("pending", "Bob")


def test_companions_can_be_tagged_while_logging(world: World) -> None:
    alice, bob, cy = world.user(), world.user("Bob"), world.user("Cy")
    view = world.visit(alice, companions=[bob, cy, bob], acknowledged_sharing=True)
    assert sorted(t.name or "" for t in view.tags) == ["Bob", "Cy"]
    assert {t.tag.status for t in view.tags} == {"pending"}


def test_tagging_twice_is_one_request(world: World) -> None:
    alice, bob = world.user(), world.user("Bob")
    group = world.visit(alice)
    first = world.visits.tag(alice, group.visit.id, bob, acknowledged_sharing=True)
    second = world.visits.tag(alice, group.visit.id, bob, acknowledged_sharing=True)
    assert first.tag.id == second.tag.id
    assert len(world.visits.tag_requests(bob)) == 1


def test_a_pending_tag_shows_the_request_and_shares_nothing(world: World) -> None:
    alice, bob = world.user(), world.user("Bob")
    view, tag_id = world.tagged(alice, bob, dishes=(SOUP,), rating=9.9)

    [request] = world.visits.tag_requests(bob)
    assert request.tag.id == tag_id
    assert (request.place_name, request.visited_on) == ("Mid Thai", START.date())
    assert request.tagger_name == "Alice"
    assert request.tag.status == "pending"
    # Nothing the tagger rated or wrote is on the request.
    assert not {"rating", "description", "dishes"} & {f for f in vars(request)}
    assert "great night" not in repr(request)

    # Bob has no visit, and cannot open Alice's.
    assert world.visits.history(bob) == []
    with pytest.raises(NotFound):
        world.visits.visit(bob, view.visit.id)
    # And it is not a visit with Bob for Alice either.
    assert world.visits.history(alice)[0].companions == ()


def test_a_declined_tag_counts_as_nothing(world: World) -> None:
    alice, bob = world.user(), world.user("Bob")
    view, tag_id = world.tagged(alice, bob, dishes=(SOUP,))
    answered = world.visits.decline_tag(bob, tag_id)

    assert answered.request.tag.status == "declined"
    assert answered.visit is None
    assert world.visits.history(bob) == []
    assert world.visits.tag_requests(bob) == []  # no longer waiting
    assert [r.tag.status for r in world.visits.tag_requests(bob, "declined")] == ["declined"]
    assert world.visits.history(alice)[0].companions == ()
    with pytest.raises(NotFound):
        world.visits.visit(bob, view.visit.id)
    assert world.visits.decline_tag(bob, tag_id).request.tag.status == "declined"  # idempotent
    with pytest.raises(Conflict):
        world.visits.accept_tag(bob, tag_id)  # no other path
    again = world.visits.tag(alice, view.visit.id, bob, acknowledged_sharing=True)
    assert again.tag.id == tag_id  # asking again does not ask a second time
    assert again.tag.status == "declined"
    assert world.visits.tag_requests(bob) == []


def test_accepting_makes_the_tagged_persons_own_visit(world: World) -> None:
    alice, bob = world.user(), world.user("Bob")
    view, tag_id = world.tagged(alice, bob, dishes=(SOUP, CURRY), rating=9.0)

    answered = world.visits.accept_tag(bob, tag_id)
    mine = answered.visit
    assert mine is not None
    assert mine.visit.user_id == bob
    assert (mine.visit.place_id, mine.visit.visited_on) == ("g-thai", START.date())
    assert mine.visit.party == "with_others"
    assert (mine.visit.rating, mine.visit.rating_origin) == (None, None)  # not Alice's rating
    assert mine.dishes == ()
    assert mine.tagged_by_name == "Alice"
    assert mine.visit.description is None

    # Bob sees the same view as Alice, pre-filled, through the tag.
    assert mine.shared is not None
    assert mine.shared.tagger_name == "Alice"
    assert mine.shared.rating == Decimal("9.0")
    assert mine.shared.description == "great night"
    assert [s.dish.name for s in mine.shared.dishes] == ["Tom Yum", "Green Curry"]
    assert [s.answered_by for s in mine.shared.dishes] == [None, None]

    [summary] = world.visits.history(bob)
    assert (summary.visit.id, summary.tagged_by_name) == (mine.visit.id, "Alice")
    assert world.visits.history(alice)[0].companions == ("Bob",)
    # Alice's own visit is as she logged it.
    hers = world.visits.visit(alice, view.visit.id)
    assert hers.visit.rating == Decimal("9.0")
    assert hers.shared is None
    assert [t.tag.status for t in hers.tags] == ["accepted"]


def test_the_tagger_data_is_shared_only_with_the_tagged_person(world: World) -> None:
    alice, bob, cy = world.user(), world.user("Bob"), world.user("Cy")
    view, tag_id = world.tagged(alice, bob, dishes=(SOUP,))
    mine = world.visits.accept_tag(bob, tag_id).visit
    assert mine is not None

    with pytest.raises(NotFound):
        world.visits.visit(cy, view.visit.id)
    with pytest.raises(NotFound):
        world.visits.visit(cy, mine.visit.id)
    with pytest.raises(NotFound):
        world.visits.accept_tag(cy, tag_id)
    with pytest.raises(NotFound):
        world.visits.decline_tag(cy, tag_id)
    with pytest.raises(NotFound):
        world.visits.accept_tag(alice, tag_id)  # not the tagger either
    assert world.visits.tag_requests(cy, None) == []
    assert world.visits.history(cy) == []
    with pytest.raises(NotFound):
        world.visits.accept_tag(bob, uuid4())


def test_accepting_twice_is_one_visit(world: World) -> None:
    alice, bob = world.user(), world.user("Bob")
    _, tag_id = world.tagged(alice, bob)
    first = world.visits.accept_tag(bob, tag_id)
    second = world.visits.accept_tag(bob, tag_id)
    assert first.visit is not None and second.visit is not None
    assert first.visit.visit.id == second.visit.visit.id
    assert len(world.visits.history(bob)) == 1
    with pytest.raises(Conflict):
        world.visits.decline_tag(bob, tag_id)  # no other path
    assert len(world.visits.history(bob)) == 1


def test_a_request_can_be_withdrawn_only_while_pending(world: World) -> None:
    alice, bob, cy = world.user(), world.user("Bob"), world.user("Cy")
    view = world.visit(alice, companions=[bob, cy], acknowledged_sharing=True)
    by_name = {t.name: t.tag.id for t in view.tags}

    with pytest.raises(NotFound):
        world.visits.withdraw_tag(bob, view.visit.id, by_name["Bob"])  # not the tagger
    world.visits.withdraw_tag(alice, view.visit.id, by_name["Bob"])
    assert world.visits.tag_requests(bob) == []
    with pytest.raises(NotFound):
        world.visits.withdraw_tag(alice, view.visit.id, by_name["Bob"])
    with pytest.raises(NotFound):
        world.visits.accept_tag(bob, by_name["Bob"])

    world.visits.accept_tag(cy, by_name["Cy"])
    with pytest.raises(Conflict):
        world.visits.withdraw_tag(alice, view.visit.id, by_name["Cy"])
    with pytest.raises(Conflict):  # a visit with people on it is not solo
        world.visits.edit_visit(alice, view.visit.id, party="solo")


def test_a_visit_can_go_solo_once_the_tags_are_gone_or_declined(world: World) -> None:
    alice, bob = world.user(), world.user("Bob")
    view, tag_id = world.tagged(alice, bob)
    with pytest.raises(Conflict):
        world.visits.edit_visit(alice, view.visit.id, party="solo")
    world.visits.decline_tag(bob, tag_id)
    assert world.visits.edit_visit(alice, view.visit.id, party="solo").visit.party == "solo"


# Confirming a tagged visit


def accepted(
    world: World, *, dishes: tuple[DishInput, ...] = (SOUP, CURRY)
) -> tuple[UUID, UUID, VisitView, VisitView]:
    alice, bob = world.user(), world.user("Bob")
    view, tag_id = world.tagged(alice, bob, dishes=dishes, rating=9.0)
    mine = world.visits.accept_tag(bob, tag_id).visit
    assert mine is not None
    return alice, bob, view, mine


def test_confirming_the_same_ratings(world: World) -> None:
    alice, bob, view, mine = accepted(world)
    soup, curry = view.dishes

    done = world.visits.confirm(
        bob,
        mine.visit.id,
        rating=RatingAnswer("same"),
        dishes=[DishAnswer(soup.id, "same"), DishAnswer(curry.id, "same", comment=" tasty ")],
    )
    assert (done.visit.rating, done.visit.rating_origin) == (Decimal("9.0"), "confirmed")
    assert [(d.name, d.rating, d.rating_origin) for d in done.dishes] == [
        ("Tom Yum", Decimal("7.5"), "confirmed"),
        ("Green Curry", Decimal("6.0"), "confirmed"),
    ]
    assert done.dishes[1].comment == "tasty"
    assert done.dishes[0].tags == ()  # the tagger's own tags are not copied
    assert {d.user_id for d in done.dishes} == {bob}
    assert {d.source_dish_id for d in done.dishes} == {soup.id, curry.id}
    assert done.shared is not None
    assert [s.answered_by for s in done.shared.dishes] == [d.id for d in done.dishes]

    # Each side counts its own: Alice's ratings are still hers and fresh.
    hers = world.visits.visit(alice, view.visit.id)
    assert hers.visit.rating_origin == "fresh"
    assert [(d.rating, d.rating_origin, d.user_id) for d in hers.dishes] == [
        (Decimal("7.5"), "fresh", alice),
        (Decimal("6.0"), "fresh", alice),
    ]


def test_changing_the_ratings(world: World) -> None:
    alice, bob, view, mine = accepted(world)
    soup, curry = view.dishes

    done = world.visits.confirm(
        bob,
        mine.visit.id,
        rating=RatingAnswer("change", "4.45"),
        dishes=[
            DishAnswer(soup.id, "change", 3, ["salty", "dinner"]),
            DishAnswer(curry.id, "same"),
        ],
    )
    assert (done.visit.rating, done.visit.rating_origin) == (Decimal("4.5"), "fresh")
    assert [(d.rating, d.rating_origin) for d in done.dishes] == [
        (Decimal("3.0"), "fresh"),
        (Decimal("6.0"), "confirmed"),
    ]
    assert done.dishes[0].tags == ("salty", "dinner")
    # The tagger's side did not move.
    hers = world.visits.visit(alice, view.visit.id)
    assert hers.visit.rating == Decimal("9.0")
    assert [d.rating for d in hers.dishes] == [Decimal("7.5"), Decimal("6.0")]


def test_having_something_different(world: World) -> None:
    _, bob, view, mine = accepted(world)
    soup, curry = view.dishes

    world.visits.confirm(
        bob,
        mine.visit.id,
        rating=RatingAnswer("same"),
        dishes=[DishAnswer(soup.id, "skip"), DishAnswer(curry.id, "same")],
    )
    mine_now = world.visits.visit(bob, mine.visit.id)
    own = world.visits.edit_visit(
        bob,
        mine.visit.id,
        dishes=[
            *(DishInput(d.name, d.rating, d.tags, d.comment, d.id) for d in mine_now.dishes),
            DishInput("Pad See Ew", 8.2, ["noodles"]),
        ],
    )
    assert [(d.name, d.rating_origin, d.source_dish_id) for d in own.dishes] == [
        ("Green Curry", "confirmed", curry.id),
        ("Pad See Ew", "fresh", None),
    ]
    assert own.shared is not None
    assert [(s.dish.name, s.answered_by is not None) for s in own.shared.dishes] == [
        ("Tom Yum", False),
        ("Green Curry", True),
    ]
    # Saying they did have the first dish after all is just another answer.
    back = world.visits.confirm(bob, mine.visit.id, dishes=[DishAnswer(soup.id, "same")])
    assert [d.name for d in back.dishes] == ["Green Curry", "Pad See Ew", "Tom Yum"]
    assert [d.position for d in back.dishes] == [0, 1, 2]


def test_answering_again_changes_the_answer_without_duplicating(world: World) -> None:
    _, bob, view, mine = accepted(world)
    soup = view.dishes[0]
    world.visits.confirm(bob, mine.visit.id, dishes=[DishAnswer(soup.id, "same")])
    again = world.visits.confirm(bob, mine.visit.id, dishes=[DishAnswer(soup.id, "change", 2)])
    assert [(d.rating, d.rating_origin) for d in again.dishes] == [(Decimal("2.0"), "fresh")]
    same = world.visits.confirm(bob, mine.visit.id, dishes=[DishAnswer(soup.id, "same")])
    assert [(d.rating, d.rating_origin) for d in same.dishes] == [(Decimal("7.5"), "confirmed")]


def test_answering_again_keeps_tags_and_comment_unless_given(world: World) -> None:
    _, bob, view, mine = accepted(world)
    soup = view.dishes[0]
    first = world.visits.confirm(
        bob, mine.visit.id, dishes=[DishAnswer(soup.id, "same", tags=["spicy"], comment="good")]
    )
    assert (first.dishes[0].tags, first.dishes[0].comment) == (("spicy",), "good")
    again = world.visits.confirm(bob, mine.visit.id, dishes=[DishAnswer(soup.id, "change", 2)])
    assert (again.dishes[0].tags, again.dishes[0].comment) == (("spicy",), "good")
    cleared = world.visits.confirm(
        bob, mine.visit.id, dishes=[DishAnswer(soup.id, "same", tags=[], comment=None)]
    )
    assert (cleared.dishes[0].tags, cleared.dishes[0].comment) == ((), None)


def test_a_bad_answer_stores_nothing(world: World) -> None:
    alice, bob, view, mine = accepted(world)
    soup = view.dishes[0]
    other = world.visit(alice, dishes=(SOUP,))

    with pytest.raises(InvalidVisit):
        world.visits.confirm(
            bob,
            mine.visit.id,
            rating=RatingAnswer("same"),
            dishes=[DishAnswer(soup.id, "same"), DishAnswer(soup.id, "same")],
        )
    with pytest.raises(InvalidVisit):  # a dish of a visit Bob was not tagged in
        world.visits.confirm(bob, mine.visit.id, dishes=[DishAnswer(other.dishes[0].id, "same")])
    with pytest.raises(InvalidVisit):
        world.visits.confirm(
            bob,
            mine.visit.id,
            rating=RatingAnswer("same"),
            dishes=[DishAnswer(soup.id, "change", 12)],
        )
    with pytest.raises(InvalidVisit):
        world.visits.confirm(bob, mine.visit.id, rating=RatingAnswer("change", None))
    after = world.visits.visit(bob, mine.visit.id)
    assert (after.visit.rating, after.dishes) == (None, ())


def test_only_a_visit_from_an_accepted_tag_can_be_confirmed(world: World) -> None:
    alice, bob = world.user(), world.user("Bob")
    plain = world.visit(bob)
    with pytest.raises(Conflict):
        world.visits.confirm(bob, plain.visit.id, rating=RatingAnswer("same"))
    view, _ = world.tagged(alice, bob)
    with pytest.raises(NotFound):
        world.visits.confirm(bob, view.visit.id)  # pending: Alice's visit is not Bob's to answer


def test_later_edits_by_the_tagger_do_not_move_confirmed_ratings(world: World) -> None:
    alice, bob, view, mine = accepted(world)
    soup, curry = view.dishes
    world.visits.confirm(
        bob, mine.visit.id, rating=RatingAnswer("same"), dishes=[DishAnswer(soup.id, "same")]
    )

    world.visits.edit_visit(
        alice,
        view.visit.id,
        rating=2,
        dishes=[DishInput("Tom Yum", 1, id=soup.id), DishInput("Green Curry", 3, id=curry.id)],
    )
    after = world.visits.visit(bob, mine.visit.id)
    assert after.visit.rating == Decimal("9.0")
    assert after.dishes[0].rating == Decimal("7.5")  # confirmed before, so theirs
    assert after.shared is not None
    assert after.shared.rating == Decimal("2.0")  # the pre-fill follows the tagger
    assert [s.dish.rating for s in after.shared.dishes] == [Decimal("1.0"), Decimal("3.0")]
    assert [s.answered_by is not None for s in after.shared.dishes] == [True, False]

    later = world.visits.confirm(bob, mine.visit.id, dishes=[DishAnswer(curry.id, "same")])
    assert [d.rating for d in later.dishes] == [Decimal("7.5"), Decimal("3.0")]


def test_deleting_the_tagger_visit_leaves_the_tagged_entry(world: World) -> None:
    alice, bob, view, mine = accepted(world)
    soup = view.dishes[0]
    world.visits.confirm(
        bob, mine.visit.id, rating=RatingAnswer("same"), dishes=[DishAnswer(soup.id, "same")]
    )

    world.visits.delete_visit(alice, view.visit.id)

    left = world.visits.visit(bob, mine.visit.id)
    assert (left.visit.rating, left.visit.rating_origin) == (Decimal("9.0"), "confirmed")
    assert [(d.name, d.rating, d.rating_origin) for d in left.dishes] == [
        ("Tom Yum", Decimal("7.5"), "confirmed")
    ]
    assert left.dishes[0].source_dish_id is None
    assert left.shared is None  # nothing of Alice's remains to see
    assert left.tagged_by_name == "Alice"
    assert len(world.visits.history(bob)) == 1
    assert world.visits.tag_requests(bob, None) == []
    with pytest.raises(Conflict):
        world.visits.confirm(bob, mine.visit.id, rating=RatingAnswer("same"))
    # Their entry is still theirs to edit.
    edited = world.visits.edit_visit(bob, mine.visit.id, rating=5)
    assert (edited.visit.rating, edited.visit.rating_origin) == (Decimal("5.0"), "fresh")


def test_deleting_the_tagged_visit_leaves_the_tagger_alone(world: World) -> None:
    alice, bob, view, mine = accepted(world)
    world.visits.delete_visit(bob, mine.visit.id)

    again = world.visits.accept_tag(bob, view.tags[0].tag.id)
    assert again.visit is None  # it is not made a second time
    assert world.visits.history(bob) == []
    assert len(world.visits.visit(alice, view.visit.id).dishes) == 2


def test_a_tagged_visit_keeps_its_restaurant(world: World) -> None:
    _, bob, _, mine = accepted(world)
    with pytest.raises(Conflict):
        world.visits.edit_visit(bob, mine.visit.id, place=RAMEN)
    with pytest.raises(Conflict):
        world.visits.edit_visit(bob, mine.visit.id, party="solo")
    ok = world.visits.edit_visit(bob, mine.visit.id, place=THAI, description="mine")
    assert ok.visit.description == "mine"


def test_the_tagger_description_can_be_kept_private(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(visit_service, "SHARE_TAGGER_DESCRIPTION", False)
    _, _, _, mine = accepted(world)
    assert mine.shared is not None
    assert mine.shared.description is None


# Data rights


def test_export_covers_every_new_table_and_only_the_persons_own_data(world: World) -> None:
    alice, bob = world.user(), world.user("Bob")
    marker = world.visits.going_here(alice, RAMEN)
    view, tag_id = world.tagged(alice, bob, dishes=(SOUP,), rating=9.1)
    world.visits.accept_tag(bob, tag_id)
    solo = world.visit(bob, party="solo", dishes=(CURRY,), description="bob private note")

    mine = world.people.export(alice)
    assert [m["id"] for m in mine["going_here"]] == [str(marker.id)]
    assert [v["id"] for v in mine["visits"]] == [str(view.visit.id)]
    assert [(d["name"], d["tags"]) for d in mine["visit_dishes"]] == [
        ("Tom Yum", ["spicy", "not spicy", "lunch"])
    ]
    assert mine["visits"][0]["rating"] == 9.1
    assert [t["status"] for t in mine["visit_tags"]] == ["accepted"]
    assert mine["tag_requests"] == []
    assert "Bob" not in str(mine) and "bob private note" not in str(mine)

    theirs = world.people.export(bob)
    assert len(theirs["visits"]) == 2  # the accepted one and their own
    assert [d["name"] for d in theirs["visit_dishes"]] == ["Green Curry"]
    [request] = theirs["tag_requests"]
    assert request["status"] == "accepted"
    assert request["place_name"] == "Mid Thai"
    assert theirs["going_here"] == [] and theirs["visit_tags"] == []
    # Bob learns of Alice's visit only as far as the request showed him, plus what he confirmed.
    assert "great night" not in str(theirs) and "Tom Yum" not in str(theirs)
    assert str(solo.visit.id) in str(theirs)


def test_a_pending_request_is_exported_without_the_taggers_data(world: World) -> None:
    alice, bob = world.user(), world.user("Bob")
    world.tagged(alice, bob, dishes=(SOUP,), rating=9.1)
    [request] = world.people.export(bob)["tag_requests"]
    assert request["status"] == "pending"
    assert set(request) == {
        "id",
        "tagger_id",
        "status",
        "created_at",
        "responded_at",
        "accepted_visit_id",
        "place_name",
        "place_address",
        "visited_on",
    }
    assert "great night" not in str(world.people.export(bob))


def test_deleting_an_account_deletes_everything_it_owns(world: World) -> None:
    alice, bob = world.user(), world.user("Bob")
    world.visits.going_here(alice, RAMEN)
    _, tag_id = world.tagged(alice, bob, dishes=(SOUP,), rating=9.0)
    mine = world.visits.accept_tag(bob, tag_id).visit
    assert mine is not None
    world.visits.confirm(bob, mine.visit.id, rating=RatingAnswer("same"))
    pending_view, _ = world.tagged(alice, bob)

    world.people.delete_user(alice)

    empty = world.people.export(alice)
    for table in ("going_here", "visits", "visit_dishes", "visit_tags", "tag_requests"):
        assert empty[table] == [], table
    # Bob keeps his own confirmed entry, which no longer names Alice, and nothing of hers.
    kept = world.visits.visit(bob, mine.visit.id)
    assert kept.visit.rating == Decimal("9.0")
    assert kept.visit.tagged_by_user_id is None
    assert kept.shared is None
    assert world.visits.tag_requests(bob, None) == []
    assert pending_view.visit.id not in {s.visit.id for s in world.visits.history(bob)}
    if world.db is not None:
        for table, column in (
            ("going_here", "user_id"),
            ("visits", "user_id"),
            ("visit_dishes", "user_id"),
            ("visit_tags", "tagger_id"),
        ):
            assert world.count(table, f"{column} = %s", alice) == 0, table


def test_deleting_the_tagged_account_removes_the_requests_to_it(world: World) -> None:
    alice, bob = world.user(), world.user("Bob")
    view, tag_id = world.tagged(alice, bob)
    world.people.delete_user(bob)
    assert world.visits.visit(alice, view.visit.id).tags == ()
    assert world.people.export(bob)["tag_requests"] == []
    with pytest.raises(NotFound):
        world.visits.accept_tag(bob, tag_id)
