import { commandHeaders, request } from "./admin";

export type CustodyContinuityStatus =
  | "INVENTORY_PENDING"
  | "PENDING_APPROVAL"
  | "PENDING_ACCEPTANCE"
  | "ACCEPTED"
  | "REJECTED"
  | "BLOCKED";

export type CustodyContinuityItem = {
  id: string;
  lot_id: string;
  lot_number: string;
  product_name: string;
  unit_symbol: string;
  lot_version: number;
  expected_quantity: string;
  actual_quantity: string | null;
  status: "PENDING" | "MATCH" | "DISCREPANCY";
  condition_notes: string | null;
  evidence_ids: string[];
  attested_by_user_id: string | null;
  attested_at: string | null;
  version: number;
};

export type CustodyContinuityCase = {
  id: string;
  cooperative_id: string;
  member_continuity_case_id: string;
  source_member_id: string;
  source_member_name: string;
  warehouse_id: string;
  warehouse_name: string;
  source_assignment_id: string;
  source_assignment_version: number;
  target_member_id: string;
  target_member_name: string;
  target_role_assignment_id: string;
  target_assignment_id: string | null;
  handover_place: string;
  temporary_valid_until: string;
  evidence_refs: string[];
  blocked_reasons: string[];
  status: CustodyContinuityStatus;
  requested_by_user_id: string;
  decided_by_user_id: string | null;
  accepted_by_user_id: string | null;
  decision_reason_code: string | null;
  created_at: string;
  inventory_completed_at: string | null;
  decided_at: string | null;
  accepted_at: string | null;
  updated_at: string;
  version: number;
  items: CustodyContinuityItem[];
};

export type CustodyContinuitySource = {
  member_continuity_case_id: string;
  cooperative_id: string;
  source_assignment_id: string;
  source_assignment_version: number;
  source_member_id: string;
  source_member_name: string;
  warehouse_id: string;
  warehouse_name: string;
  lot_count: number;
};

export type CustodyContinuityCandidate = {
  role_assignment_id: string;
  user_id: string;
  member_id: string;
  display_name: string;
};

export type CustodyContinuityCommand = {
  event_id: string;
  object_id: string;
  status: CustodyContinuityStatus;
  replayed: boolean;
};

export const getCustodyContinuityCases = () =>
  request<CustodyContinuityCase[]>(
    "/api/v1/inventory/custody-continuity-cases",
  );

export const getCustodyContinuitySources = () =>
  request<CustodyContinuitySource[]>(
    "/api/v1/inventory/custody-continuity-sources",
  );

export const getCustodyContinuityCandidates = (
  cooperativeId: string,
  warehouseId: string,
) =>
  request<CustodyContinuityCandidate[]>(
    `/api/v1/inventory/custody-continuity-candidates?cooperative_id=${encodeURIComponent(cooperativeId)}&warehouse_id=${encodeURIComponent(warehouseId)}`,
  );

export const requestCustodyContinuity = (payload: {
  member_continuity_case_id: string;
  source_assignment_id: string;
  expected_source_assignment_version: number;
  target_role_assignment_id: string;
  handover_place: string;
  temporary_valid_until: string;
  evidence_refs: string[];
}) =>
  request<CustodyContinuityCommand>(
    "/api/v1/inventory/custody-continuity-cases",
    {
      method: "POST",
      headers: commandHeaders(),
      body: JSON.stringify(payload),
    },
  );

export const attestCustodyContinuityItem = (
  continuityCase: CustodyContinuityCase,
  item: CustodyContinuityItem,
  payload: {
    actual_quantity: string;
    condition_notes: string;
    evidence_ids: string[];
  },
) =>
  request<CustodyContinuityCommand>(
    `/api/v1/inventory/custody-continuity-cases/${continuityCase.id}/items/${item.id}/attest`,
    {
      method: "POST",
      headers: commandHeaders(),
      body: JSON.stringify({
        ...payload,
        expected_case_version: continuityCase.version,
        expected_item_version: item.version,
      }),
    },
  );

export const decideCustodyContinuity = (
  continuityCase: CustodyContinuityCase,
  approve: boolean,
  reasonCode: string,
) =>
  request<CustodyContinuityCommand>(
    `/api/v1/inventory/custody-continuity-cases/${continuityCase.id}/decision`,
    {
      method: "POST",
      headers: commandHeaders(),
      body: JSON.stringify({
        approve,
        expected_version: continuityCase.version,
        reason_code: reasonCode,
      }),
    },
  );

export const decideCustodyContinuityCandidate = (
  continuityCase: CustodyContinuityCase,
  accept: boolean,
  evidenceIds: string[],
) =>
  request<CustodyContinuityCommand>(
    `/api/v1/inventory/custody-continuity-cases/${continuityCase.id}/candidate-decision`,
    {
      method: "POST",
      headers: commandHeaders(),
      body: JSON.stringify({
        accept,
        expected_version: continuityCase.version,
        evidence_ids: evidenceIds,
        reason_code: accept ? "PERSONAL_ACCEPTANCE" : "CANDIDATE_DECLINED",
      }),
    },
  );
