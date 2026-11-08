"""Offline gateway contract checks; no application wiring, credentials or network."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from makan.providers.scoring.base import ErrorKind, Status
from makan.providers.scoring.clef import ClefScorer
from makan.providers.scoring.gateway import GATEWAY_REASONS_VERSION, GATEWAY_VERSION, GatewayKeys
from makan.providers.scoring.transport import ClefReason, CloudflareTransport

ROOT = Path(__file__).resolve().parents[1]
CLEF = ROOT / "src/makan/evals/sets/clef"
NOW = 1_798_027_200_000


def native_body() -> dict[str, Any]:
    contract = json.loads((CLEF / "contract.json").read_text())
    return {
        "model": contract["selector"],
        "state": json.dumps(
            {
                "profile": {"likes": ["thai"], "dislikes": [], "source": "user_stated"},
                "candidate": {
                    "categories": ["thai_restaurant"],
                    "source": "overture_category",
                    "source_release": "synthetic-release",
                    "missing_facts": [],
                },
            }
        ),
        "questions": {
            key: {field: value[field] for field in ("type", "instructions", "criteria")}
            for key, value in contract["questions"].items()
        },
    }


def envelope(keys: GatewayKeys, **changes: Any) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "opaque_owner_or_tab_nonce": "synthetic-tab-nonce",
        "profile_revision": "1",
        "evidence_revision": "1",
        "candidate_identity": "overture:synthetic-place",
        "fact_expires_at_ms": NOW + 172_800_000,
        "now_ms": NOW,
    }
    metadata.update(changes)
    return keys.envelope(native_body(), **metadata)


def test_gateway_keys_are_stable_owner_scoped_and_private() -> None:
    keys = GatewayKeys(b"synthetic-offline-hmac-secret-value", "preview")
    first = envelope(keys)
    # A reconnect at another wall-clock time retains its unique case key.
    assert envelope(keys, now_ms=NOW + 1000)["key"] == first["key"]
    assert first["expires_at"] == NOW + 86_400_000
    for change in (
        {"opaque_owner_or_tab_nonce": "other-tab"},
        {"profile_revision": "2"},
        {"evidence_revision": "2"},
        {"candidate_identity": "overture:other-place"},
        {"fact_expires_at_ms": NOW + 1000},
    ):
        assert envelope(keys, **change)["key"] != first["key"]
    assert envelope(GatewayKeys(keys.secret, "production"))["key"] != first["key"]
    assert (
        envelope(GatewayKeys(b"another-synthetic-hmac-secret-value", "preview"))["key"]
        != first["key"]
    )
    wire = json.dumps(first)
    assert "synthetic-tab-nonce" not in wire
    assert "synthetic-place" not in wire
    assert keys.secret.decode() not in wire
    assert keys.secret.decode() not in repr(keys)
    assert len(first["key"]) == len(first["scope"]) == 64


def test_gateway_key_covers_actual_soft_facts_and_ignores_json_key_order() -> None:
    keys = GatewayKeys(b"synthetic-offline-hmac-secret-value", "preview")
    metadata: dict[str, Any] = {
        "opaque_owner_or_tab_nonce": "tab",
        "profile_revision": "1",
        "evidence_revision": "1",
        "candidate_identity": "overture:id",
        "fact_expires_at_ms": NOW + 1000,
        "now_ms": NOW,
    }
    body = native_body()
    first = keys.envelope(body, **metadata)
    state = json.loads(body["state"])
    body["state"] = json.dumps(dict(reversed(list(state.items()))))
    assert keys.envelope(body, **metadata)["key"] == first["key"]
    state["profile"]["likes"] = ["italian"]
    body["state"] = json.dumps(state)
    assert keys.envelope(body, **metadata)["key"] != first["key"]


@pytest.mark.parametrize(
    "change",
    [
        {"fact_expires_at_ms": NOW},
        {"fact_expires_at_ms": True},
        {"candidate_identity": "missing-provider"},
        {"profile_revision": ""},
    ],
)
def test_invalid_key_metadata_is_rejected(change: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="metadata"):
        envelope(GatewayKeys(b"synthetic-offline-hmac-secret-value", "preview"), **change)


def test_gateway_protocol_versions_and_reasons_match_python() -> None:
    config = (ROOT / "cloudflare/fit-gateway/src/config.ts").read_text()
    manifest = json.loads((CLEF / "manifest.json").read_text())
    assert f'"{GATEWAY_VERSION}"' in config
    assert f"reasonsVersion: {GATEWAY_REASONS_VERSION}" in config
    assert manifest["content_sha256"] in config
    for reason in ClefReason:
        assert f'"{reason.value}"' in config


def test_projected_gateway_response_is_accepted_by_native_adapter() -> None:
    # Shared synthetic fixture also runs through the gateway validator in Workers tests.
    fixture = json.loads((ROOT / "cloudflare/fit-gateway/test/fixtures/protocol.json").read_text())
    expected = GatewayKeys(b"synthetic-offline-hmac-secret-value", "preview").envelope(
        fixture["request"]["body"],
        opaque_owner_or_tab_nonce="synthetic-tab",
        profile_revision="1",
        evidence_revision="1",
        candidate_identity="overture:synthetic-place",
        fact_expires_at_ms=1798027201000,
        now_ms=1798027200000,
    )
    assert expected == fixture["request"]
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=fixture["response"])

    client = httpx.Client(transport=httpx.MockTransport(handle))
    scorer = ClefScorer(
        CloudflareTransport(
            "0" * 32,
            "synthetic-token",
            "@cf/cloudflare/clef-flash",
            client=client,
        ),
        "@cf/cloudflare/clef-flash",
    )
    result = scorer.score_profile_case(json.loads(fixture["request"]["body"]["state"]))
    assert result.choice.status is Status.OK
    assert result.ordinal is not None
    assert len(requests) == 1
    assert json.loads(requests[0].content) == fixture["request"]["body"]
    assert scorer._failure(ClefReason.QUOTA_EXHAUSTED).error is ErrorKind.BUDGET


def test_history_state_fixture_is_the_builders_output_under_its_own_manifest() -> None:
    # The same fixture runs through the gateway's version 2 validator in the Workers tests.
    from tests.test_visit_evidence import synthetic_history_state

    path = ROOT / "cloudflare/fit-gateway/test/fixtures/protocol-history.json"
    fixture = json.loads(path.read_text())
    body = ClefScorer(None, "@cf/cloudflare/clef-flash").profile_body(synthetic_history_state())
    keys = GatewayKeys(b"synthetic-offline-hmac-secret-value", "preview")
    expected = keys.envelope(
        body,
        opaque_owner_or_tab_nonce="synthetic-owner",
        profile_revision="1",
        evidence_revision="1",
        candidate_identity="overture:synthetic-place",
        fact_expires_at_ms=1798027201000,
        now_ms=1798027200000,
    )
    assert expected == fixture["request"]
    v2 = json.loads((CLEF / "v2/manifest.json").read_text())["content_sha256"]
    v1 = json.loads((CLEF / "manifest.json").read_text())["content_sha256"]
    assert fixture["request"]["manifest"] == v2 != v1
    # The same profile without history is version 1, so its key is in another namespace.
    plain = json.loads(body["state"])
    plain.pop("history")
    without = keys.envelope(
        {**body, "state": json.dumps(plain)},
        opaque_owner_or_tab_nonce="synthetic-owner",
        profile_revision="1",
        evidence_revision="1",
        candidate_identity="overture:synthetic-place",
        fact_expires_at_ms=1798027201000,
        now_ms=1798027200000,
    )
    assert without["manifest"] == v1 and without["key"] != expected["key"]
