import { commandHeaders, request, requestDirect } from "./admin";

export type LotBalance = {
  lot_id: string;
  verified_quantity: string;
  available_quantity: string;
  reserved_quantity: string;
  rights_issued_quantity: string;
  redeemed_quantity: string;
  quarantined_quantity: string;
  backing_shortfall_quantity: string;
  version: number;
  updated_at: string;
};

export type CommodityRight = {
  id: string;
  cooperative_id: string;
  lot_id: string;
  owner_member_id: string;
  original_owner_member_id: string;
  quantity: string;
  unit_id: string;
  status: string;
  redeem_warehouse_id: string;
  valid_until: string | null;
  reservation_id: string;
  issued_by_member_id: string;
  issued_role_assignment_id: string;
  issued_event_id: string;
  frozen_previous_status: string | null;
  freeze_reason: string | null;
  frozen_event_id: string | null;
  redeemed_event_id: string | null;
  created_at: string;
  updated_at: string;
  version: number;
};

export type RightRedemption = {
  id: string;
  right_id: string;
  lot_id: string;
  owner_member_id: string;
  warehouse_id: string;
  custodian_assignment_id: string;
  quantity: string;
  status: string;
  requested_by_user_id: string;
  fulfilled_by_user_id: string | null;
  requested_event_id: string;
  completed_event_id: string | null;
  requested_at: string;
  completed_at: string | null;
};

export type RightTransfer = {
  id: string;
  right_id: string;
  from_member_id: string;
  to_member_id: string;
  quantity: string;
  performed_by_user_id: string;
  event_id: string;
  created_at: string;
};

export type RightProofEvent = {
  event_id: string;
  event_type: string;
  aggregate_type: string;
  aggregate_id: string;
  aggregate_version: number;
  local_sequence: number;
  occurred_at: string;
  event_hash: string;
  payload: Record<string, unknown>;
};

export type RightProof = {
  proof_hash: string;
  right: CommodityRight;
  balance: LotBalance;
  lot_number: string;
  lot_status: string;
  current_quantity: string | null;
  original_owner_name: string;
  current_owner_name: string;
  reservation: {
    id: string;
    lot_id: string;
    purpose_type: string;
    purpose_id: string;
    quantity: string;
    status: string;
    expires_at: string | null;
    created_event_id: string;
    completed_event_id: string | null;
    created_at: string;
  };
  transfers: RightTransfer[];
  redemption: RightRedemption | null;
  signed_events: RightProofEvent[];
  generated_at: string;
};

type CommandResult = { event_id: string; object_id: string; replayed: boolean };

export const getLotBalances = () => request<LotBalance[]>("/api/v1/rights/balances");
export const getCommodityRights = () => request<CommodityRight[]>("/api/v1/rights");
export const getRightRedemptions = () =>
  request<RightRedemption[]>("/api/v1/rights/redemptions");
export const getRightProof = (rightId: string) =>
  requestDirect<RightProof>(`/api/v1/rights/${rightId}/proof`);

export const issueCommodityRight = (payload: {
  lot_id: string;
  owner_member_id: string;
  quantity: string;
  redeem_warehouse_id: string;
  valid_until: string | null;
  expected_balance_version: number;
}) => request<CommandResult>("/api/v1/rights", {
  method: "POST",
  headers: commandHeaders(),
  body: JSON.stringify(payload),
});

export const transferCommodityRight = (
  right: CommodityRight,
  toMemberId: string,
  evidenceIds: string[],
) => request<CommandResult>(`/api/v1/rights/${right.id}/transfer`, {
  method: "POST",
  headers: commandHeaders(),
  body: JSON.stringify({
    from_member_id: right.owner_member_id,
    to_member_id: toMemberId,
    evidence_ids: evidenceIds,
    expected_version: right.version,
  }),
});

export const freezeCommodityRight = (
  right: CommodityRight,
  reasonCode: string,
  decisionReference: string,
) => request<CommandResult>(`/api/v1/rights/${right.id}/freeze`, {
  method: "POST",
  headers: commandHeaders(),
  body: JSON.stringify({
    reason_code: reasonCode,
    decision_reference: decisionReference,
    expected_version: right.version,
  }),
});

export const unfreezeCommodityRight = (
  right: CommodityRight,
  decisionReference: string,
) => request<CommandResult>(`/api/v1/rights/${right.id}/unfreeze`, {
  method: "POST",
  headers: commandHeaders(),
  body: JSON.stringify({
    decision_reference: decisionReference,
    expected_version: right.version,
  }),
});

export const requestRightRedemption = (right: CommodityRight) =>
  request<CommandResult>(`/api/v1/rights/${right.id}/redemptions`, {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify({
      owner_member_id: right.owner_member_id,
      expected_version: right.version,
    }),
  });

export const completeRightRedemption = (
  redemption: RightRedemption,
  right: CommodityRight,
  evidenceIds: string[],
) => request<CommandResult>(`/api/v1/rights/redemptions/${redemption.id}/complete`, {
  method: "POST",
  headers: commandHeaders(),
  body: JSON.stringify({
    evidence_ids: evidenceIds,
    expected_right_version: right.version,
  }),
});
