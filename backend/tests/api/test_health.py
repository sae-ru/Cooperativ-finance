from uuid import UUID

from fastapi.testclient import TestClient


def test_liveness_has_release_and_request_id(client: TestClient) -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "status": "LIVE",
        "release": "test-release",
        "request_id": response.headers["X-Request-ID"],
        "checks": [],
    }
    UUID(response.headers["X-Request-ID"])


def test_valid_caller_request_id_is_preserved(client: TestClient) -> None:
    request_id = "1e26731b-aac6-41ef-9389-51b86dc6e1cf"

    response = client.get("/health/live", headers={"X-Request-ID": request_id})

    assert response.headers["X-Request-ID"] == request_id
    assert response.json()["request_id"] == request_id


def test_invalid_caller_request_id_is_replaced(client: TestClient) -> None:
    response = client.get("/health/live", headers={"X-Request-ID": "unsafe-value"})

    assert response.headers["X-Request-ID"] != "unsafe-value"
    UUID(response.headers["X-Request-ID"])


def test_readiness_without_runtime_returns_safe_error(client: TestClient) -> None:
    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "SERVICE_NOT_READY",
        "message_key": "errors.system.service_not_ready",
        "parameters": {},
        "field_errors": [],
        "retryable": True,
    }


def test_unknown_route_uses_error_envelope(client: TestClient) -> None:
    response = client.get("/missing")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "HTTP_NOT_FOUND"
    assert response.json()["request_id"] == response.headers["X-Request-ID"]
