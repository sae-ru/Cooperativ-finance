"""Cross-domain explainable anti-fraud rules added by algorithm 2.0.0."""

import json
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.crisis.infrastructure.models import ReserveTarget
from cooperative_clearing.modules.identity.infrastructure.models import Member
from cooperative_clearing.modules.inventory.domain.types import decimal_text
from cooperative_clearing.modules.inventory.infrastructure.models import (
    InventoryLot,
    Product,
)
from cooperative_clearing.modules.risk.domain.types import (
    AntifraudAction,
    AntifraudFinding,
    AntifraudRuleCode,
    AntifraudSeverity,
    AntifraudSubjectType,
)
from cooperative_clearing.modules.risk.infrastructure.models import (
    ExposureCommitment,
    RelatedPartyLink,
    RiskPolicy,
)
from cooperative_clearing.modules.solidarity.infrastructure.models import (
    AidAllocation,
    AidCampaign,
    AllocationApproval,
    Contribution,
)
from cooperative_clearing.modules.trust.infrastructure.models import (
    ArbitrationDecision,
    ReputationEvent,
    Sanction,
    TrustCase,
)

RELATED_STATUS = "ACTIVE"
POSITIVE_REPUTATION = "FULFILLED"
ACTIVE_REPUTATION = "ACTIVE"


def _ordered_pair(left: UUID, right: UUID) -> tuple[UUID, UUID]:
    return (left, right) if str(left) < str(right) else (right, left)


def _related_components(
    pairs: set[tuple[UUID, UUID]],
) -> dict[UUID, frozenset[UUID]]:
    parent: dict[UUID, UUID] = {}

    def find(member_id: UUID) -> UUID:
        parent.setdefault(member_id, member_id)
        while parent[member_id] != member_id:
            parent[member_id] = parent[parent[member_id]]
            member_id = parent[member_id]
        return member_id

    for left, right in pairs:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root
    components: dict[UUID, set[UUID]] = defaultdict(set)
    for member_id in parent:
        components[find(member_id)].add(member_id)
    return {
        member_id: frozenset(component)
        for component in components.values()
        for member_id in component
    }


def _qualified_reputation_window(
    events: list[ReputationEvent],
    *,
    maximum_window: timedelta,
) -> list[ReputationEvent]:
    ordered = sorted(events, key=lambda item: (item.created_at, str(item.id)))
    best: list[ReputationEvent] = []
    left = 0
    for right, event in enumerate(ordered):
        while event.created_at - ordered[left].created_at > maximum_window:
            left += 1
        candidate = ordered[left : right + 1]
        recorders = {item.recorded_by_member_id for item in candidate}
        if len(candidate) < 3 or len(recorders) < 2:
            continue
        if len(candidate) > len(best):
            best = candidate
    return best


def _largest_overlapping_campaign_group(
    campaigns: list[AidCampaign],
) -> list[AidCampaign]:
    ordered = sorted(campaigns, key=lambda item: (item.starts_at, str(item.id)))
    best: list[AidCampaign] = []
    for anchor in ordered:
        candidate = [item for item in ordered if item.starts_at <= anchor.starts_at < item.ends_at]
        if len(candidate) > len(best):
            best = candidate
    return best


