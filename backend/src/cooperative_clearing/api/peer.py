"""Authenticated machine-to-machine federation protocol endpoint."""

import base64
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from cooperative_clearing.api.dependencies import get_database, get_settings
from cooperative_clearing.modules.federation.application.peer_protocol import (
    PeerProtocolService,
    SignedPeerResponse,
)
from cooperative_clearing.modules.federation.domain.peer_protocol import (
    OPERATION_CAPABILITY,
    PEER_PROTOCOL_VERSION,
    PeerOperation,
    PeerRequest,
)
from cooperative_clearing.modules.federation.domain.types import federation_error
from cooperative_clearing.shared.core.config import Settings
from cooperative_clearing.shared.infrastructure.database import Database

router = APIRouter(prefix="/api/v1/federation/peer", tags=["federation-peer"])

DatabaseDependency = Annotated[Database, Depends(get_database)]
SettingsDependency = Annotated[Settings, Depends(get_settings)]


class PeerMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal["CC-PEER-1"]
    message_id: UUID
    source_node_code: str = Field(min_length=3, max_length=63)
    target_node_code: str = Field(min_length=3, max_length=63)
    capability: str = Field(min_length=1, max_length=40)
    operation: PeerOperation
    signer_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    payload: dict[str, object]
    payload_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    signature_base64: str = Field(min_length=80, max_length=200)


class PeerMessageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal["CC-PEER-1"]
    message_id: UUID
    request_hash: str
    source_node_code: str
    target_node_code: str
    capability: str
    operation: PeerOperation
    signer_fingerprint: str
    signed_at: datetime
    expires_at: datetime
    payload: dict[str, object]
    payload_hash: str
    signature_base64: str


@router.post("/messages", response_model=PeerMessageResponse)
async def handle_peer_message(
    payload: PeerMessageRequest,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> JSONResponse:
    if payload.protocol_version != PEER_PROTOCOL_VERSION:
        raise federation_error("PEER_PROTOCOL_UNSUPPORTED", 422)
    request = PeerRequest(
        message_id=payload.message_id,
        source_node_code=payload.source_node_code,
        target_node_code=payload.target_node_code,
        operation=payload.operation,
        signer_fingerprint=payload.signer_fingerprint,
        issued_at=payload.issued_at,
        expires_at=payload.expires_at,
        payload=payload.payload,
    )
    expected = request.document()
    if (
        payload.capability != OPERATION_CAPABILITY[payload.operation]
        or payload.payload_hash != expected["payload_hash"]
    ):
        raise federation_error("PEER_REQUEST_CANONICAL_MISMATCH", 422)
    try:
        signature = base64.b64decode(payload.signature_base64, validate=True)
    except ValueError as exc:
        raise federation_error("PEER_REQUEST_SIGNATURE_INVALID", 401) from exc
    async with database.session() as session:
        response = await PeerProtocolService(settings).handle(
            session,
            request=request,
            signature=signature,
        )
        await session.commit()
    return _canonical_peer_response(response)


def _canonical_peer_response(response: SignedPeerResponse) -> JSONResponse:
    # A signed envelope must reach the peer byte-for-byte; response-model
    # datetime serialization would otherwise remove canonical microseconds.
    return JSONResponse(
        content={
            **response.document,
            "signature_base64": base64.b64encode(response.signature).decode("ascii"),
        }
    )
