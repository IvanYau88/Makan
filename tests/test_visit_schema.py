"""Migration 0004 on a live Postgres: exact ratings, row-level security, tag moves, and cascades.

These skip unless `MAKAN_TEST_DATABASE_URL` points at a throwaway Postgres.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from tests.helpers import act_as, count


def add_user(db: Any) -> UUID:
    uid = uuid4()
    db.execute("insert into users (id) values (%s)", (uid,))
    return uid


def add_visit(db: Any, user: UUID, **columns: Any) -> UUID:
    values = {
        "id": uuid4(),
        "user_id": user,
        "place_source": "overture",
        "place_id": "g-1",
        "place_name": "Mid Thai",
        "visited_on": date(2026, 10, 1),
        "party": "with_others",
        "rating": Decimal("7.5"),
        "rating_origin": "fresh",
    } | columns
    names = ", ".join(values)
    marks = ", ".join(["%s"] * len(values))
    db.execute(f"insert into visits ({names}) values ({marks})", list(values.values()))
    return UUID(str(values["id"]))


def add_tag(db: Any, visit: UUID, tagger: UUID, tagged: UUID, **columns: Any) -> UUID:
    values = {"id": uuid4(), "visit_id": visit, "tagger_id": tagger, "tagged_user_id": tagged}
    values |= columns
    names = ", ".join(values)
    marks = ", ".join(["%s"] * len(values))
    db.execute(f"insert into visit_tags ({names}) values ({marks})", list(values.values()))
    return UUID(str(values["id"]))


def add_dish(db: Any, visit: UUID, user: UUID, **columns: Any) -> UUID:
    values = {
        "id": uuid4(),
        "visit_id": visit,
        "user_id": user,
        "position": 0,
        "name": "Tom Yum",
        "rating": Decimal("6.5"),
        "rating_origin": "fresh",
    } | columns
    names = ", ".join(values)
    marks = ", ".join(["%s"] * len(values))
    db.execute(f"insert into visit_dishes ({names}) values ({marks})", list(values.values()))
    return UUID(str(values["id"]))


# Ratings are exact decimals from 0 to 10


def test_ratings_are_exact_decimals_with_one_place(db: Any) -> None:
    user = add_user(db)
    visit = add_visit(db, user, rating=Decimal("5.7"))
    dish = add_dish(db, visit, user, rating=Decimal("0.1"))
    rating = db.execute("select rating from visits where id = %s", (visit,)).fetchone()[0]
    dish_rating = db.execute("select rating from visit_dishes where id = %s", (dish,)).fetchone()[0]
    assert isinstance(rating, Decimal) and rating == Decimal("5.7")
    assert dish_rating == Decimal("0.1")
    types = db.execute(
        "select table_name, data_type, numeric_precision, numeric_scale "
        "from information_schema.columns where table_schema = current_schema() "
        "and column_name = 'rating' and table_name in ('visits', 'visit_dishes')"
    ).fetchall()
    assert {tuple(r) for r in types} == {
        ("visits", "numeric", 3, 1),
        ("visit_dishes", "numeric", 3, 1),
    }


@pytest.mark.parametrize("bad", [Decimal("10.1"), Decimal("-0.1"), Decimal("11"), Decimal("100")])
def test_ratings_outside_zero_to_ten_are_refused(db: Any, bad: Decimal) -> None:
    user = add_user(db)
    with pytest.raises(Exception, match=r"out of range|overflow|violates check constraint"):
        add_visit(db, user, rating=bad)
    visit = add_visit(db, user)
    with pytest.raises(Exception, match=r"out of range|overflow|violates check constraint"):
        add_dish(db, visit, user, rating=bad)


def test_the_edges_are_allowed_and_a_dish_must_have_a_rating(db: Any) -> None:
    user = add_user(db)
    visit = add_visit(db, user, rating=Decimal("0"))
    add_visit(db, user, rating=Decimal("10"))
    add_dish(db, visit, user, rating=Decimal("10.0"))
    with pytest.raises(Exception, match="null value"):
        add_dish(db, visit, user, rating=None)
    with pytest.raises(Exception, match="violates check constraint"):
        add_dish(db, visit, user, rating_origin="guessed")


def test_a_rating_and_its_origin_go_together(db: Any) -> None:
    user = add_user(db)
    add_visit(db, user, rating=None, rating_origin=None)  # a tagged visit not yet rated
    with pytest.raises(Exception, match="violates check constraint"):
        add_visit(db, user, rating=Decimal("5"), rating_origin=None)
    with pytest.raises(Exception, match="violates check constraint"):
        add_visit(db, user, rating=None, rating_origin="fresh")
    with pytest.raises(Exception, match="violates check constraint"):
        add_visit(db, user, party="alone")


def test_place_identity_is_provider_qualified_and_required(db: Any) -> None:
    user = add_user(db)
    add_visit(db, user, place_source="overture", place_id="same", place_name="Mid Thai")
    add_visit(db, user, place_source="google", place_id="same", place_name="Mid Thai")
    for column in ("place_source", "place_id", "place_name"):
        with pytest.raises(Exception, match=r"null value|violates check constraint"):
            add_visit(db, user, **{column: None})
        with pytest.raises(Exception, match="violates check constraint"):
            add_visit(db, user, **{column: " "})


def test_dish_tags_keep_their_order_and_repeats(db: Any) -> None:
    user = add_user(db)
    visit = add_visit(db, user)
    tags = ["spicy", "not spicy", "lunch", "spicy"]
    dish = add_dish(db, visit, user, tags=tags)
    assert db.execute("select tags from visit_dishes where id = %s", (dish,)).fetchone()[0] == tags
    other = add_dish(db, visit, user)
    assert db.execute("select tags from visit_dishes where id = %s", (other,)).fetchone()[0] == []


def test_a_dish_belongs_to_a_visit_of_the_same_owner(db: Any) -> None:
    a, b = add_user(db), add_user(db)
    visit = add_visit(db, a)
    with pytest.raises(Exception, match="foreign key"):
        add_dish(db, visit, b)
    with pytest.raises(Exception, match="foreign key"):
        add_tag(db, visit, b, a)  # b did not make this visit


# Tag states


def test_a_tag_moves_once_from_pending(db: Any) -> None:
    a, b = add_user(db), add_user(db)
    visit = add_visit(db, a)
    mine = add_visit(db, b, tagged_by_user_id=a, rating=None, rating_origin=None)
    tag = add_tag(db, visit, a, b)

    def status() -> str:
        return str(db.execute("select status from visit_tags where id = %s", (tag,)).fetchone()[0])

    with pytest.raises(Exception, match="has not been answered"):
        db.execute("update visit_tags set accepted_visit_id = %s where id = %s", (mine, tag))
    with pytest.raises(Exception, match="violates check constraint"):
        db.execute("update visit_tags set status = 'accepted' where id = %s", (tag,))  # no time
    db.execute(
        "update visit_tags set status = 'accepted', responded_at = now(), "
        "accepted_visit_id = %s where id = %s",
        (mine, tag),
    )
    assert status() == "accepted"
    for target in ("pending", "declined"):
        with pytest.raises(Exception, match="cannot become"):
            db.execute("update visit_tags set status = %s where id = %s", (target, tag))
    with pytest.raises(Exception, match="keeps the time"):
        db.execute(
            "update visit_tags set responded_at = now() + interval '1 day' where id = %s", (tag,)
        )
    with pytest.raises(Exception, match="cannot be moved"):
        db.execute("update visit_tags set tagged_user_id = %s where id = %s", (a, tag))
    assert status() == "accepted"


def test_a_declined_tag_stays_declined(db: Any) -> None:
    a, b = add_user(db), add_user(db)
    tag = add_tag(db, add_visit(db, a), a, b)
    db.execute(
        "update visit_tags set status = 'declined', responded_at = now() where id = %s", (tag,)
    )
    with pytest.raises(Exception, match="cannot become"):
        db.execute("update visit_tags set status = 'accepted' where id = %s", (tag,))
    with pytest.raises(Exception, match=r"keeps the visit|violates check constraint"):
        db.execute("update visit_tags set accepted_visit_id = %s where id = %s", (uuid4(), tag))


def test_a_tag_rules_in_the_table(db: Any) -> None:
    a, b = add_user(db), add_user(db)
    visit = add_visit(db, a)
    add_tag(db, visit, a, b)
    with pytest.raises(Exception, match="visit_tags_visit_id_tagged_user_id_key"):
        add_tag(db, visit, a, b)
    with pytest.raises(Exception, match="violates check constraint"):
        add_tag(db, visit, a, a)
    with pytest.raises(Exception, match="violates check constraint"):
        add_tag(db, visit, a, add_user(db), status="accepted")  # accepted needs a time
    with pytest.raises(Exception, match="violates check constraint"):
        add_tag(db, visit, a, add_user(db), status="maybe", responded_at=None)


# Going here


def test_going_here_has_one_open_marker_per_place(db: Any) -> None:
    user = add_user(db)
    insert = (
        "insert into going_here (user_id, place_source, place_id, place_name, planned_on, "
        "started_at, remind_at) values (%s, 'overture', 'g-1', 'Mid Thai', %s, now(), "
        "now() + interval '48 hours')"
    )
    db.execute(insert, (user, date(2026, 10, 8)))
    with pytest.raises(Exception, match="going_here_one_open_idx"):
        db.execute(insert, (user, date(2026, 10, 8)))
    db.execute("update going_here set reminded_at = now()")
    db.execute(insert, (user, date(2026, 10, 8)))  # the first has been reminded, so not open
    with pytest.raises(Exception, match="violates check constraint"):
        db.execute(
            "insert into going_here (user_id, place_source, place_id, place_name, planned_on, "
            "started_at, remind_at) values (%s, 'o', 'x', 'n', now(), now(), now())",
            (user,),
        )


# Cascades


def test_deleting_the_tagger_visit_leaves_the_tagged_visit(db: Any) -> None:
    a, b = add_user(db), add_user(db)
    source = add_visit(db, a)
    dish = add_dish(db, source, a)
    mine = add_visit(db, b, tagged_by_user_id=a)
    answer = add_dish(db, mine, b, source_dish_id=dish, rating_origin="confirmed")
    tag = add_tag(db, source, a, b)
    db.execute(
        "update visit_tags set status = 'accepted', responded_at = now(), "
        "accepted_visit_id = %s where id = %s",
        (mine, tag),
    )
    marker = uuid4()
    db.execute(
        "insert into going_here (id, user_id, place_source, place_id, place_name, planned_on, "
        "started_at, remind_at, visit_id) values (%s, %s, 'o', 'x', 'n', now(), now(), "
        "now() + interval '1 hour', %s)",
        (marker, a, source),
    )

    db.execute("delete from visits where id = %s", (source,))

    assert count(db, "visit_tags") == 0
    assert count(db, "visits") == 1
    kept = db.execute(
        "select source_dish_id, rating_origin from visit_dishes where id = %s", (answer,)
    ).fetchone()
    assert tuple(kept) == (None, "confirmed")
    assert db.execute("select visit_id from going_here").fetchone()[0] is None


def test_deleting_the_tagged_visit_leaves_the_tag(db: Any) -> None:
    a, b = add_user(db), add_user(db)
    source = add_visit(db, a)
    mine = add_visit(db, b, tagged_by_user_id=a)
    tag = add_tag(db, source, a, b)
    db.execute(
        "update visit_tags set status = 'accepted', responded_at = now(), "
        "accepted_visit_id = %s where id = %s",
        (mine, tag),
    )
    db.execute("delete from visits where id = %s", (mine,))
    row = db.execute("select status, accepted_visit_id from visit_tags").fetchone()
    assert tuple(row) == ("accepted", None)
    assert count(db, "visits") == 1


def test_deleting_users_removes_their_rows_and_only_theirs(db: Any) -> None:
    a, b = add_user(db), add_user(db)
    source = add_visit(db, a)
    add_dish(db, source, a)
    mine = add_visit(db, b, tagged_by_user_id=a)
    tag = add_tag(db, source, a, b)
    db.execute(
        "update visit_tags set status = 'accepted', responded_at = now(), "
        "accepted_visit_id = %s where id = %s",
        (mine, tag),
    )
    db.execute("delete from users where id = %s", (a,))
    assert (count(db, "visit_tags"), count(db, "visit_dishes"), count(db, "visits")) == (0, 0, 1)
    assert db.execute("select tagged_by_user_id from visits").fetchone()[0] is None
    db.execute("delete from users where id = %s", (b,))
    assert count(db, "visits") == 0


# Row-level security


def seed(db: Any) -> tuple[UUID, UUID, UUID, UUID, UUID]:
    """Two people: Alice with a visit, a dish and a pending tag for Bob; Bob with a visit."""
    a, b = add_user(db), add_user(db)
    visit = add_visit(db, a, description="alice private")
    add_dish(db, visit, a)
    tag = add_tag(db, visit, a, b)
    theirs = add_visit(db, b, description="bob private")
    add_dish(db, theirs, b)
    db.execute(
        "insert into going_here (user_id, place_source, place_id, place_name, planned_on, "
        "started_at, remind_at) values (%s, 'o', 'x', 'n', now(), now(), "
        "now() + interval '1 hour')",
        (a,),
    )
    return a, b, visit, tag, theirs


def test_each_person_reads_only_their_own_visit_rows(db: Any, app_role: str) -> None:
    a, b, visit, _, theirs = seed(db)
    act_as(db, app_role, a)
    assert [r[0] for r in db.execute("select id from visits").fetchall()] == [visit]
    assert count(db, "visit_dishes") == 1
    assert count(db, "going_here") == 1
    act_as(db, app_role, b)
    assert [r[0] for r in db.execute("select id from visits").fetchall()] == [theirs]
    assert count(db, "visit_dishes") == 1
    assert count(db, "going_here") == 0


def test_a_tagged_person_sees_the_tag_but_never_the_taggers_visit(
    db: Any,
    app_role: str,
) -> None:
    _, b, visit, tag, _ = seed(db)
    act_as(db, app_role, b)
    assert [r[0] for r in db.execute("select id from visit_tags").fetchall()] == [tag]
    assert db.execute("select count(*) from visits where id = %s", (visit,)).fetchone()[0] == 0
    assert (
        db.execute("select count(*) from visit_dishes where visit_id = %s", (visit,)).fetchone()[0]
        == 0
    )
    # Accepted or not, no policy shows Alice's rows to Bob; the backend shares them.
    db.execute("reset role")
    db.execute("update visit_tags set status = 'accepted', responded_at = now()")
    act_as(db, app_role, b)
    assert db.execute("select count(*) from visits where id = %s", (visit,)).fetchone()[0] == 0
    act_as(db, app_role, uuid4())
    assert count(db, "visit_tags") == 0


def test_guests_and_unset_connections_read_no_visit_rows(db: Any, app_role: str) -> None:
    seed(db)
    act_as(db, app_role, None)
    for table in ("going_here", "visits", "visit_dishes", "visit_tags"):
        assert count(db, table) == 0, table


def test_a_person_cannot_write_into_another_persons_rows(db: Any, app_role: str) -> None:
    a, b, visit, _, theirs = seed(db)
    act_as(db, app_role, b)
    assert db.execute("update visits set rating = 1 where id = %s", (visit,)).rowcount == 0
    assert db.execute("delete from visits where id = %s", (visit,)).rowcount == 0
    assert (
        db.execute("update visit_dishes set rating = 1 where visit_id = %s", (visit,)).rowcount == 0
    )
    with pytest.raises(Exception, match="row-level security"):
        add_visit(db, a)
    with pytest.raises(Exception, match="row-level security"):
        add_dish(db, theirs, a)
    with pytest.raises(Exception, match=r"row-level security|foreign key"):
        add_dish(db, visit, b)
    with pytest.raises(Exception, match="row-level security"):
        add_tag(db, visit, a, uuid4())  # not a tag for b to make, and no such user


def test_only_the_tagger_makes_and_withdraws_and_only_the_tagged_answers(
    db: Any,
    app_role: str,
) -> None:
    a, b, visit, tag, _ = seed(db)
    c = add_user(db)
    act_as(db, app_role, a)
    with pytest.raises(Exception, match="row-level security"):
        add_tag(db, visit, a, c, status="accepted", responded_at=None)
    add_tag(db, visit, a, c)  # the tagger asks
    assert (
        db.execute(
            "update visit_tags set status = 'accepted', responded_at = now() where id = %s", (tag,)
        ).rowcount
        == 0
    )  # but cannot answer for b
    act_as(db, app_role, c)
    assert db.execute("delete from visit_tags where tagged_user_id = %s", (c,)).rowcount == 0
    assert (
        db.execute(
            "update visit_tags set status = 'declined', responded_at = now() "
            "where tagged_user_id = %s",
            (c,),
        ).rowcount
        == 1
    )
    act_as(db, app_role, a)
    assert (
        db.execute("delete from visit_tags where tagged_user_id = %s", (c,)).rowcount == 0
    )  # answered
    assert db.execute("delete from visit_tags where id = %s", (tag,)).rowcount == 1  # pending
    act_as(db, app_role, b)
    assert count(db, "visit_tags") == 0


def test_a_tagged_person_cannot_make_a_tag_look_answered_wrongly(
    db: Any,
    app_role: str,
) -> None:
    _, b, _, tag, _ = seed(db)
    act_as(db, app_role, b)
    with pytest.raises(Exception, match=r"row-level security|cannot become|not been answered"):
        db.execute(
            "update visit_tags set status = 'pending', responded_at = null where id = %s", (tag,)
        )
    with pytest.raises(Exception, match=r"row-level security|cannot become"):
        db.execute("update visit_tags set status = 'maybe' where id = %s", (tag,))
    assert (
        db.execute(
            "update visit_tags set status = 'declined', responded_at = now() where id = %s", (tag,)
        ).rowcount
        == 1
    )
    assert (
        db.execute("update visit_tags set status = 'accepted' where id = %s", (tag,)).rowcount == 0
    )


def test_the_guard_function_pins_its_search_path(db: Any) -> None:
    config = db.execute(
        "select proconfig from pg_proc where proname = 'makan_guard_visit_tag' "
        "and pronamespace = current_schema()::regnamespace"
    ).fetchone()[0]
    assert config == ['search_path=""']