async def collect_extended_findings(
    session: AsyncSession,
    *,
    cooperative_id: UUID,
    since: datetime,
    cutoff: datetime,
) -> list[AntifraudFinding]:
    links = list(
        (
            await session.execute(
                select(RelatedPartyLink).where(
                    RelatedPartyLink.cooperative_id == cooperative_id,
                    RelatedPartyLink.status == RELATED_STATUS,
                )
            )
        ).scalars()
    )
    link_by_pair = {_ordered_pair(link.member_a_id, link.member_b_id): link for link in links}
    related_pairs = set(link_by_pair)
    components = _related_components(related_pairs)

    findings: list[AntifraudFinding] = []
    findings.extend(
        await _related_rating_findings(
            session,
            cooperative_id,
            since,
            cutoff,
            link_by_pair,
        )
    )
    findings.extend(await _limit_splitting_findings(session, cooperative_id, since, cutoff))
    findings.extend(await _coefficient_change_findings(session, cooperative_id, since, cutoff))
    findings.extend(
        await _critical_resource_findings(
            session,
            cooperative_id,
            cutoff,
            components,
        )
    )
    findings.extend(
        await _reputation_synchronization_findings(
            session,
            cooperative_id,
            since,
            cutoff,
        )
    )
    findings.extend(
        await _contribution_influence_findings(
            session,
            cooperative_id,
            since,
            cutoff,
        )
    )
    findings.extend(
        await _privileged_conflict_findings(
            session,
            cooperative_id,
            since,
            cutoff,
            related_pairs,
        )
    )
    findings.extend(await _campaign_splitting_findings(session, cooperative_id, since, cutoff))
    findings.extend(
        await _sanction_continuity_findings(
            session,
            cooperative_id,
            cutoff,
            related_pairs,
        )
    )
    unique: dict[
        tuple[AntifraudRuleCode, AntifraudSubjectType, UUID],
        AntifraudFinding,
    ] = {}
    for finding in sorted(
        findings,
        key=lambda item: (
            item.rule_code.value,
            item.subject_type.value,
            str(item.subject_id),
            json.dumps(item.observed_data, sort_keys=True),
        ),
    ):
        unique.setdefault(
            (finding.rule_code, finding.subject_type, finding.subject_id),
            finding,
        )
    return list(unique.values())


async def _related_rating_findings(
    session: AsyncSession,
    cooperative_id: UUID,
    since: datetime,
    cutoff: datetime,
    link_by_pair: dict[tuple[UUID, UUID], RelatedPartyLink],
) -> list[AntifraudFinding]:
    if not link_by_pair:
        return []
    events = list(
        (
            await session.execute(
                select(ReputationEvent).where(
                    ReputationEvent.cooperative_id == cooperative_id,
                    ReputationEvent.classification == POSITIVE_REPUTATION,
                    ReputationEvent.status == ACTIVE_REPUTATION,
                    ReputationEvent.created_at >= since,
                    ReputationEvent.created_at <= cutoff,
                )
            )
        ).scalars()
    )
    by_pair: dict[tuple[UUID, UUID], list[ReputationEvent]] = defaultdict(list)
    for event in events:
        pair = _ordered_pair(event.recorded_by_member_id, event.subject_member_id)
        if pair in link_by_pair:
            by_pair[pair].append(event)
    findings: list[AntifraudFinding] = []
    for pair, rows in by_pair.items():
        directions = {(row.recorded_by_member_id, row.subject_member_id) for row in rows}
        if len(rows) < 2 or len(directions) < 2:
            continue
        for member_id in pair:
            findings.append(
                AntifraudFinding(
                    rule_code=AntifraudRuleCode.RELATED_ACCOUNT_RATING_RING,
                    subject_type=AntifraudSubjectType.MEMBER,
                    subject_id=member_id,
                    severity=AntifraudSeverity.HIGH,
                    automation_action=AntifraudAction.HOLD,
                    reason_key="antifraud.reasons.related_account_rating_ring",
                    observed_data={
                        "related_member_id": str(pair[1] if member_id == pair[0] else pair[0]),
                        "related_link_id": str(link_by_pair[pair].id),
                        "positive_event_count": len(rows),
                        "reputation_event_ids": sorted(str(row.id) for row in rows),
                    },
                    threshold_data={
                        "minimum_positive_event_count": 2,
                        "minimum_direction_count": 2,
                    },
                )
            )
    return findings


