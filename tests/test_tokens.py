"""Verifying Supabase access tokens against a published key set, with the network mocked."""

import time
from collections.abc import Callable
from typing import Any
from uuid import uuid4

import jwt
import pytest

from makan.accounts import AuthUnavailable, InvalidToken
from makan.accounts.tokens import TokenVerifier
from tests.auth_helpers import ISSUER, JWKS_URL, FakeSupabase, SigningKey


def verifier(
    fake: FakeSupabase,
    *,
    cache_seconds: float = 600.0,
    refetch_seconds: float = 30.0,
    clock: Callable[[], float] = time.monotonic,
) -> TokenVerifier:
    return TokenVerifier(
        JWKS_URL,
        ISSUER,
        client=fake.client(),
        cache_seconds=cache_seconds,
        refetch_seconds=refetch_seconds,
        clock=clock,
    )


def test_a_valid_token_gives_the_user_and_email() -> None:
    fake = FakeSupabase()
    user = uuid4()
    identity = verifier(fake).verify(fake.key.token(user, email="bob@example.com"))
    assert identity.user_id == user
    assert identity.email == "bob@example.com"


def test_the_keys_are_fetched_once_and_cached() -> None:
    fake = FakeSupabase()
    v = verifier(fake)
    for _ in range(3):
        v.verify(fake.key.token())
    assert [c for c in fake.calls if c[1].endswith("jwks.json")] == [
        ("GET", "/auth/v1/.well-known/jwks.json")
    ]


@pytest.mark.parametrize(
    "claims",
    [
        {"aud": "someone-else"},
        {"iss": "https://elsewhere.test/auth/v1"},
        {"role": "anon"},
        {"sub": "not-a-uuid"},
        {"sub": None},
        {"exp": None},
    ],
)
def test_a_token_with_the_wrong_or_missing_claim_is_rejected(claims: dict[str, Any]) -> None:
    fake = FakeSupabase()
    with pytest.raises(InvalidToken):
        verifier(fake).verify(fake.key.token(**claims))


def test_an_expired_token_says_so() -> None:
    fake = FakeSupabase()
    with pytest.raises(InvalidToken) as caught:
        verifier(fake).verify(fake.key.token(exp=int(time.time()) - 3600))
    assert caught.value.expired


def test_a_token_signed_by_another_key_is_rejected() -> None:
    fake = FakeSupabase()
    forger = SigningKey(kid=fake.key.kid)  # same key id, different key
    with pytest.raises(InvalidToken) as caught:
        verifier(fake).verify(forger.token())
    assert not caught.value.expired


def test_a_symmetric_algorithm_is_never_accepted() -> None:
    fake = FakeSupabase()
    forged = jwt.encode(
        {
            "iss": ISSUER,
            "aud": "authenticated",
            "sub": str(uuid4()),
            "role": "authenticated",
            "exp": int(time.time()) + 600,
        },
        "x" * 64,
        algorithm="HS256",
        headers={"kid": fake.key.kid},
    )
    with pytest.raises(InvalidToken):
        verifier(fake).verify(forged)


@pytest.mark.parametrize("token", ["", "abc", "a.b.c", "Bearer x"])
def test_garbage_is_rejected_without_a_network_call(token: str) -> None:
    fake = FakeSupabase()
    with pytest.raises(InvalidToken):
        verifier(fake).verify(token)
    assert fake.calls == []


def test_a_rotated_key_is_picked_up_but_unknown_keys_do_not_flood_supabase() -> None:
    now = [1000.0]
    old = SigningKey("old")
    fake = FakeSupabase(old)
    v = verifier(fake, refetch_seconds=30, clock=lambda: now[0])
    v.verify(old.token())

    new = SigningKey("new")
    fake.keys = [old, new]
    now[0] += 31
    assert v.verify(new.token())  # one refetch finds the new key

    stranger = SigningKey("stranger")
    before = len(fake.calls)
    for _ in range(5):
        with pytest.raises(InvalidToken):
            v.verify(stranger.token())
    assert len(fake.calls) == before  # inside the refetch interval, so no new requests

    now[0] += 31
    with pytest.raises(InvalidToken):
        v.verify(stranger.token())
    assert len(fake.calls) == before + 1


def test_cached_keys_keep_working_when_supabase_goes_down() -> None:
    now = [0.0]
    fake = FakeSupabase()
    v = verifier(fake, cache_seconds=600, clock=lambda: now[0])
    v.verify(fake.key.token())
    fake.jwks_down = True
    now[0] += 601
    assert v.verify(fake.key.token())


def test_with_no_cached_keys_and_supabase_down_the_error_is_unavailable() -> None:
    fake = FakeSupabase()
    fake.jwks_down = True
    with pytest.raises(AuthUnavailable):
        verifier(fake).verify(fake.key.token())


def test_a_key_the_code_cannot_use_is_skipped_not_fatal() -> None:
    fake = FakeSupabase()
    original = fake.handle

    def handle(request):  # type: ignore[no-untyped-def]
        response = original(request)
        if request.url.path.endswith("jwks.json"):
            import httpx

            return httpx.Response(
                200,
                json={"keys": [{"kid": "weird", "kty": "oct", "k": "AAAA"}, fake.key.jwk()]},
            )
        return response

    fake.handle = handle  # type: ignore[method-assign]
    assert verifier(fake).verify(fake.key.token())
