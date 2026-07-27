"""Explainable anomaly detection with independent human review."""

from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cooperative_clearing.modules.audit.infrastructure.repository import AuditRepository
from cooperative_clearing.modules.federation.infrastructure.discovery_models import (
    FederatedOffer,
    LogisticsQuote,
    PurchaseIntent,
)
from cooperative_clearing.modules.identity.domain.types import Principal, RoleCode
from cooperative_clearing.modules.identity.infrastructure.models import Member, Membership
from cooperative_clearing.modules.inventory.application.evidence import EvidenceService
from cooperative_clearing.modules.inventory.domain.types import decimal_text
from cooperative_clearing.modules.inventory.infrastructure.models import EvidenceBlob, EvidenceLink
from cooperative_clearing.modules.journal.application.service import SignedJournalService
from cooperative_clearing.modules.journal.domain.crypto import payload_hash
from cooperative_clearing.modules.risk.application.antifraud_rules_v2 import (
    collect_extended_findings,
)
from cooperative_clearing.modules.risk.application.common import (
    RiskCommandResult,
    begin_risk_command,
    complete_risk_command,
    risk_role_actor,
)
from cooperative_clearing.modules.risk.domain.antifraud import (
    decimal_median,
    outside_ratio_band,
)
from cooperative_clearing.modules.risk.domain.antifraud_catalog import (
    ALGORITHM_VERSION,
    CALIBRATION_DATASET_VERSION,
    rule_manifest_hash,
)
from cooperative_clearing.modules.risk.domain.types import (
    AntifraudAction,
    AntifraudFinding,
    AntifraudRuleCode,
    AntifraudSeverity,
    AntifraudSignalStatus,
    AntifraudSubjectType,
    CommitmentStatus,
    CommitmentType,
    risk_error,
)
from cooperative_clearing.modules.risk.infrastructure.models import (
    AntifraudScan,
    AntifraudSignal,
    ExposureCommitment,
    ShareAccount,
)
from cooperative_clearing.shared.core.config import Settings

SCAN_ROLES = {RoleCode.RISK_ADMIN}
REVIEW_ROLES = {RoleCode.AUDITOR}
ACTIVE_SIGNAL_STATUSES = {
    AntifraudSignalStatus.OPEN.value,
    AntifraudSignalStatus.IN_REVIEW.value,
    AntifraudSignalStatus.CONFIRMED.value,
}
CLOSED_INTENT_STATUSES = {"CANCELLED", "COMPENSATED", "EXPIRED"}


