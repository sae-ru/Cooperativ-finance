from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from cooperative_clearing.modules.identity.application.service_clients import (
    ServicePrincipal,
    normalize_service_client_config,
)
from cooperative_clearing.modules.identity.domain.types import ServiceScope
from cooperative_clearing.shared.domain.errors import DomainError


def test_service_client_config_normalizes_scopes_networks_and_contact() -> None:
    now = datetime.now(UTC)
    config = normalize_service_client_config(
        display_name="  Farm   accounting bridge ",
        technical_contact_name="  Ivan   Operator ",
        technical_contact_email="OPS@EXAMPLE.ORG",
        scopes=["catalog:read", "catalog:read", "clearing:accounting:read"],
        network_allowlist=["10.10.2.18/24", "127.0.0.1/32"],
        rate_limit_per_minute=120,
        expires_at=now + timedelta(days=30),
        now=now,
    )

    assert config.display_name == "Farm accounting bridge"
    assert config.technical_contact_name == "Ivan Operator"
    assert config.technical_contact_email == "ops@example.org"
    assert config.scopes == ("catalog:read", "clearing:accounting:read")
    assert config.network_allowlist == ("10.10.2.0/24", "127.0.0.1/32")


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("scopes", ["inventory:write"], "SERVICE_SCOPES_INVALID"),
        ("network_allowlist", ["0.0.0.0/0"], "SERVICE_NETWORK_ALLOWLIST_INVALID"),
        ("technical_contact_email", "not-an-email", "SERVICE_CONTACT_EMAIL_INVALID"),
        ("rate_limit_per_minute", 0, "SERVICE_RATE_LIMIT_INVALID"),
    ],
)
def test_service_client_config_rejects_unsafe_values(field: str, value: object, code: str) -> None:
    now = datetime.now(UTC)
    payload: dict[str, object] = {
        "display_name": "Accounting bridge",
        "technical_contact_name": "Ivan Operator",
        "technical_contact_email": "ops@example.org",
        "scopes": ["catalog:read"],
        "network_allowlist": ["127.0.0.1/32"],
        "rate_limit_per_minute": 120,
        "expires_at": now + timedelta(days=30),
        "now": now,
    }
    payload[field] = value
    with pytest.raises(DomainError) as error:
        normalize_service_client_config(**payload)  # type: ignore[arg-type]
    assert error.value.code == code


def test_service_principal_enforces_explicit_scope() -> None:
    principal = ServicePrincipal(
        service_client_id=uuid4(),
        token_id=uuid4(),
        client_code="svc_example",
        owner_cooperative_id=uuid4(),
        scopes=(ServiceScope.CATALOG_READ.value,),
        source_ip="127.0.0.1",
    )

    principal.require_scope(ServiceScope.CATALOG_READ)
    with pytest.raises(DomainError) as denied:
        principal.require_scope(ServiceScope.CLEARING_ACCOUNTING_READ)
    assert denied.value.code == "SERVICE_SCOPE_DENIED"
