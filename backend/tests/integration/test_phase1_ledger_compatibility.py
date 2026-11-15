"""Full HTTP responses captured from f560f6c before the member-net refactor."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from app.api.v1.auth import create_access_token
from app.core.ledger import payment_preview, settlement_basis

CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "phase1_ledger_before.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_two_member_ledger_matches_before_refactor(client, monkeypatch, case):
    snapshot = deepcopy(case["snapshot"])
    original = deepcopy(snapshot)

    async def read_snapshot(*args):
        return snapshot

    monkeypatch.setattr("app.api.v1.ledger.snapshot", read_snapshot)
    monkeypatch.setattr("app.core.ledger.business_today", lambda: "2026-11-15")
    response = client.get(
        "/v1/trips/phase1-sample/ledger",
        headers={"Authorization": f"Bearer {create_access_token('a')}"},
    )

    assert response.status_code == 200
    # No field removal, numeric conversion, sorting of arrays, or normalization.
    assert response.json() == case["response"]
    if "preview" in case:
        preview = payment_preview(snapshot)
        assert preview == case["preview"]
        assert {
            g["currency"]: settlement_basis(snapshot, preview, g["currency"])
            for g in preview["groups"]
        } == case["freeze_basis"]
    assert snapshot == original