class AntifraudService:
    def __init__(self, settings: Settings) -> None:
        self.journal = SignedJournalService(settings)

    async def scan(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        cooperative_id: UUID,
        lookback_hours: int,
        idempotency_key: str,
        request_id: UUID | None,
        rule_codes: frozenset[AntifraudRuleCode] | None = None,
        subject_ids: frozenset[UUID] | None = None,
    ) -> RiskCommandResult:
        if not 1 <= lookback_hours <= 2160:
            raise risk_error("ANTIFRAUD_LOOKBACK_INVALID")
        actor = risk_role_actor(principal, cooperative_id, SCAN_ROLES)
        await self._eligible_member(session, cooperative_id, actor.person_id)
        cutoff = datetime.now(UTC)
        manifest_hash = rule_manifest_hash()
        request_payload: dict[str, object] = {
            "cooperative_id": str(cooperative_id),
            "lookback_hours": lookback_hours,
            "algorithm_version": ALGORITHM_VERSION,
            "rule_manifest_hash": manifest_hash,
            "calibration_dataset_version": CALIBRATION_DATASET_VERSION,
        }
        if rule_codes is not None:
            request_payload["rule_codes"] = sorted(item.value for item in rule_codes)
        if subject_ids is not None:
            request_payload["subject_ids"] = sorted(str(item) for item in subject_ids)
        record, replay = await begin_risk_command(
            session,
            principal,
            "RISK_ANTIFRAUD_SCAN",
            idempotency_key,
            request_payload,
        )
        if replay is not None:
            return replay

        findings = await self._collect_findings(
            session,
            cooperative_id=cooperative_id,
            cutoff=cutoff,
            lookback_hours=lookback_hours,
        )
        if rule_codes is not None:
            findings = [item for item in findings if item.rule_code in rule_codes]
        if subject_ids is not None:
            findings = [item for item in findings if item.subject_id in subject_ids]
        scan_id = uuid4()
        pending_signals: list[AntifraudSignal] = []
        new_count = 0
        repeated_count = 0
        for finding in findings:
            existing = (
                await session.execute(
                    select(AntifraudSignal)
                    .where(
                        AntifraudSignal.cooperative_id == cooperative_id,
                        AntifraudSignal.rule_code == finding.rule_code.value,
                        AntifraudSignal.subject_type == finding.subject_type.value,
                        AntifraudSignal.subject_id == finding.subject_id,
                        AntifraudSignal.status.in_(ACTIVE_SIGNAL_STATUSES),
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if existing is not None:
                next_version = existing.version + 1
                recurrence_event = await self.journal.append(
                    session,
                    event_type="risk.antifraud_signal_reobserved",
                    aggregate_type="antifraud_signal",
                    aggregate_id=existing.id,
                    aggregate_version=next_version,
                    actor=actor,
                    payload={
                        "signal_id": str(existing.id),
                        "scan_id": str(scan_id),
                        "rule_code": existing.rule_code,
                        "subject_type": existing.subject_type,
                        "subject_id": str(existing.subject_id),
                        "occurrence_count": existing.occurrence_count + 1,
                        "observed_at": cutoff.isoformat(),
                    },
                )
                existing.occurrence_count += 1
                existing.last_seen_at = cutoff
                existing.updated_at = cutoff
                existing.version = next_version
                repeated_count += 1
                await self._audit(
                    session,
                    principal,
                    cooperative_id,
                    "ANTIFRAUD_SIGNAL_REOBSERVED",
                    "AntifraudSignal",
                    existing.id,
                    recurrence_event.event_id,
                    request_id,
                )
                continue

            signal_id = uuid4()
            finding_payload = self._finding_payload(finding)
            detected_event = await self.journal.append(
                session,
                event_type="risk.antifraud_signal_detected",
                aggregate_type="antifraud_signal",
                aggregate_id=signal_id,
                aggregate_version=1,
                actor=actor,
                payload={
                    "signal_id": str(signal_id),
                    "scan_id": str(scan_id),
                    **finding_payload,
                    "notice": "signal_requires_independent_review",
                },
            )
            pending_signals.append(
                AntifraudSignal(
                    id=signal_id,
                    cooperative_id=cooperative_id,
                    scan_id=scan_id,
                    rule_code=finding.rule_code.value,
                    rule_version=finding.rule_version,
                    subject_type=finding.subject_type.value,
                    subject_id=finding.subject_id,
                    severity=finding.severity.value,
                    automation_action=finding.automation_action.value,
                    status=AntifraudSignalStatus.OPEN.value,
                    reason_key=finding.reason_key,
                    observed_data=finding.observed_data,
                    threshold_data=finding.threshold_data,
                    dedupe_key=payload_hash(
                        {
                            "cooperative_id": str(cooperative_id),
                            "rule_code": finding.rule_code.value,
                            "subject_type": finding.subject_type.value,
                            "subject_id": str(finding.subject_id),
                        }
                    ),
                    occurrence_count=1,
                    first_seen_at=cutoff,
                    last_seen_at=cutoff,
                    detected_by_user_id=principal.user_id,
                    detected_by_member_id=actor.person_id,
                    detected_role_assignment_id=actor.role_assignment_id,
                    detected_event_id=detected_event.event_id,
                    version=1,
                )
            )
            new_count += 1
            await self._audit(
                session,
                principal,
                cooperative_id,
                "ANTIFRAUD_SIGNAL_DETECTED",
                "AntifraudSignal",
                signal_id,
                detected_event.event_id,
                request_id,
            )

        rule_counts = Counter(finding.rule_code.value for finding in findings)
        summary: dict[str, object] = {
            "finding_count": len(findings),
            "new_signal_count": new_count,
            "reobserved_signal_count": repeated_count,
            "rule_counts": dict(sorted(rule_counts.items())),
            "automatic_decisions": 0,
        }
        completed_event = await self.journal.append(
            session,
            event_type="risk.antifraud_scan_completed",
            aggregate_type="antifraud_scan",
            aggregate_id=scan_id,
            aggregate_version=1,
            actor=actor,
            payload={
                **request_payload,
                "scan_id": str(scan_id),
                "input_cutoff": cutoff.isoformat(),
                **summary,
            },
        )
        session.add(
            AntifraudScan(
                id=scan_id,
                cooperative_id=cooperative_id,
                algorithm_version=ALGORITHM_VERSION,
                rule_manifest_hash=manifest_hash,
                calibration_dataset_version=CALIBRATION_DATASET_VERSION,
                lookback_hours=lookback_hours,
                input_cutoff=cutoff,
                finding_count=len(findings),
                result_summary=summary,
                initiated_by_user_id=principal.user_id,
                initiated_by_member_id=actor.person_id,
                initiated_role_assignment_id=actor.role_assignment_id,
                completed_event_id=completed_event.event_id,
            )
        )
        await session.flush()
        session.add_all(pending_signals)
        await self._audit(
            session,
            principal,
            cooperative_id,
            "ANTIFRAUD_SCAN_COMPLETED",
            "AntifraudScan",
            scan_id,
            completed_event.event_id,
            request_id,
        )
        return complete_risk_command(record, completed_event.event_id, scan_id)

    async def begin_review(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        signal_id: UUID,
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
        rule_codes: frozenset[AntifraudRuleCode] | None = None,
        subject_ids: frozenset[UUID] | None = None,
    ) -> RiskCommandResult:
        signal = await session.get(AntifraudSignal, signal_id, with_for_update=True)
        if signal is None:
            raise risk_error("ANTIFRAUD_SIGNAL_NOT_FOUND", 404)
        actor = risk_role_actor(principal, signal.cooperative_id, REVIEW_ROLES)
        await self._eligible_member(session, signal.cooperative_id, actor.person_id)
        payload = {"signal_id": str(signal.id), "expected_version": expected_version}
        record, replay = await begin_risk_command(
            session,
            principal,
            "RISK_ANTIFRAUD_REVIEW_BEGIN",
            idempotency_key,
            payload,
        )
        if replay is not None:
            return replay
        self._version(signal.version, expected_version)
        if signal.status != AntifraudSignalStatus.OPEN.value:
            raise risk_error("ANTIFRAUD_SIGNAL_NOT_OPEN", 409)
        if signal.detected_by_member_id == actor.person_id:
            raise risk_error("ANTIFRAUD_INDEPENDENT_REVIEW_REQUIRED", 409)

        event = await self.journal.append(
            session,
            event_type="risk.antifraud_review_started",
            aggregate_type="antifraud_signal",
            aggregate_id=signal.id,
            aggregate_version=signal.version + 1,
            actor=actor,
            payload={
                **payload,
                "rule_code": signal.rule_code,
                "subject_type": signal.subject_type,
                "subject_id": str(signal.subject_id),
            },
        )
        now = datetime.now(UTC)
        signal.status = AntifraudSignalStatus.IN_REVIEW.value
        signal.reviewer_user_id = principal.user_id
        signal.reviewer_member_id = actor.person_id
        signal.reviewer_role_assignment_id = actor.role_assignment_id
        signal.review_started_event_id = event.event_id
        signal.updated_at = now
        signal.version += 1
        await self._audit(
            session,
            principal,
            signal.cooperative_id,
            "ANTIFRAUD_REVIEW_STARTED",
            "AntifraudSignal",
            signal.id,
            event.event_id,
            request_id,
        )
        return complete_risk_command(record, event.event_id, signal.id)

    async def decide(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        signal_id: UUID,
        decision: AntifraudSignalStatus,
        rationale: str,
        evidence_ids: Sequence[UUID],
        expected_version: int,
        idempotency_key: str,
        request_id: UUID | None,
        rule_codes: frozenset[AntifraudRuleCode] | None = None,
        subject_ids: frozenset[UUID] | None = None,
    ) -> RiskCommandResult:
        signal = await session.get(AntifraudSignal, signal_id, with_for_update=True)
        if signal is None:
            raise risk_error("ANTIFRAUD_SIGNAL_NOT_FOUND", 404)
        actor = risk_role_actor(principal, signal.cooperative_id, REVIEW_ROLES)
        await self._eligible_member(session, signal.cooperative_id, actor.person_id)
        if decision not in {
            AntifraudSignalStatus.CLEARED,
            AntifraudSignalStatus.CONFIRMED,
        }:
            raise risk_error("ANTIFRAUD_DECISION_INVALID")
        normalized_rationale = " ".join(rationale.split())
        if len(normalized_rationale) < 2 or len(normalized_rationale) > 8000:
            raise risk_error("ANTIFRAUD_RATIONALE_INVALID")
        payload = {
            "signal_id": str(signal.id),
            "decision": decision.value,
            "rationale": normalized_rationale,
            "expected_version": expected_version,
            "evidence_ids": [str(item) for item in evidence_ids],
        }
        record, replay = await begin_risk_command(
            session,
            principal,
            "RISK_ANTIFRAUD_DECIDE",
            idempotency_key,
            payload,
        )
        if replay is not None:
            return replay
        self._version(signal.version, expected_version)
        if signal.status != AntifraudSignalStatus.IN_REVIEW.value:
            raise risk_error("ANTIFRAUD_SIGNAL_NOT_IN_REVIEW", 409)
        if (
            signal.reviewer_user_id != principal.user_id
            or signal.reviewer_member_id != actor.person_id
        ):
            raise risk_error("ANTIFRAUD_REVIEWER_MISMATCH", 403)
        evidence = await EvidenceService.require_ready(
            session,
            signal.cooperative_id,
            evidence_ids,
            required=True,
        )
        event = await self.journal.append(
            session,
            event_type="risk.antifraud_signal_decided",
            aggregate_type="antifraud_signal",
            aggregate_id=signal.id,
            aggregate_version=signal.version + 1,
            actor=actor,
            payload={
                **payload,
                "evidence": self._evidence_payload(evidence),
                "automation_released": decision is AntifraudSignalStatus.CLEARED,
            },
        )
        now = datetime.now(UTC)
        signal.status = decision.value
        signal.decision_event_id = event.event_id
        signal.decision_rationale = normalized_rationale
        signal.reviewed_at = now
        signal.updated_at = now
        signal.version += 1
        self._link_evidence(session, evidence, event.event_id, signal.id)
        await self._audit(
            session,
            principal,
            signal.cooperative_id,
            "ANTIFRAUD_SIGNAL_DECIDED",
            "AntifraudSignal",
            signal.id,
            event.event_id,
            request_id,
        )
        return complete_risk_command(record, event.event_id, signal.id)

    async def _collect_findings(
        self,
        session: AsyncSession,
        *,
        cooperative_id: UUID,
        cutoff: datetime,
        lookback_hours: int,
    ) -> list[AntifraudFinding]:
        since = cutoff - timedelta(hours=lookback_hours)
        findings = await self._offer_findings(session, cooperative_id, since, cutoff)
        findings.extend(await self._logistics_findings(session, cooperative_id, cutoff))
        findings.extend(await self._cancellation_findings(session, cooperative_id, since, cutoff))
        findings.extend(await self._guarantee_findings(session, cooperative_id, cutoff))
        findings.extend(await self._concentration_findings(session, cooperative_id, cutoff))
        findings.extend(
            await collect_extended_findings(
                session,
                cooperative_id=cooperative_id,
                since=since,
                cutoff=cutoff,
            )
        )
        return sorted(
            findings,
            key=lambda item: (
                item.rule_code.value,
                item.subject_type.value,
                str(item.subject_id),
            ),
        )

    @staticmethod
    async def _offer_findings(
        session: AsyncSession,
        cooperative_id: UUID,
        since: datetime,
        cutoff: datetime,
    ) -> list[AntifraudFinding]:
        rows = list(
            (
                await session.execute(
                    select(FederatedOffer).where(
                        FederatedOffer.cooperative_id == cooperative_id,
                        FederatedOffer.signed_at <= cutoff,
                    )
                )
            ).scalars()
        )
        latest: dict[UUID, FederatedOffer] = {}
        versions: dict[UUID, list[FederatedOffer]] = defaultdict(list)
        for row in rows:
            versions[row.offer_id].append(row)
            current = latest.get(row.offer_id)
            if current is None or row.offer_version > current.offer_version:
                latest[row.offer_id] = row

        findings: list[AntifraudFinding] = []
        groups: dict[tuple[str, str, str], list[FederatedOffer]] = defaultdict(list)
        for row in latest.values():
            if row.status == "ACTIVE":
                groups[(row.product_code, row.unit_code, row.valuation_unit)].append(row)
        for group, offers in groups.items():
            if len(offers) < 3:
                continue
            prices = [offer.unit_price + offer.mandatory_fee_per_unit for offer in offers]
            median = decimal_median(prices)
            for offer, price in zip(offers, prices, strict=True):
                if outside_ratio_band(
                    price,
                    median,
                    lower_ratio=Decimal("0.5"),
                    upper_ratio=Decimal("2"),
                ):
                    findings.append(
                        AntifraudFinding(
                            rule_code=AntifraudRuleCode.OFFER_PRICE_OUTLIER,
                            subject_type=AntifraudSubjectType.OFFER,
                            subject_id=offer.offer_id,
                            severity=AntifraudSeverity.HIGH,
                            automation_action=AntifraudAction.HOLD,
                            reason_key="antifraud.reasons.offer_price_outlier",
                            observed_data={
                                "unit_total": decimal_text(price),
                                "sample_size": len(offers),
                                "product_code": group[0],
                                "unit_code": group[1],
                                "valuation_unit": group[2],
                            },
                            threshold_data={
                                "median": decimal_text(median),
                                "lower_ratio": "0.5",
                                "upper_ratio": "2",
                            },
                        )
                    )
        for offer_id, offer_versions in versions.items():
            recent_count = sum(1 for row in offer_versions if since <= row.signed_at <= cutoff)
            if recent_count >= 4:
                findings.append(
                    AntifraudFinding(
                        rule_code=AntifraudRuleCode.OFFER_REPUBLICATION_BURST,
                        subject_type=AntifraudSubjectType.OFFER,
                        subject_id=offer_id,
                        severity=AntifraudSeverity.MEDIUM,
                        automation_action=AntifraudAction.WARN,
                        reason_key="antifraud.reasons.offer_republication_burst",
                        observed_data={"version_count": recent_count},
                        threshold_data={"minimum_version_count": 4},
                    )
                )
        return findings

    @staticmethod
    async def _logistics_findings(
        session: AsyncSession,
        cooperative_id: UUID,
        cutoff: datetime,
    ) -> list[AntifraudFinding]:
        rows = list(
            (
                await session.execute(
                    select(LogisticsQuote).where(
                        LogisticsQuote.cooperative_id == cooperative_id,
                        LogisticsQuote.signed_at <= cutoff,
                    )
                )
            ).scalars()
        )
        latest: dict[UUID, LogisticsQuote] = {}
        for row in rows:
            current = latest.get(row.quote_id)
            if current is None or row.quote_version > current.quote_version:
                latest[row.quote_id] = row
        groups: dict[tuple[str, str, str, str], list[tuple[LogisticsQuote, Decimal]]] = defaultdict(
            list
        )
        for quote in latest.values():
            if quote.status != "ACTIVE":
                continue
            try:
                total = sum(
                    (Decimal(str(value)) for value in quote.cost_components.values()),
                    Decimal(0),
                )
                unit_cost = total / quote.capacity
            except (InvalidOperation, ZeroDivisionError):
                continue
            groups[
                (
                    quote.origin_region,
                    quote.destination_region,
                    quote.unit_code,
                    quote.valuation_unit,
                )
            ].append((quote, unit_cost))
        findings: list[AntifraudFinding] = []
        for group, quotes in groups.items():
            if len(quotes) < 3:
                continue
            median = decimal_median([unit_cost for _, unit_cost in quotes])
            for quote, unit_cost in quotes:
                if outside_ratio_band(
                    unit_cost,
                    median,
                    lower_ratio=None,
                    upper_ratio=Decimal("2"),
                ):
                    findings.append(
                        AntifraudFinding(
                            rule_code=AntifraudRuleCode.LOGISTICS_PRICE_OUTLIER,
                            subject_type=AntifraudSubjectType.LOGISTICS_QUOTE,
                            subject_id=quote.quote_id,
                            severity=AntifraudSeverity.HIGH,
                            automation_action=AntifraudAction.HOLD,
                            reason_key="antifraud.reasons.logistics_price_outlier",
                            observed_data={
                                "unit_cost": decimal_text(unit_cost),
                                "sample_size": len(quotes),
                                "origin_region": group[0],
                                "destination_region": group[1],
                                "unit_code": group[2],
                                "valuation_unit": group[3],
                            },
                            threshold_data={
                                "median": decimal_text(median),
                                "upper_ratio": "2",
                            },
                        )
                    )
        return findings

    @staticmethod
    async def _cancellation_findings(
        session: AsyncSession,
        cooperative_id: UUID,
        since: datetime,
        cutoff: datetime,
    ) -> list[AntifraudFinding]:
        rows = list(
            (
                await session.execute(
                    select(PurchaseIntent).where(
                        PurchaseIntent.cooperative_id == cooperative_id,
                        PurchaseIntent.created_at >= since,
                        PurchaseIntent.created_at <= cutoff,
                        PurchaseIntent.status.in_(CLOSED_INTENT_STATUSES),
                    )
                )
            ).scalars()
        )
        counts = Counter(row.buyer_member_id for row in rows)
        return [
            AntifraudFinding(
                rule_code=AntifraudRuleCode.PURCHASE_CANCELLATION_BURST,
                subject_type=AntifraudSubjectType.MEMBER,
                subject_id=member_id,
                severity=AntifraudSeverity.HIGH,
                automation_action=AntifraudAction.HOLD,
                reason_key="antifraud.reasons.purchase_cancellation_burst",
                observed_data={"closed_intent_count": count},
                threshold_data={"minimum_closed_intent_count": 3},
            )
            for member_id, count in counts.items()
            if count >= 3
        ]

    @staticmethod
    async def _guarantee_findings(
        session: AsyncSession,
        cooperative_id: UUID,
        cutoff: datetime,
    ) -> list[AntifraudFinding]:
        commitments = list(
            (
                await session.execute(
                    select(ExposureCommitment).where(
                        ExposureCommitment.cooperative_id == cooperative_id,
                        ExposureCommitment.commitment_type == CommitmentType.GUARANTEE.value,
                        ExposureCommitment.status.in_(
                            {
                                CommitmentStatus.PROPOSED.value,
                                CommitmentStatus.ACTIVE.value,
                            }
                        ),
                        ExposureCommitment.expires_at > cutoff,
                    )
                )
            ).scalars()
        )
        directed: dict[tuple[UUID, UUID], list[UUID]] = defaultdict(list)
        for item in commitments:
            if item.debtor_member_id is not None:
                directed[(item.owner_member_id, item.debtor_member_id)].append(item.id)
        findings: list[AntifraudFinding] = []
        seen: set[tuple[UUID, UUID]] = set()
        for (owner, debtor), commitment_ids in directed.items():
            pair = (owner, debtor) if str(owner) < str(debtor) else (debtor, owner)
            if pair in seen or (debtor, owner) not in directed:
                continue
            seen.add(pair)
            all_ids = commitment_ids + directed[(debtor, owner)]
            for member_id, counterpart_id in ((owner, debtor), (debtor, owner)):
                findings.append(
                    AntifraudFinding(
                        rule_code=AntifraudRuleCode.CIRCULAR_GUARANTEE,
                        subject_type=AntifraudSubjectType.MEMBER,
                        subject_id=member_id,
                        severity=AntifraudSeverity.CRITICAL,
                        automation_action=AntifraudAction.HOLD,
                        reason_key="antifraud.reasons.circular_guarantee",
                        observed_data={
                            "counterpart_member_id": str(counterpart_id),
                            "commitment_ids": sorted(str(item) for item in all_ids),
                        },
                        threshold_data={"circular_path_length": 2},
                    )
                )
        return findings

    @staticmethod
    async def _concentration_findings(
        session: AsyncSession,
        cooperative_id: UUID,
        cutoff: datetime,
    ) -> list[AntifraudFinding]:
        accounts = {
            account.id: account
            for account in (
                await session.execute(
                    select(ShareAccount).where(
                        ShareAccount.cooperative_id == cooperative_id,
                        ShareAccount.status == "ACTIVE",
                    )
                )
            ).scalars()
        }
        commitments = list(
            (
                await session.execute(
                    select(ExposureCommitment).where(
                        ExposureCommitment.cooperative_id == cooperative_id,
                        ExposureCommitment.status.in_(
                            {
                                CommitmentStatus.PROPOSED.value,
                                CommitmentStatus.ACTIVE.value,
                            }
                        ),
                        ExposureCommitment.expires_at > cutoff,
                    )
                )
            ).scalars()
        )
        by_account: dict[UUID, list[ExposureCommitment]] = defaultdict(list)
        for item in commitments:
            by_account[item.account_id].append(item)
        findings: list[AntifraudFinding] = []
        for account_id, items in by_account.items():
            account = accounts.get(account_id)
            if account is None or len(items) < 3:
                continue
            usable_balance = (
                account.balance - account.protected_amount - account.executed_not_settled
            )
            aggregate_loss = sum((item.max_loss for item in items), Decimal(0))
            if usable_balance > 0 and aggregate_loss > usable_balance * Decimal("0.8"):
                findings.append(
                    AntifraudFinding(
                        rule_code=AntifraudRuleCode.COLLATERAL_CONCENTRATION,
                        subject_type=AntifraudSubjectType.SHARE_ACCOUNT,
                        subject_id=account_id,
                        severity=AntifraudSeverity.CRITICAL,
                        automation_action=AntifraudAction.HOLD,
                        reason_key="antifraud.reasons.collateral_concentration",
                        observed_data={
                            "commitment_count": len(items),
                            "aggregate_max_loss": decimal_text(aggregate_loss),
                            "usable_balance": decimal_text(usable_balance),
                        },
                        threshold_data={
                            "minimum_commitment_count": 3,
                            "maximum_ratio": "0.8",
                        },
                    )
                )
        return findings

    @staticmethod
    def _finding_payload(finding: AntifraudFinding) -> dict[str, object]:
        return {
            "algorithm_version": ALGORITHM_VERSION,
            "rule_code": finding.rule_code.value,
            "rule_version": finding.rule_version,
            "subject_type": finding.subject_type.value,
            "subject_id": str(finding.subject_id),
            "severity": finding.severity.value,
            "automation_action": finding.automation_action.value,
            "reason_key": finding.reason_key,
            "observed_data": finding.observed_data,
            "threshold_data": finding.threshold_data,
        }

    @staticmethod
    async def _eligible_member(
        session: AsyncSession,
        cooperative_id: UUID,
        member_id: UUID,
    ) -> None:
        member = await session.get(Member, member_id)
        membership = (
            await session.execute(
                select(Membership.id).where(
                    Membership.cooperative_id == cooperative_id,
                    Membership.member_id == member_id,
                    Membership.status == "ACTIVE",
                )
            )
        ).scalar_one_or_none()
        if member is None or member.status != "ACTIVE" or membership is None:
            raise risk_error("MEMBER_NOT_ELIGIBLE", 409)

    @staticmethod
    def _version(current: int, expected: int) -> None:
        if current != expected:
            raise risk_error("VERSION_CONFLICT", 409)

    @staticmethod
    def _evidence_payload(items: Sequence[EvidenceBlob]) -> list[dict[str, object]]:
        return [
            {
                "evidence_id": str(item.id),
                "sha256": item.expected_sha256,
                "size": item.expected_size,
                "kind": item.kind,
            }
            for item in items
        ]

    @staticmethod
    def _link_evidence(
        session: AsyncSession,
        evidence: Sequence[EvidenceBlob],
        event_id: UUID,
        signal_id: UUID,
    ) -> None:
        session.add_all(
            [
                EvidenceLink(
                    id=uuid4(),
                    evidence_id=item.id,
                    event_id=event_id,
                    subject_type="AntifraudSignal",
                    subject_id=signal_id,
                )
                for item in evidence
            ]
        )

    @staticmethod
    async def _audit(
        session: AsyncSession,
        principal: Principal,
        cooperative_id: UUID,
        action: str,
        object_type: str,
        object_id: UUID,
        event_id: UUID,
        request_id: UUID | None,
    ) -> None:
        await AuditRepository(session).record(
            action=action,
            object_type=object_type,
            object_id=object_id,
            cooperative_id=cooperative_id,
            actor_user_id=principal.user_id,
            outcome="SUCCESS",
            request_id=request_id,
            payload={"signed_event_id": str(event_id)},
        )
