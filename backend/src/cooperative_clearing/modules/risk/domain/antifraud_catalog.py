"""Versioned anti-fraud rule manifest and synthetic calibration baseline."""

import json
from dataclasses import dataclass
from hashlib import sha256

from cooperative_clearing.modules.risk.domain.types import (
    AntifraudAction,
    AntifraudRuleCode,
    AntifraudSeverity,
)

ALGORITHM_VERSION = "2.0.0"
CALIBRATION_DATASET_VERSION = "synthetic-v2.0.0"


@dataclass(frozen=True, slots=True)
class RuleDescriptor:
    code: AntifraudRuleCode
    requirement_key: str
    severity: AntifraudSeverity
    action: AntifraudAction
    data_sources: tuple[str, ...]
    rule_version: int = 1


@dataclass(frozen=True, slots=True)
class CalibrationCase:
    case_id: str
    rule_code: AntifraudRuleCode
    expected_signal: bool


RULE_DESCRIPTORS = (
    RuleDescriptor(
        AntifraudRuleCode.PURCHASE_CANCELLATION_BURST,
        "antifraud.requirements.synthetic_demand",
        AntifraudSeverity.HIGH,
        AntifraudAction.HOLD,
        ("federation.purchase_intents",),
    ),
    RuleDescriptor(
        AntifraudRuleCode.RELATED_ACCOUNT_RATING_RING,
        "antifraud.requirements.related_accounts",
        AntifraudSeverity.HIGH,
        AntifraudAction.HOLD,
        ("risk.related_party_links", "trust.reputation_events"),
    ),
    RuleDescriptor(
        AntifraudRuleCode.OFFER_PRICE_OUTLIER,
        "antifraud.requirements.supply_suppression",
        AntifraudSeverity.HIGH,
        AntifraudAction.HOLD,
        ("federation.federated_offers",),
    ),
    RuleDescriptor(
        AntifraudRuleCode.LIMIT_SPLITTING_BURST,
        "antifraud.requirements.limit_splitting",
        AntifraudSeverity.MEDIUM,
        AntifraudAction.WARN,
        ("risk.exposure_commitments", "risk.risk_policies"),
    ),
    RuleDescriptor(
        AntifraudRuleCode.RISK_COEFFICIENT_CHANGE_SPIKE,
        "antifraud.requirements.coefficient_change",
        AntifraudSeverity.MEDIUM,
        AntifraudAction.WARN,
        ("risk.risk_policies",),
    ),
    RuleDescriptor(
        AntifraudRuleCode.LOGISTICS_PRICE_OUTLIER,
        "antifraud.requirements.logistics_estimate",
        AntifraudSeverity.HIGH,
        AntifraudAction.HOLD,
        ("federation.logistics_quotes",),
    ),
    RuleDescriptor(
        AntifraudRuleCode.RELATED_CRITICAL_RESOURCE_CONCENTRATION,
        "antifraud.requirements.critical_resource_concentration",
        AntifraudSeverity.CRITICAL,
        AntifraudAction.HOLD,
        (
            "solidarity.reserve_targets",
            "assets.products",
            "assets.inventory_lots",
            "risk.related_party_links",
        ),
    ),
    RuleDescriptor(
        AntifraudRuleCode.CIRCULAR_GUARANTEE,
        "antifraud.requirements.guarantee_reuse",
        AntifraudSeverity.CRITICAL,
        AntifraudAction.HOLD,
        ("risk.exposure_commitments",),
    ),
    RuleDescriptor(
        AntifraudRuleCode.COLLATERAL_CONCENTRATION,
        "antifraud.requirements.guarantee_reuse",
        AntifraudSeverity.CRITICAL,
        AntifraudAction.HOLD,
        ("risk.share_accounts", "risk.exposure_commitments"),
    ),
    RuleDescriptor(
        AntifraudRuleCode.REPUTATION_SYNCHRONIZATION,
        "antifraud.requirements.reputation_synchronization",
        AntifraudSeverity.HIGH,
        AntifraudAction.WARN,
        ("trust.reputation_events",),
    ),
    RuleDescriptor(
        AntifraudRuleCode.CONTRIBUTION_REPUTATION_INFLUENCE,
        "antifraud.requirements.donation_influence",
        AntifraudSeverity.HIGH,
        AntifraudAction.WARN,
        ("solidarity.contributions", "trust.reputation_events"),
    ),
    RuleDescriptor(
        AntifraudRuleCode.PRIVILEGED_DECISION_RELATED_PARTY,
        "antifraud.requirements.privileged_conflict",
        AntifraudSeverity.CRITICAL,
        AntifraudAction.HOLD,
        (
            "risk.related_party_links",
            "solidarity.allocations",
            "solidarity.allocation_approvals",
            "trust.arbitration_decisions",
        ),
    ),
    RuleDescriptor(
        AntifraudRuleCode.AID_CAMPAIGN_SPLITTING,
        "antifraud.requirements.aid_campaign_splitting",
        AntifraudSeverity.HIGH,
        AntifraudAction.HOLD,
        ("solidarity.campaigns",),
    ),
    RuleDescriptor(
        AntifraudRuleCode.SANCTION_IDENTITY_CONTINUITY,
        "antifraud.requirements.sanction_evasion",
        AntifraudSeverity.CRITICAL,
        AntifraudAction.HOLD,
        ("identity.members", "risk.related_party_links", "trust.sanctions"),
    ),
    RuleDescriptor(
        AntifraudRuleCode.OFFER_REPUBLICATION_BURST,
        "antifraud.requirements.supply_suppression",
        AntifraudSeverity.MEDIUM,
        AntifraudAction.WARN,
        ("federation.federated_offers",),
    ),
)

RULE_DESCRIPTOR_BY_CODE = {item.code: item for item in RULE_DESCRIPTORS}

# Regression scenarios are an engineering baseline, not pilot calibration.
CALIBRATION_CASES = tuple(
    CalibrationCase(
        case_id=f"{descriptor.code.value.lower()}:{kind}",
        rule_code=descriptor.code,
        expected_signal=kind == "positive",
    )
    for descriptor in RULE_DESCRIPTORS
    for kind in ("positive", "negative")
)


def rule_manifest_payload() -> list[dict[str, object]]:
    return [
        {
            "code": item.code.value,
            "rule_version": item.rule_version,
            "requirement_key": item.requirement_key,
            "severity": item.severity.value,
            "action": item.action.value,
            "data_sources": list(item.data_sources),
            "calibration_dataset_version": CALIBRATION_DATASET_VERSION,
            "engineering_case_count": sum(
                1 for case in CALIBRATION_CASES if case.rule_code is item.code
            ),
            "pilot_false_positive_rate": None,
            "production_approved": False,
        }
        for item in sorted(RULE_DESCRIPTORS, key=lambda descriptor: descriptor.code.value)
    ]


def rule_manifest_hash() -> str:
    encoded = json.dumps(
        rule_manifest_payload(),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"
