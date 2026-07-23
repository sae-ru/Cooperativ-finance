from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from cooperative_clearing.main import create_app
from cooperative_clearing.shared.core.config import Environment, Settings


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = Settings(
        environment=Environment.TEST,
        release="test-release",
        node_code="node-test-01",
        allowed_hosts=["testserver"],
    )
    with TestClient(create_app(settings, manage_runtime=False)) as test_client:
        yield test_client