async def _limit_splitting_findings(
    session: AsyncSession,
    cooperative_id: UUID,
    since: datetime,
    cutoff: datetime,
) -> list[AntifraudFinding]:
    policies = {
        policy.id: policy
        for policy in (
            await session.execute(
                select(RiskPolicy).where(RiskPolicy.cooperative_id == cooperative_id)
            )
        ).scalars()
    }
    rows = list(
        (
            await session.execute(
                select(ExposureCommitment).where(
                    ExposureCommitment.cooperative_id == cooperative_id,
                    ExposureCommitment.created_at >= since,
                    ExposureCommitment.created_at <= cutoff,
                    ExposureCommitment.status != "CANCELLED",
                )
            )
        ).scalars()
    )
    grouped: dict[tuple[UUID, UUID, str], list[ExposureCommitment]] = defaultdict(list)
    for row in rows:
        grouped[(row.owner_member_id, row.policy_id, row.risk_type)].append(row)
    findings: list[AntifraudFinding] = []
    for (member_id, policy_id, risk_type), commitments in grouped.items():
        policy = policies.get(policy_id)
        if policy is None:
            continue
        fragment_ceiling = policy.max_member_exposure * Decimal("0.30")
        aggregate_floor = policy.max_member_exposure * Decimal("0.80")
        fragments = [item for item in commitments if Decimal(0) < item.max_loss <= fragment_ceiling]
        aggregate = sum((item.max_loss for item in fragments), Decimal(0))
        if len(fragments) < 4 or aggregate < aggregate_floor:
            continue
        findings.append(
            AntifraudFinding(
                rule_code=AntifraudRuleCode.LIMIT_SPLITTING_BURST,
                subject_type=AntifraudSubjectType.MEMBER,
                subject_id=member_id,
                severity=AntifraudSeverity.MEDIUM,
                automation_action=AntifraudAction.WARN,
                reason_key="antifraud.reasons.limit_splitting_burst",
                observed_data={
                    "risk_type": risk_type,
                    "fragment_count": len(fragments),
                    "aggregate_max_loss": decimal_text(aggregate),
                    "commitment_ids": sorted(str(item.id) for item in fragments),
                },
                threshold_data={
                    "minimum_fragment_count": 4,
                    "fragment_maximum_ratio": "0.30",
                    "aggregate_minimum_ratio": "0.80",
                    "member_limit": decimal_text(policy.max_member_exposure),
                },
            )
        )
    return findings


async def _coefficient_change_findings(
    session: AsyncSession,
    cooperative_id: UUID,
    since: datetime,
    cutoff: datetime,
) -> list[AntifraudFinding]:
    policies = list(
        (
            await session.execute(
                select(RiskPolicy).where(
                    RiskPolicy.cooperative_id == cooperative_id,
                    RiskPolicy.status.in_({"ACTIVE", "SUPERSEDED"}),
                    RiskPolicy.created_at <= cutoff,
                )
            )
        ).scalars()
    )
    by_denomination: dict[str, list[RiskPolicy]] = defaultdict(list)
    for policy in policies:
        by_denomination[policy.denomination].append(policy)
    by_actor: dict[UUID, AntifraudFinding] = {}
    for rows in by_denomination.values():
        rows.sort(key=lambda item: (item.policy_version, item.created_at, str(item.id)))
        for previous, current in pairwise(rows):
            if current.created_at < since:
                continue
            member_change = (
                abs(current.max_member_exposure - previous.max_member_exposure)
                / previous.max_member_exposure
            )
            related_change = (
                abs(current.max_related_exposure - previous.max_related_exposure)
                / previous.max_related_exposure
            )
            depth_change = abs(
                current.max_guarantee_chain_depth - previous.max_guarantee_chain_depth
            )
            maximum_change = max(member_change, related_change)
            if maximum_change <= Decimal("0.5") and depth_change < 2:
                continue
            by_actor[current.proposed_by_member_id] = AntifraudFinding(
                rule_code=AntifraudRuleCode.RISK_COEFFICIENT_CHANGE_SPIKE,
                subject_type=AntifraudSubjectType.MEMBER,
                subject_id=current.proposed_by_member_id,
                severity=AntifraudSeverity.MEDIUM,
                automation_action=AntifraudAction.WARN,
                reason_key="antifraud.reasons.risk_coefficient_change_spike",
                observed_data={
                    "previous_policy_id": str(previous.id),
                    "current_policy_id": str(current.id),
                    "member_limit_change_ratio": decimal_text(member_change),
                    "related_limit_change_ratio": decimal_text(related_change),
                    "guarantee_depth_change": depth_change,
                },
                threshold_data={
                    "maximum_change_ratio": "0.5",
                    "maximum_depth_change": 1,
                },
            )
    return list(by_actor.values())


