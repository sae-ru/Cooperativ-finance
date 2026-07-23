import { commandHeaders, request } from "./admin";

export type FederatedClearingPolicy = {
  id: string;
  policy_code: string;
  policy_version: number;
  valuation_unit: string;
  algorithm_id: string;
  algorithm_version: string;
  decimal_scale: number;
  rounding_mode: "DOWN" | "HALF_EVEN";
  minimum_operation: string;
  max_iterations: number;
  max_cycle_length: number;
  prepare_ttl_seconds: number;
  policy_hash: string;
  status: string;
  created_by_member_id: string;
  created_event_id: string;
  created_at: string;
  version: number;
};

export type InterNodeObligation = {
  id: string;
  home_node_code: string;
  debtor_node_code: string;
  creditor_node_code: string;
  unit_code: string;
  original_amount: string;
  outstanding_amount: string;
  cleared_amount: string;
  source_reference: string;
  source_event_hash: string;
  liquidity_class: string;
  status: string;
  prepared_cycle_id: string | null;
  prepared_input_hash: string | null;
  prepared_until: string | null;
  created_event_id: string;
  created_at: string;
  updated_at: string;
  version: number;
};

export type FederatedClearingCycle = {
  id: string;
  cycle_code: string;
  coordinator_node_code: string;
  policy_id: string;
  period_start: string;
  period_end: string;
  status: string;
  participant_node_codes: string[];
  affected_node_codes: string[];
  input_hash: string | null;
  result_hash: string | null;
  certificate_hash: string | null;
  created_by_member_id: string;
  created_event_id: string;
  created_at: string;
  updated_at: string;
  prepared_at: string | null;
  certified_at: string | null;
  reconciled_at: string | null;
  version: number;
};

export type FederatedArtifact = {
  node_code?: string;
  payload: Record<string, unknown>;
  hash: string;
  signer_fingerprint?: string;
  expires_at?: string;
  approved_at?: string;
  certified_at?: string;
  applied_at?: string;
};

export type FederatedCycleEvidence = {
  cycle: FederatedClearingCycle;
  snapshots: FederatedArtifact[];
  prepare_receipts: FederatedArtifact[];
  proposal: FederatedArtifact | null;
  approvals: FederatedArtifact[];
  certificate: FederatedArtifact | null;
  apply_receipts: FederatedArtifact[];
  proof: FederatedArtifact | null;
};

export type FederatedCommandResult = {
  cycle_id: string | null;
  object_id: string | null;
  event_id: string | null;
  status: string;
  replayed: boolean;
  nodes: Array<{ node_code: string; phase: string; result_code: string }>;
};

export const getFederatedClearingPolicies = () =>
  request<FederatedClearingPolicy[]>("/api/v1/federated-clearing/policies");

export const getInterNodeObligations = () =>
  request<InterNodeObligation[]>("/api/v1/federated-clearing/obligations");

export const getFederatedClearingCycles = () =>
  request<FederatedClearingCycle[]>("/api/v1/federated-clearing/cycles");

export const getFederatedCycleEvidence = (cycleId: string) =>
  request<FederatedCycleEvidence>(`/api/v1/federated-clearing/cycles/${cycleId}`);

export const createFederatedClearingPolicy = (payload: {
  policy_code: string;
  policy_version: number;
  valuation_unit: string;
  decimal_scale: number;
  rounding_mode: "DOWN" | "HALF_EVEN";
  minimum_operation: string;
  max_iterations: number;
  max_cycle_length: number;
  prepare_ttl_seconds: number;
}) => request<FederatedCommandResult>("/api/v1/federated-clearing/policies", {
  method: "POST",
  headers: commandHeaders(),
  body: JSON.stringify(payload),
});

export const createInterNodeObligation = (payload: {
  debtor_node_code: string;
  creditor_node_code: string;
  unit_code: string;
  amount: string;
  source_reference: string;
  source_event_hash: string;
  liquidity_class: string;
}) => request<FederatedCommandResult>("/api/v1/federated-clearing/obligations", {
  method: "POST",
  headers: commandHeaders(),
  body: JSON.stringify(payload),
});

export const createFederatedClearingCycle = (payload: {
  cycle_code: string;
  policy_id: string;
  period_start: string;
  period_end: string;
  participant_node_codes: string[];
}) => request<FederatedCommandResult>("/api/v1/federated-clearing/cycles", {
  method: "POST",
  headers: commandHeaders(),
  body: JSON.stringify(payload),
});

function cycleCommand(cycleId: string, action: string) {
  return request<FederatedCommandResult>(
    `/api/v1/federated-clearing/cycles/${cycleId}/${action}`,
    { method: "POST", headers: commandHeaders() },
  );
}

export const collectFederatedSnapshots = (cycleId: string) =>
  cycleCommand(cycleId, "snapshots/collect");
export const prepareFederatedCycle = (cycleId: string) => cycleCommand(cycleId, "prepare");
export const publishFederatedProposal = (cycleId: string) => cycleCommand(cycleId, "proposal");
export const collectFederatedApprovals = (cycleId: string) =>
  cycleCommand(cycleId, "approvals/collect");
export const approveLocalFederatedCycle = (cycleId: string) =>
  cycleCommand(cycleId, "approvals/local");
export const commitFederatedCycle = (cycleId: string) => cycleCommand(cycleId, "commit");
export const recoverFederatedCycle = (cycleId: string) => cycleCommand(cycleId, "recover");

export const releaseFederatedCycle = (cycleId: string, expired = false) =>
  request<FederatedCommandResult>(`/api/v1/federated-clearing/cycles/${cycleId}/release`, {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify({ expired }),
  });
