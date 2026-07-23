import { commandHeaders, request } from "./admin";

export type ClearingPolicy = {
  id: string;
  cooperative_id: string;
  policy_version: number;
  valuation_unit_id: string;
  algorithm_id: string;
  algorithm_version: string;
  decimal_scale: number;
  rounding_mode: "DOWN" | "HALF_EVEN";
  minimum_operation: string;
  max_iterations: number;
  max_cycle_length: number;
  dispute_window_seconds: number;
  required_approvals: number;
  liquidity_order: string[];
  terms_hash: string;
  status: string;
  proposed_by_member_id: string;
  approved_by_member_id: string | null;
  created_at: string;
  approved_at: string | null;
  version: number;
};

export type ClearingCycle = {
  id: string;
  cooperative_id: string;
  policy_id: string;
  cycle_code: string;
  period_start: string;
  period_end: string;
  status: string;
  collected_count: number;
  input_hash: string | null;
  parameters_hash: string | null;
  result_hash: string | null;
  dispute_until: string | null;
  created_by_member_id: string;
  created_event_id: string;
  previewed_at: string | null;
  finalized_at: string | null;
  reconciled_at: string | null;
  created_at: string;
  updated_at: string;
  version: number;
};

export type ClearingInputSnapshot = {
  id: string;
  cycle_id: string;
  input_version: number;
  policy_version: number;
  ordered_payload: Record<string, unknown>;
  input_hash: string;
  frozen_by_member_id: string;
  frozen_event_id: string;
  frozen_at: string;
};

export type ClearingEntry = {
  id: string;
  cycle_id: string;
  obligation_id: string;
  debtor_member_id: string;
  creditor_member_id: string;
  unit_id: string;
  obligation_version: number;
  amount_before: string;
  cleared_amount: string;
  amount_after: string;
  inclusion_status: string;
  exclusion_reason: string | null;
  allocations: Array<Record<string, unknown>>;
  created_at: string;
};

export type ClearingPosition = {
  id: string;
  cycle_id: string;
  member_id: string;
  unit_id: string;
  incoming_before: string;
  outgoing_before: string;
  incoming_cleared: string;
  outgoing_cleared: string;
  incoming_after: string;
  outgoing_after: string;
  net_before: string;
  net_after: string;
};

export type ClearingApproval = {
  id: string;
  cycle_id: string;
  approval_type: string;
  input_hash: string;
  result_hash: string;
  member_id: string;
  role_assignment_id: string;
  event_id: string;
  approved_at: string;
};

export type ClearingDispute = {
  id: string;
  cycle_id: string;
  entry_id: string;
  reason_code: string;
  statement: string;
  evidence_refs: Array<Record<string, unknown>>;
  status: string;
  opened_by_member_id: string;
  opened_event_id: string;
  resolution_notes: string | null;
  resolved_by_member_id: string | null;
  resolution_event_id: string | null;
  created_at: string;
  resolved_at: string | null;
  version: number;
};

export type ClearingProof = {
  id: string;
  cycle_id: string;
  proof_payload: Record<string, unknown>;
  proof_hash: string;
  finalized_event_id: string;
  node_event_hash: string;
  created_at: string;
};

export type ClearingStatement = {
  id: string;
  cycle_id: string;
  member_id: string;
  unit_id: string;
  statement_payload: Record<string, unknown>;
  statement_hash: string;
  created_event_id: string;
  created_at: string;
};

export type ClearingAccountingExport = {
  id: string;
  cycle_id: string;
  export_payload: Record<string, unknown>;
  package_hash: string;
  created_event_id: string;
  created_at: string;
};

export type ClearingVerification = {
  valid: boolean;
  input_hash: string;
  parameters_hash: string;
  result_hash: string;
  proof_hash: string;
};

type CommandResult = { event_id: string; object_id: string; replayed: boolean };

export const getClearingPolicies = () =>
  request<ClearingPolicy[]>("/api/v1/clearing/policies");
export const getClearingCycles = () =>
  request<ClearingCycle[]>("/api/v1/clearing/cycles");
export const getClearingInput = (cycleId: string) =>
  request<ClearingInputSnapshot>(`/api/v1/clearing/cycles/${cycleId}/input`);
export const getClearingEntries = (cycleId: string) =>
  request<ClearingEntry[]>(`/api/v1/clearing/cycles/${cycleId}/entries`);
export const getClearingPositions = (cycleId: string) =>
  request<ClearingPosition[]>(`/api/v1/clearing/cycles/${cycleId}/positions`);