async def _critical_resource_findings(
    session: AsyncSession,
    cooperative_id: UUID,
    cutoff: datetime,
    components: dict[UUID, frozenset[UUID]],
) -> list[AntifraudFinding]:
    if not components:
        return []
    resource_codes = set(
        (
            await session.execute(
                select(ReserveTarget.resource_code).where(
                    ReserveTarget.cooperative_id == cooperative_id,
                    ReserveTarget.status == "ACTIVE",
                )
            )
        ).scalars()
    )
    if not resource_codes:
        return []
    rows = list(
        (
            await session.execute(
                select(InventoryLot, Product)
                .join(Product, Product.id == InventoryLot.product_id)
                .where(
                    InventoryLot.cooperative_id == cooperative_id,
                    InventoryLot.status.in_({"VERIFIED", "FROZEN"}),
                    InventoryLot.current_quantity.is_not(None),
                    InventoryLot.current_quantity > 0,
                    Product.sku.in_(resource_codes),
                    InventoryLot.created_at <= cutoff,
                )
            )
        ).all()
    )
    by_resource: dict[str, list[InventoryLot]] = defaultdict(list)
    for lot, product in rows:
        by_resource[product.sku].append(lot)
    by_member: dict[UUID, tuple[Decimal, AntifraudFinding]] = {}
    for resource_code, lots in by_resource.items():
        total = sum((lot.current_quantity or Decimal(0) for lot in lots), Decimal(0))
        if total <= 0:
            continue
        component_quantity: dict[frozenset[UUID], Decimal] = defaultdict(Decimal)
        owners_by_component: dict[frozenset[UUID], set[UUID]] = defaultdict(set)
        for lot in lots:
            component = components.get(lot.owner_member_id)
            if component is None:
                continue
            component_quantity[component] += lot.current_quantity or Decimal(0)
            owners_by_component[component].add(lot.owner_member_id)
        for component, quantity in component_quantity.items():
            owners = owners_by_component[component]
            ratio = quantity / total
            if len(owners) < 2 or ratio <= Decimal("0.8"):
                continue
            for member_id in owners:
                finding = AntifraudFinding(
                    rule_code=(AntifraudRuleCode.RELATED_CRITICAL_RESOURCE_CONCENTRATION),
                    subject_type=AntifraudSubjectType.MEMBER,
                    subject_id=member_id,
                    severity=AntifraudSeverity.CRITICAL,
                    automation_action=AntifraudAction.HOLD,
                    reason_key=("antifraud.reasons.related_critical_resource_concentration"),
                    observed_data={
                        "resource_code": resource_code,
                        "related_member_ids": sorted(str(item) for item in owners),
                        "component_quantity": decimal_text(quantity),
                        "total_quantity": decimal_text(total),
                        "concentration_ratio": decimal_text(ratio),
                    },
                    threshold_data={
                        "minimum_related_owner_count": 2,
                        "maximum_concentration_ratio": "0.8",
                    },
                )
                previous = by_member.get(member_id)
                if previous is None or ratio > previous[0]:
                    by_member[member_id] = (ratio, finding)
    return [item[1] for item in by_member.values()]


