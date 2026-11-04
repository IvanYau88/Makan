"""Pure server-side gateway envelope construction, unwired from product requests.

Only trusted Makan servers compute these HMACs. No owner identifier, candidate identifier,
private revision or secret leaves this boundary in plaintext. The body is the native
single-case request built by ClefScorer, independently allowlisted again by the gateway.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from importlib import resources
from typing import Any, Literal

GATEWAY_VERSION = "fit-gateway-v1"
GATEWAY_REASONS_VERSION = 2


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


@dataclass(frozen=True)
class GatewayKeys:
    """Separate secrets and namespaces for preview and production; never browser config."""

    secret: bytes = field(repr=False)
    namespace: Literal["preview", "production"]

    def __post_init__(self) -> None:
        if len(self.secret) < 32 or self.namespace not in ("preview", "production"):
            raise ValueError("invalid gateway HMAC configuration")

    def _digest(self, purpose: str, value: Any) -> str:
        return hmac.new(
            self.secret,
            _canonical(["fit-gateway-hmac-v1", self.namespace, purpose, value]),
            hashlib.sha256,
        ).hexdigest()

    def owner_scope(self, opaque_owner_or_tab_nonce: str) -> str:
        if not opaque_owner_or_tab_nonce or len(opaque_owner_or_tab_nonce) > 512:
            raise ValueError("invalid gateway owner scope")
        return self._digest("scope", opaque_owner_or_tab_nonce)

    def envelope(
        self,
        body: dict[str, Any],
        *,
        opaque_owner_or_tab_nonce: str,
        profile_revision: str,
        evidence_revision: str,
        candidate_identity: str,
        fact_expires_at_ms: int,
        now_ms: int,
    ) -> dict[str, Any]:
        """Use stable source expiry/revisions, never search center, order or request wording.

        candidate_identity must include its provider namespace, for example overture:id.
        The exact body includes source release, canonical profile and candidate fact hash.
        Reconnection reuses the same key, so the gateway returns its persisted attempt id.
        The caller must derive owner/tab scope from authenticated server session state.
        """
        if (
            isinstance(fact_expires_at_ms, bool)
            or not isinstance(fact_expires_at_ms, int)
            or isinstance(now_ms, bool)
            or not isinstance(now_ms, int)
            or not now_ms < fact_expires_at_ms <= 2**53 - 1
            or any(
                not value or len(value) > 512
                for value in (profile_revision, evidence_revision, candidate_identity)
            )
            or ":" not in candidate_identity
        ):
            raise ValueError("invalid gateway case metadata")
        if set(body) != {"model", "state", "questions"} or not isinstance(body["state"], str):
            raise ValueError("invalid native gateway body")
        # Canonicalize state so JSON key order cannot change case identity.
        native = dict(body)
        native["state"] = _canonical(json.loads(body["state"])).decode("utf-8")
        if len(_canonical(native)) > 8192:
            raise ValueError("gateway body exceeds 8 KiB")
        manifest = json.loads(
            resources.files("makan")
            .joinpath("evals", "sets", "clef", "manifest.json")
            .read_text("utf-8")
        )["content_sha256"]
        scope = self.owner_scope(opaque_owner_or_tab_nonce)
        key = self._digest(
            "case",
            [
                GATEWAY_VERSION,
                scope,
                profile_revision,
                evidence_revision,
                candidate_identity,
                hashlib.sha256(_canonical(native)).hexdigest(),
                fact_expires_at_ms,
                manifest,
            ],
        )
        return {
            "v": 1,
            "namespace": self.namespace,
            "scope": scope,
            "key": key,
            "manifest": manifest,
            "expires_at": min(fact_expires_at_ms, now_ms + 86_400_000),
            "body": native,
        }
