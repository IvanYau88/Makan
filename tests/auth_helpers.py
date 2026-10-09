"""A fake Supabase project for tests: a signing key, its JWKS, tokens, and the admin API.

Nothing here touches the network. `httpx.MockTransport` answers the two Supabase calls the
backend makes, and the keys are generated for each run, so no real key or token is in the tests.
"""

import json
import time
from typing import Any
from uuid import UUID, uuid4

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import ec

from makan.accounts import Accounts, InMemoryAccountStore
from makan.accounts.supabase import SupabaseAdmin
from makan.accounts.tokens import TokenVerifier
from makan.config import SupabaseConfig
from makan.memory import Memory
from makan.visits import Visits
from makan.web.accounts import AccountServices

URL = "https://project.supabase.test"
ISSUER = f"{URL}/auth/v1"
JWKS_URL = f"{ISSUER}/.well-known/jwks.json"
SERVICE_KEY = "service-role-key-for-tests-only"
ANON_KEY = "anon-key-for-tests-only"


class SigningKey:
    def __init__(self, kid: str = "key-1") -> None:
        self.kid = kid
        self.private = ec.generate_private_key(ec.SECP256R1())

    def jwk(self) -> dict[str, Any]:
        document = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(self.private.public_key()))
        return {**document, "kid": self.kid, "alg": "ES256", "use": "sig"}

    def token(self, user_id: UUID | None = None, *, algorithm: str = "ES256", **claims: Any) -> str:
        now = int(time.time())
        payload: dict[str, Any] = {
            "iss": ISSUER,
            "aud": "authenticated",
            "sub": str(user_id or uuid4()),
            "role": "authenticated",
            "iat": now,
            "exp": now + 3600,
            "email": "bob@example.com",
            **claims,
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        return jwt.encode(payload, self.private, algorithm=algorithm, headers={"kid": self.kid})


class FakeSupabase:
    """The JWKS endpoint and the Auth admin API, with the users it knows and every call it saw."""

    def __init__(self, *keys: SigningKey) -> None:
        self.keys = list(keys) or [SigningKey()]
        self.users: set[UUID] = set()
        self.calls: list[tuple[str, str]] = []
        self.headers: list[dict[str, str]] = []
        self.jwks_down = False
        self.admin_status: int | None = None  # force every admin call to answer this status

    @property
    def key(self) -> SigningKey:
        return self.keys[0]

    def sign_up(self) -> UUID:
        user_id = uuid4()
        self.users.add(user_id)
        return user_id

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        self.headers.append(dict(request.headers))
        if request.url.path.endswith("/.well-known/jwks.json"):
            if self.jwks_down:
                return httpx.Response(503)
            return httpx.Response(200, json={"keys": [k.jwk() for k in self.keys]})
        if "/admin/users/" in request.url.path:
            if self.admin_status is not None:
                return httpx.Response(self.admin_status)
            user_id = UUID(request.url.path.rsplit("/", 1)[1])
            known = user_id in self.users
            if request.method == "DELETE":
                self.users.discard(user_id)
                return httpx.Response(200 if known else 404)
            return httpx.Response(200 if known else 404)
        return httpx.Response(404)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))


def services(
    fake: FakeSupabase | None = None, store: InMemoryAccountStore | None = None
) -> tuple[AccountServices, FakeSupabase, InMemoryAccountStore]:
    fake = fake or FakeSupabase()
    store = store or InMemoryAccountStore()
    accounts = Accounts(
        store, Memory(store.memory), SupabaseAdmin(URL, SERVICE_KEY, client=fake.client())
    )
    verifier = TokenVerifier(JWKS_URL, ISSUER, client=fake.client())
    visits = Visits(store.visits, store)
    return (
        AccountServices(accounts, verifier, SupabaseConfig(URL, ANON_KEY, SERVICE_KEY), visits),
        fake,
        store,
    )
