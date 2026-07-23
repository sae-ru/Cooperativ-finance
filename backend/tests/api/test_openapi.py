import json
from pathlib import Path

from fastapi.testclient import TestClient


def test_openapi_contains_versioned_contract_and_health(client: TestClient) -> None:
    document = client.get("/api/openapi.json").json()

    assert document["info"]["version"] == "0.1.0"
    assert "/api/v1/system/status" in document["paths"]
    assert "/api/v1/exchange/deals" in document["paths"]
    assert "/api/v1/exchange/disputes/{dispute_id}/resolution" in document["paths"]
    assert "/api/v1/clearing/cycles" in document["paths"]
    assert "/api/v1/clearing/cycles/{cycle_id}/finalize" in document["paths"]
    assert "/api/v1/trust/cases" in document["paths"]
    assert "/api/v1/trust/appeals/{appeal_id}/decision" in document["paths"]
    assert "/api/v1/trust/workspaces/arbitrator" in document["paths"]
    assert "/api/v1/trust/workspaces/auditor" in document["paths"]
    assert "/api/v1/solidarity/campaigns" in document["paths"]
    assert "/api/v1/solidarity/allocations/{allocation_id}/approval" in document["paths"]
    assert "/api/v1/solidarity/workspaces/controller" in document["paths"]
    assert "/api/v1/federation/nodes/applications" in document["paths"]
    assert "/api/v1/federation/trust-contracts" in document["paths"]
    assert "/api/v1/federation/nodes/{node_id}/offline-epochs" in document["paths"]
    assert "/api/v1/federation/sync/packages/export" in document["paths"]
    assert "/api/v1/federation/sync/packages/import" in document["paths"]
    assert "/api/v1/federation/sync/packages/{package_id}/apply" in document["paths"]
    assert "/api/v1/federation/sync/conflicts/{conflict_id}/resolution" in document["paths"]
    assert "/api/v1/federation/workspaces/{workspace}" in document["paths"]
    operation_ids = [
        operation["operationId"]
        for path in document["paths"].values()
        for operation in path.values()
        if isinstance(operation, dict) and "operationId" in operation
    ]
    assert len(operation_ids) == len(set(operation_ids))
    assert "/health/live" in document["paths"]
    assert "/health/ready" in document["paths"]


def test_committed_openapi_snapshot_matches_application(client: TestClient) -> None:
    snapshot = Path(__file__).resolve().parents[2] / "openapi.json"
    committed = json.loads(snapshot.read_text(encoding="utf-8"))
    assert client.get("/api/openapi.json").json() == committed
