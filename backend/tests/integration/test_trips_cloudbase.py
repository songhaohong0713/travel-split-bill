from collections.abc import Generator

import pytest
from app.api.v1.auth import create_access_token
from app.main import app
from app.providers.cloudbase_pg import CloudBasePgRequestError
from expense_samples import payload as valid_payload
from fastapi.testclient import TestClient


class FakeCloudBasePg:
    def __init__(self) -> None:
        self.rpc_calls: list[tuple[str, dict[str, object]]] = []
        self.request_calls: list[tuple[str, str, dict[str, object] | None]] = []

    async def rpc(self, name: str, payload: dict[str, object]) -> dict[str, object]:
        self.rpc_calls.append((name, payload))
        if name == "tsb_list_member_trips":
            return [
                {
                    "id": "trip-1",
                    "name": "Tokyo",
                    "default_currency": "JPY",
                    "member_count": 2,
                }
            ]
        if name.startswith("tsb_delete_owner_"):
            return {"deleted": True}
        return {
            "id": payload["p_trip_id"],
            "name": payload["p_name"],
            "default_currency": payload["p_default_currency"],
        }

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, object] | None = None,
        payload: object = None,
    ) -> list[dict[str, str]]:
        self.request_calls.append((method, path, params))
        if path == "/trip_members":
            return [{"user_id": "owner-1"}]
        return [{"id": "trip-1", "name": "Tokyo", "default_currency": "JPY"}]


@pytest.fixture
def client() -> Generator[tuple[TestClient, FakeCloudBasePg], None, None]:
    provider = FakeCloudBasePg()
    app.state.cloudbase_pg = provider
    with TestClient(app) as test_client:
        yield test_client, provider
    delattr(app.state, "cloudbase_pg")


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token('owner-1')}"}


def test_trips_use_cloudbase_rpc_for_mutation_and_table_api_for_reading(
    client: tuple[TestClient, FakeCloudBasePg],
) -> None:
    test_client, provider = client

    created = test_client.post(
        "/v1/trips",
        json={"name": "Tokyo", "default_currency": "JPY"},
        headers=_headers(),
    )
    listed = test_client.get("/v1/trips", headers=_headers())

    assert created.status_code == 201
    assert provider.rpc_calls[0][0] == "tsb_create_trip_with_cadence"
    assert listed.status_code == 200
    assert provider.rpc_calls[1] == ("tsb_list_member_trips", {"p_user_id": "owner-1"})


