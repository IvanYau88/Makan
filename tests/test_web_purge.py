"""The scheduled purge of expired group sessions, with an in-memory store and no network."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from makan.config import Config
from makan.places import FakePlacesProvider
from makan.providers import FakeProvider
from makan.sessions import GroupSessions, InMemorySessionStore
from makan.web import create_app
from makan.web.purge import purge_forever

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
CREATE: dict[str, Any] = {
    "latitude": 3.1481,
    "longitude": 101.6951,
    "request": "dinner",
    "display_name": "Alex",
}


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


def eventually(condition: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def app_over(sessions: GroupSessions) -> TestClient:
    app = create_app(
        provider=FakeProvider([]),
        places=FakePlacesProvider([]),
        config=Config(model="test/classifier"),
        sessions=sessions,
    )
    return TestClient(app)


def test_the_app_purges_expired_sessions_at_startup() -> None:
    clock = Clock()
    store = InMemorySessionStore()
    old = GroupSessions(store, retention=timedelta(hours=1), clock=clock)
    fresh = GroupSessions(store, retention=timedelta(hours=24), clock=clock)
    expired, expired_host = old.create(
        latitude=3.1, longitude=101.6, request="dinner", host_name="Alex"
    )
    old.join(str(expired.link_token), display_name="Sam")
    kept, kept_host = fresh.create(latitude=3.1, longitude=101.6, request="dinner", host_name="Bo")
    clock.now = T0 + timedelta(hours=2)  # the first session is now past its expiry

    # Reads already refuse it, but its rows are still stored until the purge runs.
    assert store.get_by_token(expired.link_token) is not None
    assert store.participants(expired.id) != []

    with app_over(fresh):
        assert eventually(lambda: store.get_by_token(expired.link_token) is None)
        assert store.participants(expired.id) == []
        assert store.get_participant(expired_host.id) is None
        assert store.get_by_token(kept.link_token) is not None
        assert store.get_participant(kept_host.id) is not None


def test_creating_an_app_does_not_purge_until_it_starts() -> None:
    clock = Clock()
    store = InMemorySessionStore()
    groups = GroupSessions(store, retention=timedelta(hours=1), clock=clock)
    session, _ = groups.create(latitude=3.1, longitude=101.6, request="dinner", host_name="Alex")
    clock.now = T0 + timedelta(hours=2)
    app_over(groups)  # built, never started
    time.sleep(0.05)
    assert store.get_by_token(session.link_token) is not None


def test_the_app_stops_purging_when_it_shuts_down() -> None:
    clock = Clock()
    store = InMemorySessionStore()
    groups = GroupSessions(store, retention=timedelta(hours=1), clock=clock)
    with app_over(groups):
        pass
    session, _ = groups.create(latitude=3.1, longitude=101.6, request="dinner", host_name="Alex")
    clock.now = T0 + timedelta(hours=2)
    time.sleep(0.05)
    assert store.get_by_token(session.link_token) is not None


def test_the_purge_loop_keeps_removing_sessions_as_they_expire() -> None:
    clock = Clock()
    store = InMemorySessionStore()
    groups = GroupSessions(store, retention=timedelta(hours=1), clock=clock)
    first, _ = groups.create(latitude=3.1, longitude=101.6, request="dinner", host_name="Alex")

    async def scenario() -> None:
        task = asyncio.create_task(purge_forever(groups, 0.01))
        try:
            await asyncio.sleep(0.05)
            assert store.get_by_token(first.link_token) is not None  # not expired yet
            clock.now = T0 + timedelta(hours=2)
            for _ in range(200):
                if store.get_by_token(first.link_token) is None:
                    break
                await asyncio.sleep(0.01)
            assert store.get_by_token(first.link_token) is None
            later, _ = groups.create(
                latitude=3.1, longitude=101.6, request="dinner", host_name="Bo"
            )
            clock.now = T0 + timedelta(hours=4)  # a later round catches sessions made after startup
            for _ in range(200):
                if store.get_by_token(later.link_token) is None:
                    break
                await asyncio.sleep(0.01)
            assert store.get_by_token(later.link_token) is None
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_a_failed_purge_is_logged_and_the_next_round_still_runs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Flaky(InMemorySessionStore):
        failures = 1

        def purge_expired(self, now: datetime) -> int:
            if self.failures:
                self.failures -= 1
                raise RuntimeError("database is down")
            return super().purge_expired(now)

    clock = Clock()
    store = Flaky()
    groups = GroupSessions(store, retention=timedelta(hours=1), clock=clock)
    session, _ = groups.create(latitude=3.1, longitude=101.6, request="dinner", host_name="Alex")
    clock.now = T0 + timedelta(hours=2)

    async def scenario() -> None:
        task = asyncio.create_task(purge_forever(groups, 0.01))
        try:
            for _ in range(200):
                if store.get_by_token(session.link_token) is None:
                    break
                await asyncio.sleep(0.01)
            assert store.get_by_token(session.link_token) is None
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    with caplog.at_level(logging.ERROR, logger="makan.web"):
        asyncio.run(scenario())
    assert "purging expired group sessions failed" in caplog.text
