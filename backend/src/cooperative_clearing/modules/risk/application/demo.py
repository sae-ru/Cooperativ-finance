"""Deterministic bounded-risk demo built through production commands."""

import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.identity.application.bootstrap import stable_id
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode, RoleGrant
from cooperative_clearing.modules.inventory.application.evidence import EvidenceService
from cooperative_clearing.modules.risk.application.service import RiskService
from cooperative_clearing.modules.risk.domain.types import CommitmentType, ShareContour
from cooperative_clearing.modules.risk.infrastructure.models import (
    ExposureCommitment,
    RiskPolicy,
    ShareAccount,
)
from cooperative_clearing.shared.core.config import Settings


async def seed_demo_risk(session: AsyncSession, settings: Settings) -> None:
    cooperative_id = stable_id("cooperative", settings.node_code)
    registrar = _principal(
        "registrar",
        "demo-member-anna",
        cooperative_id,
        (("bootstrap-role", "registrar:COOPERATIVE_ADMIN", RoleCode.COOPERATIVE_ADMIN),),
    )
    security = _principal(
        "security",
        "demo-member-elena",
        cooperative_id,
        (("demo-role", "security:RISK_ADMIN", RoleCode.RISK_ADMIN),),
    )
    policy_proposal_evidence = await _evidence(
        session,
        settings,
        registrar,
        cooperative_id,
        "demo-risk-policy-proposal-v1",
        "Cooperative board proposal for the bounded demo share policy.",
    )
    policy_approval_evidence = await _evidence(
        session,
        settings,
        security,
        cooperative_id,
        "demo-risk-policy-approval-v1",
        "Independent risk review of explicit demo exposure limits.",
    )
    account_evidence = await _evidence(
        session,
        settings,
        registrar,
        cooperative_id,
        "demo-risk-share-register-v1",
        "Opening entry in the demo cooperative share register.",
    )
    farmer_account_evidence = await _evidence(
        session,
        settings,
        registrar,
        cooperative_id,
        "demo-risk-farmer-share-register-v1",
        "Opening entry for the ordinary demo member in the cooperative share register.",
    )
    service = RiskService(settings)
    proposed_policy = await service.propose_policy(
        session,
        principal=registrar,
        cooperative_id=cooperative_id,
        denomination="DEMO_SHARE",
        max_member_exposure=Decimal("60"),
        max_related_exposure=Decimal("100"),
        max_guarantee_chain_depth=3,
        protected_amount_rule=(
            "The protected amount and all primary shares are excluded from automatic exposure."
        ),
        related_party_rule=(
            "Active related-party groups share one explicit aggregate exposure ceiling."
        ),
        approval_reference="DEMO-BOARD-RISK-POLICY-V1",
        evidence_ids=[policy_proposal_evidence],
        idempotency_key="demo-risk-policy-propose-v1",
        request_id=None,
    )
    policy = await session.get(RiskPolicy, proposed_policy.object_id)
    if policy is None:
        raise RuntimeError("demo risk policy was not created")
    await service.approve_policy(
        session,
        principal=security,
        policy_id=policy.id,
        terms_hash=policy.terms_hash,
        expected_version=1,
        evidence_ids=[policy_approval_evidence],
        idempotency_key="demo-risk-policy-approve-v1",
        request_id=None,
    )
    opened = await service.open_account(
        session,
        principal=registrar,
        policy_id=policy.id,
        member_id=stable_id("member", "demo-member-anna"),
        contour=ShareContour.GUARANTEE,
        opening_balance=Decimal("100"),
        protected_amount=Decimal("40"),
        source_reference="DEMO-SHARE-REGISTER-ANNA-V1",
        evidence_ids=[account_evidence],
        idempotency_key="demo-risk-account-open-anna-v1",
        request_id=None,
    )
    account = await session.get(ShareAccount, opened.object_id)
    if account is None:
        raise RuntimeError("demo share account was not created")
    farmer_opened = await service.open_account(
        session,
        principal=registrar,
        policy_id=policy.id,
        member_id=stable_id("member", "demo-member-ivan"),
        contour=ShareContour.GUARANTEE,
        opening_balance=Decimal("50"),
        protected_amount=Decimal("10"),
        source_reference="DEMO-SHARE-REGISTER-FARMER-V1",
        evidence_ids=[farmer_account_evidence],
        idempotency_key="demo-risk-account-open-farmer-v1",
        request_id=None,
    )
    farmer_account = await session.get(ShareAccount, farmer_opened.object_id)
    if farmer_account is None:
        raise RuntimeError("demo farmer share account was not created")

    proposed_commitment = await service.propose_commitment(
        session,
        principal=security,
        account_id=account.id,
        policy_id=policy.id,
        commitment_type=CommitmentType.DIRECT_OBLIGATION,
        risk_type="DEMO_DELIVERY",
        risk_id=stable_id("demo-risk", "limited-cabbage-delivery-v1"),
        debtor_member_id=account.member_id,
        beneficiary_member_id=None,
        role_assignment_id=None,
        amount_reserved=Decimal("30"),
        max_loss=Decimal("25"),
        coverage_ratio=Decimal("0.833333"),
        starts_at=datetime(2026, 1, 1, tzinfo=UTC),
        expires_at=datetime(2035, 1, 31, tzinfo=UTC),
        release_condition="Verified fulfillment or an independent release decision.",
        trigger_conditions="Documented non-performance after the agreed due date.",
        exclusions="Protected amount, primary shares, and documented force majeure.",
        idempotency_key="demo-risk-commitment-propose-v1",
        request_id=None,
    )
    commitment = await session.get(ExposureCommitment, proposed_commitment.object_id)
    if commitment is None:
        raise RuntimeError("demo exposure commitment was not created")
    await service.accept_commitment(
        session,
        principal=registrar,
        commitment_id=commitment.id,
        terms_hash=commitment.terms_hash,
        expected_version=1,
        idempotency_key="demo-risk-commitment-accept-v1",
        request_id=None,
    )
    await session.flush()
    if (
        policy.status != "ACTIVE"
        or account.status != "ACTIVE"
        or account.balance < account.protected_amount
        or account.protected_amount != Decimal("40")
        or farmer_account.status != "ACTIVE"
        or farmer_account.balance < farmer_account.protected_amount
        or farmer_account.protected_amount != Decimal("10")
        or commitment.status != "ACTIVE"
        or commitment.max_loss != Decimal("25")
    ):
        raise RuntimeError("demo bounded-risk flow was not completed")


