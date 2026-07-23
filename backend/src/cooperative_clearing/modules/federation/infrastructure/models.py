"""Persistence for external-node trust, offline packages, and liability evidence."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from cooperative_clearing.shared.infrastructure.orm import Base


class NodeOwnerOrganization(Base):
    __tablename__ = "node_owner_organizations"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE','SUSPENDED','ARCHIVED')", name="status_allowed"),
        CheckConstraint("version >= 1", name="version_positive"),
        UniqueConstraint("jurisdiction", "registration_code", name="uq_node_owner_registration"),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    legal_name: Mapped[str] = mapped_column(String(240), nullable=False)
    registration_code: Mapped[str] = mapped_column(String(120), nullable=False)
    jurisdiction: Mapped[str] = mapped_column(String(120), nullable=False)
    contact_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class ExternalNode(Base):
    __tablename__ = "external_nodes"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT','APPLICATION_SUBMITTED','IDENTITY_VERIFIED',"
            "'TECHNICAL_CHALLENGE','AUDIT_PENDING','LIMITED','ACTIVE','SUSPENDED',"
            "'QUARANTINED','REVOKED','ARCHIVED','REJECTED')",
            name="status_allowed",
        ),
        CheckConstraint(
            "trust_level IN ('UNTRUSTED','LIMITED','STANDARD','HIGH')",
            name="trust_level_allowed",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_external_nodes_owner_status", "owner_organization_id", "status"),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    node_code: Mapped[str] = mapped_column(String(63), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    owner_organization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.node_owner_organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    sponsor_local_node_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("node.node_profiles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    territory: Mapped[str] = mapped_column(String(160), nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    trust_level: Mapped[str] = mapped_column(String(16), nullable=False)
    network_endpoints: Mapped[list[object]] = mapped_column(JSONB, nullable=False)
    hardware_manifest: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    release_manifest: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    supported_protocols: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    supported_policies: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False)
    data_scopes: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_checkpoint_hash: Mapped[str | None] = mapped_column(String(71))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class NodeApplication(Base):
    __tablename__ = "node_applications"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT','APPLICATION_SUBMITTED','IDENTITY_VERIFIED',"
            "'TECHNICAL_CHALLENGE','AUDIT_PENDING','LIMITED','ACTIVE','REJECTED')",
            name="status_allowed",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_node_applications_status", "status", "created_at"),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    node_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.external_nodes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_capabilities: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    requested_limits: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    requested_data_scopes: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    recovery_contacts: Mapped[list[object]] = mapped_column(JSONB, nullable=False)
    security_questionnaire: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    evidence_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    proposed_trust_expiry: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT"), nullable=False
    )
    created_by_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.members.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_role_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.role_assignments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    identity_verified_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    identity_verified_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    audit_decided_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    audit_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    identity_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    audit_decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class NodeResponsibleParty(Base):
    __tablename__ = "node_responsible_parties"
    __table_args__ = (
        CheckConstraint(
            "role_code IN ('OWNER_SIGNATORY','TECHNICAL_CUSTODIAN','SECURITY_ADMINISTRATOR',"
            "'BUSINESS_OPERATOR','NODE_AUDITOR','SPONSOR_APPROVER')",
            name="role_allowed",
        ),
        CheckConstraint(
            "status IN ('PROPOSED','ACTIVE','RELEASED','REVOKED')", name="status_allowed"
        ),
        CheckConstraint("max_exposure >= 0", name="exposure_nonnegative"),
        CheckConstraint("valid_until IS NULL OR valid_until > valid_from", name="period_valid"),
        Index(
            "uq_node_responsible_active_role_member",
            "node_id",
            "role_code",
            "member_id",
            unique=True,
            postgresql_where=text("status IN ('PROPOSED','ACTIVE')"),
        ),
        Index("ix_node_responsible_node_status", "node_id", "status"),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    node_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.external_nodes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    application_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.node_applications.id", ondelete="RESTRICT"),
        nullable=False,
    )
    member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.members.id", ondelete="RESTRICT"),
        nullable=False,
    )
    role_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.role_assignments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    role_code: Mapped[str] = mapped_column(String(40), nullable=False)
    capability_scope: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    responsibility_scope: Mapped[str] = mapped_column(Text, nullable=False)
    max_exposure: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    exposure_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    accepted_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    accepted_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NodeCertificate(Base):
    __tablename__ = "node_certificates"
    __table_args__ = (
        CheckConstraint("algorithm = 'Ed25519'", name="algorithm_allowed"),
        CheckConstraint(
            "status IN ('PENDING','ACTIVE','ROTATING','RETIRED',"
            "'SUSPENDED','REVOKED','COMPROMISED')",
            name="status_allowed",
        ),
        CheckConstraint("valid_until > valid_from", name="period_valid"),
        Index(
            "uq_node_certificate_active",
            "node_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        Index("ix_node_certificates_node_status", "node_id", "status"),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    node_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.external_nodes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    algorithm: Mapped[str] = mapped_column(String(20), nullable=False)
    public_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(71), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    activated_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    revoked_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NodeChallenge(Base):
    __tablename__ = "node_challenges"
    __table_args__ = (
        CheckConstraint("status IN ('ISSUED','PASSED','FAILED','EXPIRED')", name="status_allowed"),
        CheckConstraint("expires_at > issued_at", name="period_valid"),
        CheckConstraint("nonce_hash ~ '^sha256:[0-9a-f]{64}$'", name="nonce_hash_sha256"),
        Index("ix_node_challenges_application", "application_id", "status"),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    application_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.node_applications.id", ondelete="RESTRICT"),
        nullable=False,
    )
    certificate_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.node_certificates.id", ondelete="RESTRICT"),
        nullable=False,
    )
    nonce_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    challenge_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    protocol_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    issued_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT"), nullable=False
    )
    issued_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    response_signature: Mapped[bytes | None] = mapped_column(LargeBinary)
    response_payload: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    response_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NodeTrustContract(Base):
    __tablename__ = "node_trust_contracts"
    __table_args__ = (
        CheckConstraint("status IN ('DRAFT','ACTIVE','EXPIRED','REVOKED')", name="status_allowed"),
        CheckConstraint("trust_level IN ('LIMITED','STANDARD','HIGH')", name="trust_level_allowed"),
        CheckConstraint("valid_until > valid_from", name="period_valid"),
        CheckConstraint("max_offline_hours > 0", name="offline_positive"),
        CheckConstraint("terms_hash ~ '^sha256:[0-9a-f]{64}$'", name="terms_hash_sha256"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "uq_node_trust_contract_active",
            "node_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    node_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.external_nodes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    application_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.node_applications.id", ondelete="RESTRICT"),
        nullable=False,
    )
    contract_number: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    trust_level: Mapped[str] = mapped_column(String(16), nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    event_types: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    inbound_scope: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    outbound_scope: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    federation_limits: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    allowed_counterparties: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    max_offline_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    required_protocols: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    required_policies: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False)
    service_levels: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    liability_terms: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    terms_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    proposed_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT"), nullable=False
    )
    proposed_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    approved_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    approved_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class NodeBilateralLimit(Base):
    __tablename__ = "node_bilateral_limits"
    __table_args__ = (
        CheckConstraint("status IN ('DRAFT','ACTIVE','RETIRED')", name="status_allowed"),
        CheckConstraint(
            "max_package_value >= 0 AND max_unsettled_obligations >= 0 "
            "AND max_external_rights >= 0 AND max_clearing_position >= 0",
            name="amounts_nonnegative",
        ),
        CheckConstraint(
            "max_offline_hours > 0 AND required_confirmations > 0", name="limits_positive"
        ),
        CheckConstraint("terms_hash ~ '^sha256:[0-9a-f]{64}$'", name="terms_hash_sha256"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "uq_bilateral_limit_active_capability",
            "node_id",
            "capability",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    node_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.external_nodes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    capability: Mapped[str] = mapped_column(String(40), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    max_package_value: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    max_unsettled_obligations: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    max_external_rights: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    max_clearing_position: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    max_offline_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    allowed_critical_resources: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    required_confirmations: Mapped[int] = mapped_column(Integer, nullable=False)
    terms_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    proposed_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT"), nullable=False
    )
    proposed_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    approved_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    approved_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class NodeBond(Base):
    __tablename__ = "node_bonds"
    __table_args__ = (
        CheckConstraint(
            "amount > 0 AND protected_amount >= 0 AND protected_amount < amount",
            name="amount_valid",
        ),
        CheckConstraint(
            "maximum_loss > 0 AND maximum_loss <= amount - protected_amount", name="loss_bounded"
        ),
        CheckConstraint(
            "status IN ('PLEDGED','ACTIVE','FROZEN','RELEASED','EXHAUSTED')", name="status_allowed"
        ),
        CheckConstraint("valid_until > valid_from", name="period_valid"),
        Index("ix_node_bonds_node_status", "node_id", "status"),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    node_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.external_nodes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    provider_organization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.node_owner_organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    reference: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    protected_amount: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    maximum_loss: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    capability_scope: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    evidence_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class NodeExposure(Base):
    __tablename__ = "node_exposures"
    __table_args__ = (
        CheckConstraint("current_amount >= 0 AND reserved_amount >= 0", name="amounts_nonnegative"),
        CheckConstraint("version >= 1", name="version_positive"),
        UniqueConstraint("node_id", "capability", "unit", name="uq_node_exposure_capability_unit"),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    node_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.external_nodes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    capability: Mapped[str] = mapped_column(String(40), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    current_amount: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    reserved_amount: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    updated_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class OfflineEpoch(Base):
    __tablename__ = "offline_epochs"
    __table_args__ = (
        CheckConstraint(
            "(local_node_id IS NOT NULL AND external_node_id IS NULL) OR "
            "(local_node_id IS NULL AND external_node_id IS NOT NULL)",
            name="one_node_subject",
        ),
        CheckConstraint(
            "status IN ('DRAFT','OPEN','CLOSED','EXPIRED','REVOKED')", name="status_allowed"
        ),
        CheckConstraint("expires_at IS NULL OR expires_at > starts_at", name="period_valid"),
        CheckConstraint("policy_hash ~ '^sha256:[0-9a-f]{64}$'", name="policy_hash_sha256"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_offline_epochs_external_status", "external_node_id", "status"),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    local_node_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("node.node_profiles.id", ondelete="RESTRICT")
    )
    external_node_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("federation.external_nodes.id", ondelete="RESTRICT")
    )
    base_checkpoint_hash: Mapped[str | None] = mapped_column(String(71))
    allowed_event_types: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    limits: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    protocol_version: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_versions: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False)
    emergency_contacts: Mapped[list[object]] = mapped_column(JSONB, nullable=False)
    closure_rules: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opened_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT"), nullable=False
    )
    opened_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    closed_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class SyncPackage(Base):
    __tablename__ = "sync_packages"
    __table_args__ = (
        CheckConstraint("direction IN ('INBOUND','OUTBOUND')", name="direction_allowed"),
        CheckConstraint(
            "status IN ('EXPORTED','QUARANTINED','VERIFIED','SIMULATED',"
            "'CONFLICT','APPLIED','REJECTED')",
            name="status_allowed",
        ),
        CheckConstraint(
            "sequence_first > 0 AND sequence_last >= sequence_first", name="sequence_valid"
        ),
        CheckConstraint(
            "event_count > 0 AND blob_count >= 0 AND archive_size > 0", name="counts_positive"
        ),
        CheckConstraint("archive_hash ~ '^sha256:[0-9a-f]{64}$'", name="archive_hash_sha256"),
        CheckConstraint("manifest_hash ~ '^sha256:[0-9a-f]{64}$'", name="manifest_hash_sha256"),
        CheckConstraint("expires_at > created_at", name="period_valid"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_sync_packages_peer_status", "peer_node_id", "status", "created_at"),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    peer_node_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.external_nodes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    epoch_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("federation.offline_epochs.id", ondelete="RESTRICT")
    )
    direction: Mapped[str] = mapped_column(String(12), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    source_node_code: Mapped[str] = mapped_column(String(63), nullable=False)
    target_node_code: Mapped[str] = mapped_column(String(63), nullable=False)
    protocol_version: Mapped[str] = mapped_column(String(32), nullable=False)
    required_capabilities: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    sequence_first: Mapped[int] = mapped_column(Integer, nullable=False)
    sequence_last: Mapped[int] = mapped_column(Integer, nullable=False)
    base_checkpoint_hash: Mapped[str | None] = mapped_column(String(71))
    event_count: Mapped[int] = mapped_column(Integer, nullable=False)
    blob_count: Mapped[int] = mapped_column(Integer, nullable=False)
    archive_size: Mapped[int] = mapped_column(Integer, nullable=False)
    archive_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    manifest_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    archive_path: Mapped[str] = mapped_column(String(500), nullable=False)
    simulation_summary: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    rejection_code: Mapped[str | None] = mapped_column(String(100))
    created_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT"), nullable=False
    )
    created_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    applied_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    applied_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    simulated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class InboxEvent(Base):
    __tablename__ = "inbox_events"
    __table_args__ = (
        CheckConstraint("local_sequence > 0 AND aggregate_version > 0", name="versions_positive"),
        CheckConstraint(
            "status IN ('HELD','READY','APPLIED','IGNORED','REJECTED','CONFLICT')",
            name="status_allowed",
        ),
        CheckConstraint("event_hash ~ '^sha256:[0-9a-f]{64}$'", name="event_hash_sha256"),
        UniqueConstraint("source_node_id", "event_id", name="uq_inbox_source_event"),
        UniqueConstraint("source_node_id", "local_sequence", name="uq_inbox_source_sequence"),
        Index("ix_inbox_events_package_status", "package_id", "status"),
        Index("ix_inbox_events_aggregate", "aggregate_type", "aggregate_id", "aggregate_version"),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    package_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.sync_packages.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_node_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.external_nodes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    event_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(160), nullable=False)
    local_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_event_hash: Mapped[str | None] = mapped_column(String(71))
    event_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    envelope_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    signature: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    effect_summary: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SyncConflict(Base):
    __tablename__ = "sync_conflicts"
    __table_args__ = (
        CheckConstraint(
            "conflict_class IN ('DUPLICATE','TAMPERED_DUPLICATE','REFERENTIAL_GAP',"
            "'CONCURRENT_METADATA','COMPETING_RESERVATION','DOUBLE_REDEMPTION',"
            "'ROLE_KEY_INVALID','POLICY_MISMATCH','CUSTODY_CONFLICT','REPUTATION_DIVERGENCE')",
            name="class_allowed",
        ),
        CheckConstraint(
            "status IN ('OPEN','UNDER_REVIEW','RESOLVED','APPEALED')", name="status_allowed"
        ),
        CheckConstraint("maximum_exposure >= 0", name="exposure_nonnegative"),
        CheckConstraint(
            "decision IS NULL OR decision IN ('ACCEPT_REMOTE','KEEP_LOCAL',"
            "'COMPENSATE','REJECT_PACKAGE')",
            name="decision_allowed",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_sync_conflicts_status", "status", "created_at"),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    package_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.sync_packages.id", ondelete="RESTRICT"),
        nullable=False,
    )
    inbox_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("federation.inbox_events.id", ondelete="RESTRICT")
    )
    conflict_class: Mapped[str] = mapped_column(String(40), nullable=False)
    affected_object_type: Mapped[str] = mapped_column(String(100), nullable=False)
    affected_object_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    local_event_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    remote_event_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    local_event_hash: Mapped[str | None] = mapped_column(String(71))
    remote_event_hash: Mapped[str | None] = mapped_column(String(71))
    maximum_exposure: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    exposure_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    freeze_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    evidence_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    decision: Mapped[str | None] = mapped_column(String(24))
    rationale: Mapped[str | None] = mapped_column(Text)
    opened_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    decided_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    decided_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class SyncReceipt(Base):
    __tablename__ = "sync_receipts"
    __table_args__ = (
        CheckConstraint("receipt_hash ~ '^sha256:[0-9a-f]{64}$'", name="receipt_hash_sha256"),
        UniqueConstraint("package_id", name="uq_sync_receipt_package"),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    package_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.sync_packages.id", ondelete="RESTRICT"),
        nullable=False,
    )
    receipt_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    receipt_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    signature: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class FederationCheckpoint(Base):
    __tablename__ = "federation_checkpoints"
    __table_args__ = (
        CheckConstraint(
            "local_sequence >= 0 AND remote_sequence >= 0", name="sequences_nonnegative"
        ),
        CheckConstraint("checkpoint_hash ~ '^sha256:[0-9a-f]{64}$'", name="checkpoint_hash_sha256"),
        UniqueConstraint("peer_node_id", "checkpoint_hash", name="uq_federation_checkpoint_hash"),
        Index("ix_federation_checkpoints_peer_created", "peer_node_id", "created_at"),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    peer_node_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.external_nodes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    package_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.sync_packages.id", ondelete="RESTRICT"),
        nullable=False,
    )
    local_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    remote_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    local_event_hash: Mapped[str | None] = mapped_column(String(71))
    remote_event_hash: Mapped[str | None] = mapped_column(String(71))
    checkpoint_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class NodeSecurityIncident(Base):
    __tablename__ = "node_security_incidents"
    __table_args__ = (
        CheckConstraint("severity IN ('LOW','MEDIUM','HIGH','CRITICAL')", name="severity_allowed"),
        CheckConstraint(
            "status IN ('OPEN','CONTAINED','RESOLVED','APPEALED')", name="status_allowed"
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_node_security_incidents_node_status", "node_id", "status"),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    node_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.external_nodes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    incident_type: Mapped[str] = mapped_column(String(80), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    earliest_compromise_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    description: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    containment_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    corrective_actions: Mapped[list[object]] = mapped_column(JSONB, nullable=False)
    opened_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT"), nullable=False
    )
    opened_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    resolved_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    resolved_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class NodeKeyRotationRequest(Base):
    __tablename__ = "node_key_rotation_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','APPROVED','REJECTED','CANCELLED')", name="status_allowed"
        ),
        CheckConstraint(
            "reason IN ('SCHEDULED','COMPROMISE','CUSTODY_CHANGE','RECOVERY')",
            name="reason_allowed",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_node_key_rotations_node_status", "node_id", "status"),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    node_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.external_nodes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    old_certificate_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.node_certificates.id", ondelete="RESTRICT"),
        nullable=False,
    )
    new_certificate_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.node_certificates.id", ondelete="RESTRICT"),
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    requested_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT"), nullable=False
    )
    requested_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    decided_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    decided_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    continuity_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class FederationPaperForm(Base):
    __tablename__ = "paper_forms"
    __table_args__ = (
        CheckConstraint(
            "form_type IN ('GOODS_TRANSFER','LOGISTICS_HANDOFF','SERVICE_ACCEPTANCE',"
            "'EMERGENCY_NODE_ACTION','EXCEPTION')",
            name="type_allowed",
        ),
        CheckConstraint("form_version BETWEEN 1 AND 100", name="form_version_bounded"),
        CheckConstraint("status IN ('ISSUED','RECORDED','VOID','EXPIRED')", name="status_allowed"),
        CheckConstraint("expires_at > issued_at", name="expiry_after_issue"),
        CheckConstraint("checksum ~ '^sha256:[0-9a-f]{64}$'", name="checksum_sha256"),
        CheckConstraint(
            "payload_hash IS NULL OR payload_hash ~ '^sha256:[0-9a-f]{64}$'",
            name="payload_hash_sha256",
        ),
        CheckConstraint("version >= 1", name="aggregate_version_positive"),
        UniqueConstraint("external_node_id", "serial_number", name="uq_paper_form_node_serial"),
        UniqueConstraint("qr_reference", name="uq_paper_form_qr_reference"),
        Index("ix_federation_paper_forms_epoch_status", "epoch_id", "status"),
        {"schema": "federation"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    external_node_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.external_nodes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    epoch_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("federation.offline_epochs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    serial_number: Mapped[str] = mapped_column(String(64), nullable=False)
    qr_reference: Mapped[str] = mapped_column(String(220), nullable=False)
    checksum: Mapped[str] = mapped_column(String(71), nullable=False)
    form_type: Mapped[str] = mapped_column(String(32), nullable=False)
    form_version: Mapped[int] = mapped_column(Integer, nullable=False)
    participant_refs: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    operation_constraints: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    payload_hash: Mapped[str | None] = mapped_column(String(71))
    signatures: Mapped[list[object] | None] = mapped_column(JSONB)
    evidence_ids: Mapped[list[str] | None] = mapped_column(JSONB)
    issued_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT"), nullable=False
    )
    issued_by_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.members.id", ondelete="RESTRICT"),
        nullable=False,
    )
    issued_role_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("identity.role_assignments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    issued_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    recorded_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    recorded_by_member_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    recorded_role_assignment_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.role_assignments.id", ondelete="RESTRICT")
    )
    recorded_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    voided_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.users.id", ondelete="RESTRICT")
    )
    voided_by_member_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("identity.members.id", ondelete="RESTRICT")
    )
    voided_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("journal.signed_events.event_id", ondelete="RESTRICT"),
        unique=True,
    )
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    void_reason: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
