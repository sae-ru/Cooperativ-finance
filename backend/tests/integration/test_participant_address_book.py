"""A participant privately manages reusable logistics contact points."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from cooperative_clearing.api.dependencies import get_principal
from cooperative_clearing.cli import initialize_node, seed_demo
from cooperative_clearing.main import create_app
from cooperative_clearing.modules.audit.infrastructure.models import AuditEntry
from cooperative_clearing.modules.identity.application.bootstrap import stable_id
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, RoleGrant
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.infrastructure.database import Database


@pytest.mark.integration
async def test_participant_address_book_is_private_versioned_and_idempotent() -> None:
    settings = Settings(service_name="participant-address-book-integration")
    await initialize_node(settings)
    await seed_demo(settings)
    database = Database.from_settings(settings)
    cooperative_id = stable_id("cooperative", settings.node_code)
    farmer = Principal(
        user_id=stable_id("demo-user", "farmer"),
        session_id=uuid4(),
        login="farmer",
        member_id=stable_id("member", "demo-member-ivan"),
        must_change_password=False,
        roles=(
            RoleGrant(
                stable_id("demo-role", "farmer:EXCHANGE_PARTICIPANT"),
                RoleCode.EXCHANGE_PARTICIPANT,
                cooperative_id,
            ),
        ),
    )
    outsider = Principal(
        user_id=stable_id("bootstrap-user", "auditor"),
        session_id=uuid4(),
        login="auditor",
        member_id=stable_id("member", "demo-member-pavel"),
        must_change_password=False,
        roles=(
            RoleGrant(
                stable_id("bootstrap-role", "auditor:AUDITOR"),
                RoleCode.AUDITOR,
                None,
            ),
        ),
    )

    async def as_farmer() -> Principal:
        return farmer

    async def as_outsider() -> Principal:
        return outsider

    payload = {
        "cooperative_id": str(cooperative_id),
        "label": "Workshop",
        "purpose": "DELIVERY",
        "region_code": "EAST-DISTRICT",
        "address_text": "Private lane 4, workshop gate",
        "contact_name": "Ivan",
        "contact_phone": "+7 900 000-00-01",
        "instructions": "Call before arrival",
        "is_default_pickup": False,
        "is_default_delivery": True,
    }
    app = create_app(settings)
    try:
        app.dependency_overrides[get_principal] = as_farmer
        with TestClient(app) as client:
            seeded = client.get("/api/v1/participant/addresses")
            assert seeded.status_code == 200, seeded.text
            assert {item["label"] for item in seeded.json()["data"]} == {
                "Ферма",
                "Дом",
                "Склад",
            }

            key = f"address-create-{uuid4()}"
            created = client.post(
                "/api/v1/participant/addresses",
                headers={"Idempotency-Key": key},
                json=payload,
            )
            assert created.status_code == 201, created.text
            address_id = created.json()["data"]["object_id"]
            replay = client.post(
                "/api/v1/participant/addresses",
                headers={"Idempotency-Key": key},
                json=payload,
            )
            assert replay.status_code == 201, replay.text
            assert replay.json()["data"] == {
                **created.json()["data"],
                "replayed": True,
            }

            listed = client.get("/api/v1/participant/addresses")
            assert listed.status_code == 200, listed.text
            workshop = next(item for item in listed.json()["data"] if item["id"] == address_id)
            assert workshop["is_default_delivery"] is True
            assert sum(item["is_default_delivery"] for item in listed.json()["data"]) == 1

            updated_payload = {
                **payload,
                "label": "Repair workshop",
                "address_text": "Private lane 4, blue workshop gate",
                "expected_version": workshop["version"],
            }
            updated = client.put(
                f"/api/v1/participant/addresses/{address_id}",
                headers={"Idempotency-Key": f"address-update-{uuid4()}"},
                json=updated_payload,
            )
            assert updated.status_code == 200, updated.text

            stale = client.put(
                f"/api/v1/participant/addresses/{address_id}",
                headers={"Idempotency-Key": f"address-stale-{uuid4()}"},
                json=updated_payload,
            )
            assert stale.status_code == 409, stale.text
            assert stale.json()["error"]["code"] == "PARTICIPANT_ADDRESS_VERSION_CONFLICT"

            app.dependency_overrides[get_principal] = as_outsider
            outsider_list = client.get("/api/v1/participant/addresses")
            assert outsider_list.status_code == 200, outsider_list.text
            assert all(item["id"] != address_id for item in outsider_list.json()["data"])
            denied = client.post(
                f"/api/v1/participant/addresses/{address_id}/archive",
                headers={"Idempotency-Key": f"address-outsider-{uuid4()}"},
                json={"expected_version": 2},
            )
            assert denied.status_code == 404, denied.text

            app.dependency_overrides[get_principal] = as_farmer
            archived = client.post(
                f"/api/v1/participant/addresses/{address_id}/archive",
                headers={"Idempotency-Key": f"address-archive-{uuid4()}"},
                json={"expected_version": 2},
            )
            assert archived.status_code == 200, archived.text
            assert all(
                item["id"] != address_id
                for item in client.get("/api/v1/participant/addresses").json()["data"]
            )

        async with database.session() as session:
            audit = await session.scalar(
                select(AuditEntry)
                .where(
                    AuditEntry.object_id == address_id,
                    AuditEntry.action == "PARTICIPANT_ADDRESS_CREATED",
                )
                .order_by(AuditEntry.occurred_at.desc())
            )
            assert audit is not None
            assert "address_text" not in audit.payload
            assert "contact_phone" not in audit.payload
    finally:
        await database.dispose()