async def _reputation_synchronization_findings(
    session: AsyncSession,
    cooperative_id: UUID,
    since: datetime,
    cutoff: datetime,
) -> list[AntifraudFinding]:
    rows = list(
        (
            await session.execute(
                select(ReputationEvent).where(
                    ReputationEvent.cooperative_id == cooperative_id,
                    ReputationEvent.classification == POSITIVE_REPUTATION,
                    ReputationEvent.status == ACTIVE_REPUTATION,
                    ReputationEvent.created_at >= since,
                    ReputationEvent.created_at <= cutoff,
                )
            )
        ).scalars()
    )
    grouped: dict[tuple[UUID, str], list[ReputationEvent]] = defaultdict(list)
    for row in rows:
        grouped[(row.subject_member_id, row.context)].append(row)
    findings: list[AntifraudFinding] = []
    window = timedelta(minutes=10)
    for (member_id, context), events in grouped.items():
        best = _qualified_reputation_window(events, maximum_window=window)
        recorders = {event.recorded_by_member_id for event in best}
        if not best:
            continue
        findings.append(
            AntifraudFinding(
                rule_code=AntifraudRuleCode.REPUTATION_SYNCHRONIZATION,
                subject_type=AntifraudSubjectType.MEMBER,
                subject_id=member_id,
                severity=AntifraudSeverity.HIGH,
                automation_action=AntifraudAction.WARN,
                reason_key="antifraud.reasons.reputation_synchronization",
                observed_data={
                    "reputation_context": context,
                    "positive_event_count": len(best),
                    "recorder_count": len(recorders),
                    "reputation_event_ids": sorted(str(event.id) for event in best),
                },
                threshold_data={
                    "minimum_positive_event_count": 3,
                    "minimum_recorder_count": 2,
                    "maximum_window_minutes": 10,
                },
            )
        )
    return findings


async def _contribution_influence_findings(
    session: AsyncSession,
    cooperative_id: UUID,
    since: datetime,
    cutoff: datetime,
) -> list[AntifraudFinding]:
    contributions = list(
        (
            await session.execute(
                select(Contribution).where(
                    Contribution.status == "VERIFIED",
                    Contribution.verified_at.is_not(None),
                    Contribution.verified_at >= since,
                    Contribution.verified_at <= cutoff,
                )
            )
        ).scalars()
    )
    campaign_ids = {item.campaign_id for item in contributions}
    if not campaign_ids:
        return []
    valid_campaign_ids = set(
        (
            await session.execute(
                select(AidCampaign.id).where(
                    AidCampaign.id.in_(campaign_ids),
                    AidCampaign.cooperative_id == cooperative_id,
                )
            )
        ).scalars()
    )
    contributions = [item for item in contributions if item.campaign_id in valid_campaign_ids]
    reputations = list(
        (
            await session.execute(
                select(ReputationEvent).where(
                    ReputationEvent.cooperative_id == cooperative_id,
                    ReputationEvent.classification == POSITIVE_REPUTATION,
                    ReputationEvent.status == ACTIVE_REPUTATION,
                    ReputationEvent.created_at >= since,
                    ReputationEvent.created_at <= cutoff,
                )
            )
        ).scalars()
    )
    by_member: dict[UUID, AntifraudFinding] = {}
    maximum_lag = timedelta(hours=72)
    for contribution in contributions:
        if contribution.verified_at is None:
            continue
        matches = [
            event
            for event in reputations
            if event.subject_member_id == contribution.donor_member_id
            and contribution.verified_by_member_id is not None
            and event.recorded_by_member_id == contribution.verified_by_member_id
            and contribution.verified_at <= event.created_at
            and event.created_at - contribution.verified_at <= maximum_lag
        ]
        if not matches:
            continue
        event = min(matches, key=lambda item: (item.created_at, str(item.id)))
        lag_hours = Decimal(
            str((event.created_at - contribution.verified_at).total_seconds() / 3600)
        )
        by_member[contribution.donor_member_id] = AntifraudFinding(
            rule_code=AntifraudRuleCode.CONTRIBUTION_REPUTATION_INFLUENCE,
            subject_type=AntifraudSubjectType.MEMBER,
            subject_id=contribution.donor_member_id,
            severity=AntifraudSeverity.HIGH,
            automation_action=AntifraudAction.WARN,
            reason_key="antifraud.reasons.contribution_reputation_influence",
            observed_data={
                "contribution_id": str(contribution.id),
                "reputation_event_id": str(event.id),
                "verifier_member_id": str(contribution.verified_by_member_id),
                "reputation_context": event.context,
                "lag_hours": decimal_text(lag_hours),
            },
            threshold_data={"maximum_lag_hours": 72},
        )
    return list(by_member.values())