async def _evidence(
    session: AsyncSession,
    settings: Settings,
    principal: Principal,
    cooperative_id: UUID,
    key: str,
    text: str,
) -> UUID:
    content = text.encode("utf-8")
    service = EvidenceService(settings)
    intent = await service.create_intent(
        session,
        principal=principal,
        cooperative_id=cooperative_id,
        expected_sha256=hashlib.sha256(content).hexdigest(),
        expected_size=len(content),
        mime_type="text/plain",
        kind="POLICY",
        original_name=f"{key}.txt",
        access_scope="COOPERATIVE",
        retention_until=None,
        idempotency_key=f"{key}-intent",
        request_id=None,
    )

    async def chunks() -> AsyncIterator[bytes]:
        yield content

    await service.store_content(
        session,
        principal=principal,
        evidence_id=intent.object_id,
        chunks=chunks(),
        request_id=None,
    )
    return intent.object_id


def _principal(
    login: str,
    member_key: str,
    cooperative_id: UUID,
    roles: tuple[tuple[str, str, RoleCode], ...],
) -> Principal:
    return Principal(
        user_id=stable_id("bootstrap-user", login),
        session_id=stable_id("demo-session", login),
        login=login,
        member_id=stable_id("member", member_key),
        must_change_password=False,
        roles=tuple(
            RoleGrant(stable_id(kind, value), role, cooperative_id) for kind, value, role in roles
        ),
    )