export const getClearingApprovals = (cycleId: string) =>
  request<ClearingApproval[]>(`/api/v1/clearing/cycles/${cycleId}/approvals`);
export const getClearingDisputes = (cycleId: string) =>
  request<ClearingDispute[]>(`/api/v1/clearing/cycles/${cycleId}/disputes`);
export const getClearingProof = (cycleId: string) =>
  request<ClearingProof>(`/api/v1/clearing/cycles/${cycleId}/proof`);
export const getClearingStatements = (cycleId: string, memberId: string) =>
  request<ClearingStatement[]>(
    `/api/v1/clearing/cycles/${cycleId}/statements/${memberId}`,
  );
export const getClearingAccountingExport = (cycleId: string) =>
  request<ClearingAccountingExport>(
    `/api/v1/clearing/cycles/${cycleId}/accounting-export`,
  );

export const proposeClearingPolicy = (payload: {
  cooperative_id: string;
  valuation_unit_id: string;
  decimal_scale: number;
  rounding_mode: "DOWN" | "HALF_EVEN";
  minimum_operation: string;
  max_iterations: number;
  max_cycle_length: number;
  dispute_window_seconds: number;
  required_approvals: number;
  liquidity_order: string[];
}) => request<CommandResult>("/api/v1/clearing/policies", {
  method: "POST",
  headers: commandHeaders(),
  body: JSON.stringify(payload),
});

export const approveClearingPolicy = (policy: ClearingPolicy) =>
  request<CommandResult>(`/api/v1/clearing/policies/${policy.id}/approval`, {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify({ expected_version: policy.version }),
  });

export const createClearingCycle = (payload: {
  cooperative_id: string;
  policy_id: string;
  cycle_code: string;
  period_start: string;
  period_end: string;
}) => request<CommandResult>("/api/v1/clearing/cycles", {
  method: "POST",
  headers: commandHeaders(),
  body: JSON.stringify(payload),
});

function versionCommand(cycle: ClearingCycle, action: string) {
  return request<CommandResult>(`/api/v1/clearing/cycles/${cycle.id}/${action}`, {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify({ expected_version: cycle.version }),
  });
}

export const collectClearingCycle = (cycle: ClearingCycle) => versionCommand(cycle, "collect");
export const freezeClearingInput = (cycle: ClearingCycle) =>
  versionCommand(cycle, "freeze-input");
export const previewClearingCycle = (cycle: ClearingCycle) => versionCommand(cycle, "preview");
export const markClearingReady = (cycle: ClearingCycle) => versionCommand(cycle, "ready");
export const reconcileClearingCycle = (cycle: ClearingCycle) =>
  versionCommand(cycle, "reconcile");

export const approveClearingPreview = (cycle: ClearingCycle) =>
  request<CommandResult>(`/api/v1/clearing/cycles/${cycle.id}/approvals`, {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify({
      expected_version: cycle.version,
      input_hash: cycle.input_hash,
      result_hash: cycle.result_hash,
    }),
  });

export const finalizeClearingCycle = (cycle: ClearingCycle) =>
  request<CommandResult>(`/api/v1/clearing/cycles/${cycle.id}/finalize`, {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify({
      expected_version: cycle.version,
      result_hash: cycle.result_hash,
    }),
  });

export const openClearingDispute = (
  cycle: ClearingCycle,
  payload: {
    entry_id: string;
    reason_code: string;
    statement: string;
    evidence_ids: string[];
  },
) => request<CommandResult>(`/api/v1/clearing/cycles/${cycle.id}/disputes`, {
  method: "POST",
  headers: commandHeaders(),
  body: JSON.stringify({ ...payload, expected_version: cycle.version }),
});

export const decideClearingDispute = (
  cycle: ClearingCycle,
  dispute: ClearingDispute,
  decision: "UPHOLD" | "REJECT",
  resolutionNotes: string,
) => request<CommandResult>(`/api/v1/clearing/disputes/${dispute.id}/decision`, {
  method: "POST",
  headers: commandHeaders(),
  body: JSON.stringify({
    decision,
    resolution_notes: resolutionNotes,
    expected_version: dispute.version,
    expected_cycle_version: cycle.version,
  }),
});

export const verifyClearingProof = (proof: Record<string, unknown>) =>
  request<ClearingVerification>("/api/v1/clearing/proofs/verify", {
    method: "POST",
    body: JSON.stringify({ proof }),
  });