async def _privileged_conflict_findings(
    session: AsyncSession,
    cooperative_id: UUID,
    since: datetime,
    cutoff: datetime,
    related_pairs: set[tuple[UUID, UUID]],
) -> list[AntifraudFinding]:
    if not related_pairs:
        return []
    by_actor: dict[UUID, AntifraudFinding] = {}
    allocation_rows = list(
        (
            await session.execute(
                select(AidAllocation, AllocationApproval)
                .join(
                    AllocationApproval,
                    AllocationApproval.allocation_id == AidAllocation.id,
                )
                .join(AidCampaign, AidCampaign.id == AidAllocation.campaign_id)
                .where(
                    AidCampaign.cooperative_id == cooperative_id,
                    AllocationApproval.decided_at >= since,
                    AllocationApproval.decided_at <= cutoff,
                )
            )
        ).all()
    )
    for allocation, approval in allocation_rows:
        for actor_id, source, event_id in (
            (
                allocation.proposed_by_member_id,
                "SOLIDARITY_PROPOSAL",
                allocation.proposed_event_id,
            ),
            (
                approval.decided_by_member_id,
                "SOLIDARITY_APPROVAL",
                approval.decided_event_id,
            ),
        ):
            pair = _ordered_pair(actor_id, allocation.recipient_member_id)
            if pair not in related_pairs:
                continue
            by_actor[actor_id] = _privileged_conflict_finding(
                actor_id,
                allocation.recipient_member_id,
                source,
                allocation.id,
                event_id,
            )
    decision_rows = list(
        (
            await session.execute(
                select(ArbitrationDecision, TrustCase)
                .join(TrustCase, TrustCase.id == ArbitrationDecision.case_id)
                .where(
                    TrustCase.cooperative_id == cooperative_id,
                    ArbitrationDecision.issued_at >= since,
                    ArbitrationDecision.issued_at <= cutoff,
                )
            )
        ).all()
    )
    for decision, case in decision_rows:
        for affected_member_id in {case.subject_member_id, case.claimant_member_id}:
            pair = _ordered_pair(decision.issued_by_member_id, affected_member_id)
            if pair not in related_pairs:
                continue
            by_actor[decision.issued_by_member_id] = _privileged_conflict_finding(
                decision.issued_by_member_id,
                affected_member_id,
                "ARBITRATION_DECISION",
                decision.id,
                decision.issued_event_id,
            )
    return list(by_actor.values())


def _privileged_conflict_finding(
    actor_id: UUID,
    affected_member_id: UUID,
    source: str,
    decision_id: UUID,
    event_id: UUID,
) -> AntifraudFinding:
    return AntifraudFinding(
        rule_code=AntifraudRuleCode.PRIVILEGED_DECISION_RELATED_PARTY,
        subject_type=AntifraudSubjectType.MEMBER,
        subject_id=actor_id,
        severity=AntifraudSeverity.CRITICAL,
        automation_action=AntifraudAction.HOLD,
        reason_key="antifraud.reasons.privileged_decision_related_party",
        observed_data={
            "affected_member_id": str(affected_member_id),
            "decision_source": source,
            "decision_id": str(decision_id),
            "decision_event_id": str(event_id),
        },
        threshold_data={"related_link_status": RELATED_STATUS},
    )


