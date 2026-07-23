"""Scoped candidate lookup without exposing the security user registry."""

from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from cooperative_clearing.api.dependencies import DatabaseDependency, PrincipalDependency
from cooperative_clearing.modules.identity.domain.types import RoleCode, require_role
from cooperative_clearing.modules.identity.infrastructure.models import (
    Member,
    RoleAssignment,
    UserAccount,
)
from cooperative_clearing.modules.responsibility.application.service import PROPOSER_ROLES
from cooperative_clearing.shared.core.request_context import get_request_id

router = APIRouter(prefix="/api/v1/responsibility", tags=["responsibility"])


class ResponsibilityCandidateResponse(BaseModel):
    role_assignment_id: UUID
    user_id: UUID
    member_id: UUID
    display_name: str
    role_code: RoleCode


class ResponsibilityCandidateCollection(BaseModel):
    data: list[ResponsibilityCandidateResponse]
    request_id: str


@router.get("/candidates", response_model=ResponsibilityCandidateCollection)
async def list_candidates(
    cooperative_id: UUID,
    principal: PrincipalDependency,
    database: DatabaseDependency,
) -> ResponsibilityCandidateCollection:
    require_role(principal, PROPOSER_ROLES, cooperative_id)
    async with database.session() as session:
        rows = list(
            (
                await session.execute(
                    select(RoleAssignment, UserAccount, Member)
                    .join(UserAccount, UserAccount.id == RoleAssignment.user_id)
                    .join(Member, Member.id == UserAccount.member_id)
                    .where(
                        RoleAssignment.status == "ACTIVE",
                        RoleAssignment.cooperative_id == cooperative_id,
                        UserAccount.status == "ACTIVE",
                        Member.status.in_(["ACTIVE", "LIMITED"]),
                    )
                    .order_by(Member.display_name, RoleAssignment.role_code)
                )
            ).all()
        )
    return ResponsibilityCandidateCollection(
        data=[
            ResponsibilityCandidateResponse(
                role_assignment_id=role.id,
                user_id=user.id,
                member_id=member.id,
                display_name=member.display_name,
                role_code=RoleCode(role.role_code),
            )
            for role, user, member in rows
        ],
        request_id=get_request_id(),
    )