def test_cloudbase_expense_revision_conflict_returns_409(
    client: tuple[TestClient, FakeCloudBasePg],
) -> None:
    test_client, provider = client

    async def conflicting_rpc(
        name: str, payload: dict[str, object]
    ) -> dict[str, object]:
        provider.rpc_calls.append((name, payload))
        raise CloudBasePgRequestError(400, "TSB_REVISION_CONFLICT")

    provider.rpc = conflicting_rpc  # type: ignore[method-assign]
    response = test_client.patch(
        "/v1/trips/trip-1/expenses/expense-1",
        headers=_headers(),
        json={
            "revision": 1,
            "occurred_at": "2026-09-28",
            "payload": valid_payload(payer="owner-1"),
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REVISION_CONFLICT"


def test_cloudbase_expense_not_found_result_returns_404(
    client: tuple[TestClient, FakeCloudBasePg],
) -> None:
    test_client, provider = client

    async def missing_rpc(name: str, payload: dict[str, object]) -> dict[str, object]:
        provider.rpc_calls.append((name, payload))
        return {"not_found": True}

    provider.rpc = missing_rpc  # type: ignore[method-assign]
    response = test_client.patch(
        "/v1/trips/trip-1/expenses/expense-1",
        headers=_headers(),
        json={
            "revision": 1,
            "occurred_at": "2026-09-28",
            "payload": valid_payload(payer="owner-1"),
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_cloudbase_expense_malformed_result_returns_503(
    client: tuple[TestClient, FakeCloudBasePg],
) -> None:
    test_client, provider = client

    async def malformed_rpc(name: str, payload: dict[str, object]) -> dict[str, object]:
        provider.rpc_calls.append((name, payload))
        return {"revision": 2}

    provider.rpc = malformed_rpc  # type: ignore[method-assign]
    response = test_client.patch(
        "/v1/trips/trip-1/expenses/expense-1",
        headers=_headers(),
        json={
            "revision": 1,
            "occurred_at": "2026-09-28",
            "payload": valid_payload(payer="owner-1"),
        },
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"


def test_cloudbase_owner_deletion_uses_delete_rpcs(
    client: tuple[TestClient, FakeCloudBasePg],
) -> None:
    test_client, provider = client

    expense = test_client.delete(
        "/v1/trips/trip-1/expenses/expense-1", headers=_headers()
    )
    trip = test_client.delete("/v1/trips/trip-1", headers=_headers())

    assert expense.status_code == trip.status_code == 204
    assert provider.rpc_calls == [
        (
            "tsb_delete_owner_expense",
            {
                "p_trip_id": "trip-1",
                "p_expense_id": "expense-1",
                "p_owner_id": "owner-1",
            },
        ),
        ("tsb_delete_owner_trip", {"p_trip_id": "trip-1", "p_owner_id": "owner-1"}),
    ]


class CadenceCloud(FakeCloudBasePg):
    """Answers the settlement cadence RPCs with a scripted result."""

    def __init__(self) -> None:
        super().__init__()
        self.answers: dict[str, dict[str, object]] = {}
        self.trip_rows: list[dict[str, object]] = [{"id": "trip-1"}]

    async def rpc(self, name: str, payload: dict[str, object]):
        if name in self.answers:
            self.rpc_calls.append((name, payload))
            return self.answers[name]
        return await super().rpc(name, payload)

    async def request(self, method, path, *, params=None, payload=None):
        if path == "/trips":
            self.request_calls.append((method, path, params))
            return self.trip_rows
        return await super().request(method, path, params=params, payload=payload)


@pytest.fixture
def cadence() -> Generator[tuple[TestClient, CadenceCloud], None, None]:
    provider = CadenceCloud()
    app.state.cloudbase_pg = provider
    with TestClient(app) as test_client:
        yield test_client, provider
    delattr(app.state, "cloudbase_pg")


def test_cloudbase_create_sends_cadence_and_dates(cadence) -> None:
    test_client, provider = cadence

    plain = test_client.post(
        "/v1/trips",
        json={"name": "Tokyo", "default_currency": "JPY"},
        headers=_headers(),
    )
    whole = test_client.post(
        "/v1/trips",
        json={
            "name": "Tokyo",
            "default_currency": "JPY",
            "settlement_cadence": "trip",
            "start_date": "2026-11-20",
            "end_date": "2026-11-30",
        },
        headers=_headers(),
    )
    missing = test_client.post(
        "/v1/trips",
        json={"name": "Tokyo", "default_currency": "JPY", "settlement_cadence": "trip"},
        headers=_headers(),
    )

    assert plain.status_code == whole.status_code == 201
    assert set(whole.json()["data"]) == {"id", "name", "default_currency"}
    first, second = (payload for _, payload in provider.rpc_calls)
    assert (
        first["p_settlement_cadence"],
        first["p_start_date"],
        first["p_end_date"],
    ) == ("daily", None, None)
    assert (
        second["p_settlement_cadence"],
        second["p_start_date"],
        second["p_end_date"],
    ) == ("trip", "2026-11-20", "2026-11-30")
    # Refused before anything is sent to the database.
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "END_DATE_REQUIRED"
    assert len(provider.rpc_calls) == 2


def test_cloudbase_create_reports_refusals_of_the_function(cadence) -> None:
    test_client, provider = cadence
    body = {
        "name": "Tokyo",
        "default_currency": "JPY",
        "settlement_cadence": "trip",
        "end_date": "2026-11-30",
    }

    provider.answers["tsb_create_trip_with_cadence"] = {"end_date_required": True}
    refused = test_client.post("/v1/trips", json=body, headers=_headers())
    provider.answers["tsb_create_trip_with_cadence"] = {"conflict": True}
    conflict = test_client.post("/v1/trips", json=body, headers=_headers())

    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "END_DATE_REQUIRED"
    assert conflict.status_code == 409


def test_cloudbase_create_against_a_database_without_the_function_is_unavailable(
    cadence, monkeypatch
) -> None:
    test_client, provider = cadence

    async def missing_function(name, payload):
        raise CloudBasePgRequestError(404, "function not found")

    monkeypatch.setattr(provider, "rpc", missing_function)

    response = test_client.post(
        "/v1/trips",
        json={"name": "Tokyo", "default_currency": "JPY"},
        headers=_headers(),
    )

    assert response.status_code == 503


def test_cloudbase_list_reads_the_cadence_and_defaults_to_daily(cadence) -> None:
    test_client, provider = cadence

    plain = test_client.get("/v1/trips", headers=_headers()).json()["data"][0]
    provider.trip_rows = [
        {"id": "trip-1", "end_date": "2026-11-30", "settlement_cadence": "trip"}
    ]
    whole = test_client.get("/v1/trips", headers=_headers()).json()["data"][0]

    assert plain["settlement_cadence"] == "daily"
    assert (whole["settlement_cadence"], whole["end_date"]) == ("trip", "2026-11-30")
    assert "settlement_cadence" in provider.request_calls[-1][2]["select"].split(",")


def test_cloudbase_cadence_setter_maps_every_result(cadence) -> None:
    test_client, provider = cadence
    path = "/v1/trips/trip-1/settlement-cadence"
    name = "tsb_set_trip_settlement_cadence"

    provider.answers[name] = {"ok": True}
    saved = test_client.patch(
        path, json={"settlement_cadence": "trip"}, headers=_headers()
    )
    provider.answers[name] = {"end_date_required": True}
    refused = test_client.patch(
        path, json={"settlement_cadence": "trip"}, headers=_headers()
    )
    provider.answers[name] = {"not_found": True}
    hidden = test_client.patch(
        path, json={"settlement_cadence": "daily"}, headers=_headers()
    )
    invalid = test_client.patch(
        path, json={"settlement_cadence": "weekly"}, headers=_headers()
    )

    assert saved.status_code == 200
    assert saved.json()["data"] == {"settlement_cadence": "trip"}
    assert provider.rpc_calls[0] == (
        name,
        {"p_trip_id": "trip-1", "p_user_id": "owner-1", "p_settlement_cadence": "trip"},
    )
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "END_DATE_REQUIRED"
    assert hidden.status_code == 404
    assert invalid.status_code == 422
    assert len(provider.rpc_calls) == 3


def test_cloudbase_dates_cannot_drop_the_end_date_of_a_whole_trip(cadence) -> None:
    test_client, provider = cadence
    provider.answers["tsb_update_trip_dates"] = {"end_date_required": True}

    refused = test_client.patch("/v1/trips/trip-1", json={}, headers=_headers())

    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "END_DATE_REQUIRED"
    provider.answers["tsb_update_trip_dates"] = {"ok": True}
    assert (
        test_client.patch(
            "/v1/trips/trip-1", json={"end_date": "2026-11-30"}, headers=_headers()
        ).status_code
        == 200
    )