async def _campaign_splitting_findings(
    session: AsyncSession,
    cooperative_id: UUID,
    since: datetime,
    cutoff: datetime,
) -> list[AntifraudFinding]:
    rows = list(
        (
            await session.execute(
                select(AidCampaign).where(
                    AidCampaign.cooperative_id == cooperative_id,
                    AidCampaign.created_at >= since,
                    AidCampaign.created_at <= cutoff,
                    AidCampaign.status != "CANCELLED",
                )
            )
        ).scalars()
    )
    grouped: dict[tuple[UUID, UUID], list[AidCampaign]] = defaultdict(list)
    for row in rows:
        grouped[(row.created_by_member_id, row.fund_id)].append(row)
    findings: list[AntifraudFinding] = []
    for (member_id, fund_id), campaigns in grouped.items():
        overlapping = _largest_overlapping_campaign_group(campaigns)
        if len(overlapping) < 3:
            continue
        overlap_start = max(item.starts_at for item in overlapping)
        overlap_end = min(item.ends_at for item in overlapping)
        findings.append(
            AntifraudFinding(
                rule_code=AntifraudRuleCode.AID_CAMPAIGN_SPLITTING,
                subject_type=AntifraudSubjectType.MEMBER,
                subject_id=member_id,
                severity=AntifraudSeverity.HIGH,
                automation_action=AntifraudAction.HOLD,
                reason_key="antifraud.reasons.aid_campaign_splitting",
                observed_data={
                    "fund_id": str(fund_id),
                    "campaign_count": len(overlapping),
                    "campaign_ids": sorted(str(item.id) for item in overlapping),
                    "overlap_start": overlap_start.isoformat(),
                    "overlap_end": overlap_end.isoformat(),
                },
                threshold_data={
                    "minimum_campaign_count": 3,
                    "requires_overlapping_period": True,
                },
            )
        )
    return findings


async def _sanction_continuity_findings(
    session: AsyncSession,
    cooperative_id: UUID,
    cutoff: datetime,
    related_pairs: set[tuple[UUID, UUID]],
) -> list[AntifraudFinding]:
    if not related_pairs:
        return []
    sanctions = list(
        (
            await session.execute(
                select(Sanction)
                .join(TrustCase, TrustCase.id == Sanction.case_id)
                .where(
                    TrustCase.cooperative_id == cooperative_id,
                    Sanction.status == "ACTIVE",
                    Sanction.starts_at <= cutoff,
                    (Sanction.expires_at.is_(None) | (Sanction.expires_at > cutoff)),
                )
            )
        ).scalars()
    )
    member_ids = {member_id for pair in related_pairs for member_id in pair}
    members = {
        member.id: member
        for member in (
            await session.execute(select(Member).where(Member.id.in_(member_ids)))
        ).scalars()
    }
    by_member: dict[UUID, AntifraudFinding] = {}
    for sanction in sanctions:
        for pair in related_pairs:
            if sanction.subject_member_id not in pair:
                continue
            candidate_id = pair[1] if pair[0] == sanction.subject_member_id else pair[0]
            candidate = members.get(candidate_id)
            if (
                candidate is None
                or candidate.status not in {"PENDING_VERIFICATION", "LIMITED", "ACTIVE"}
                or candidate.created_at <= sanction.starts_at
            ):
                continue
            by_member[candidate_id] = AntifraudFinding(
                rule_code=AntifraudRuleCode.SANCTION_IDENTITY_CONTINUITY,
                subject_type=AntifraudSubjectType.MEMBER,
                subject_id=candidate_id,
                severity=AntifraudSeverity.CRITICAL,
                automation_action=AntifraudAction.HOLD,
                reason_key="antifraud.reasons.sanction_identity_continuity",
                observed_data={
                    "sanction_id": str(sanction.id),
                    "sanctioned_member_id": str(sanction.subject_member_id),
                    "related_member_id": str(candidate_id),
                    "related_member_created_at": candidate.created_at.isoformat(),
                    "sanction_started_at": sanction.starts_at.isoformat(),
                },
                threshold_data={
                    "related_link_status": RELATED_STATUS,
                    "account_created_after_sanction": True,
                },
            )
    return list(by_member.values())
