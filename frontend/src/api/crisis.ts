import { commandHeaders, request } from "./admin";

export type ReserveTarget = {
  id: string; cooperative_id: string; resource_code: string; resource_name: string; unit_code: string;
  target_quantity: string; critical_minimum: string; warning_coverage_days: string; critical_coverage_days: string;
  max_snapshot_age_hours: number; policy_version: number; terms_hash: string; status: string;
  proposed_by_member_id: string; approved_by_member_id: string | null; created_at: string; approved_at: string | null; version: number;
};

export type ReserveSnapshot = {
  id: string; target_id: string; physical_verified_quantity: string; committed_quantity: string;
  available_quantity: string; consumption_rate_per_day: string; coverage_days: string | null;
  expiring_quantity: string; quality_status: string; confidence: string; reserve_level: string;
  observed_at: string; snapshot_hash: string; recorded_by_member_id: string; created_at: string;
};

export type CrisisMandate = {
  id: string; cooperative_id: string; mandate_code: string; crisis_type: string; scope_payload: Record<string, unknown>;
  capabilities: string[]; rationale: string; exit_criteria: string; safe_state: string; policy_version: number;
  starts_at: string; review_at: string; expires_at: string; maximum_end_at: string; terms_hash: string;
  status: string; effective_status: string | null; proposed_by_member_id: string; activated_by_member_id: string | null;
  closed_by_member_id: string | null; created_at: string; activated_at: string | null; closed_at: string | null; version: number;
};

export type CrisisReview = {
  id: string; mandate_id: string; decision_round: number; decision: string; facts_payload: Record<string, unknown>;
  rationale: string; previous_review_at: string; previous_expires_at: string; new_review_at: string | null;
  new_expires_at: string | null; reviewer_member_id: string; created_at: string;
};

export type RationingRule = {
  id: string; mandate_id: string; target_id: string; policy_version: number; formula: string;
  eligibility_policy: Record<string, unknown>; protected_minimum: string; maximum_per_member: string; period_hours: number;
  terms_hash: string; status: string; proposed_by_member_id: string; approved_by_member_id: string | null;
  created_at: string; approved_at: string | null; version: number;
};

export type RationingPlan = {
  id: string; rule_id: string; snapshot_id: string; available_input: string; eligible_count: number;
  total_allocated: string; input_hash: string; allocations_hash: string; status: string; expires_at: string;
  proposed_by_member_id: string; confirmed_by_member_id: string | null; created_at: string; confirmed_at: string | null; version: number;
};

export type RationingAllocation = {
  id: string; plan_id: string; member_id: string; weight: number; quantity: string; status: string;
  created_at: string; issued_at: string | null;
};

export type RationIssuance = {
  id: string; allocation_id: string; quantity: string; acknowledgement: string; issued_by_member_id: string; created_at: string;
};

export type CrisisPaperForm = {
  id: string; cooperative_id: string; mandate_id: string; serial_number: string; checksum: string; form_type: string;
  assigned_to_member_id: string; status: string; issued_at: string; expires_at: string; payload_hash: string | null;
  issued_by_member_id: string; recorded_by_member_id: string | null; recorded_at: string | null;
};

export type CrisisReport = {
  id: string; mandate_id: string; report_payload: Record<string, unknown>; report_hash: string; generated_at: string;
};

export type CrisisOperatorWorkspace = {
  active_targets: ReserveTarget[]; active_mandates: CrisisMandate[]; active_rules: RationingRule[];
  confirmed_plans: RationingPlan[]; issued_forms: CrisisPaperForm[];
};

export type CrisisControllerWorkspace = {
  draft_targets: ReserveTarget[]; draft_mandates: CrisisMandate[]; due_reviews: CrisisMandate[];
  draft_rules: RationingRule[]; previewed_plans: RationingPlan[]; issued_forms: CrisisPaperForm[];
};

type CommandResult = { event_id: string; object_id: string; replayed: boolean };

function command<T extends object>(path: string, payload: T) {
  return request<CommandResult>(path, { method: "POST", headers: commandHeaders(), body: JSON.stringify(payload) });
}

function withId(path: string, key: string, id?: string) {
  return id ? `${path}?${key}=${encodeURIComponent(id)}` : path;
}

export const getReserveTargets = () => request<ReserveTarget[]>("/api/v1/crisis/reserve-targets");
export const getReserveSnapshots = (targetId?: string) => request<ReserveSnapshot[]>(withId("/api/v1/crisis/reserve-snapshots", "target_id", targetId));
export const getCrisisMandates = () => request<CrisisMandate[]>("/api/v1/crisis/mandates");
export const getCrisisReviews = (mandateId?: string) => request<CrisisReview[]>(withId("/api/v1/crisis/reviews", "mandate_id", mandateId));
export const getRationingRules = (mandateId?: string) => request<RationingRule[]>(withId("/api/v1/crisis/rationing-rules", "mandate_id", mandateId));
export const getRationingPlans = (ruleId?: string) => request<RationingPlan[]>(withId("/api/v1/crisis/rationing-plans", "rule_id", ruleId));
export const getRationingAllocations = (planId?: string) => request<RationingAllocation[]>(withId("/api/v1/crisis/rationing-allocations", "plan_id", planId));
export const getRationIssuances = () => request<RationIssuance[]>("/api/v1/crisis/ration-issuances");
export const getCrisisPaperForms = (mandateId?: string) => request<CrisisPaperForm[]>(withId("/api/v1/crisis/paper-forms", "mandate_id", mandateId));
export const getCrisisReports = () => request<CrisisReport[]>("/api/v1/crisis/reports");
export const getCrisisOperatorWorkspace = () => request<CrisisOperatorWorkspace>("/api/v1/crisis/workspaces/operator");
export const getCrisisControllerWorkspace = () => request<CrisisControllerWorkspace>("/api/v1/crisis/workspaces/controller");

export const proposeReserveTarget = (payload: Record<string, unknown>) => command("/api/v1/crisis/reserve-targets", payload);
export const approveReserveTarget = (item: ReserveTarget) => command(`/api/v1/crisis/reserve-targets/${item.id}/approval`, { expected_version: item.version });
export const recordReserveSnapshot = (payload: Record<string, unknown>) => command("/api/v1/crisis/reserve-snapshots", payload);
export const proposeCrisisMandate = (payload: Record<string, unknown>) => command("/api/v1/crisis/mandates", payload);
export const activateCrisisMandate = (item: CrisisMandate) => command(`/api/v1/crisis/mandates/${item.id}/activation`, { expected_version: item.version, terms_hash: item.terms_hash });
export const reviewCrisisMandate = (item: CrisisMandate, payload: Record<string, unknown>) => command(`/api/v1/crisis/mandates/${item.id}/review`, { expected_version: item.version, ...payload });
export const closeCrisisMandate = (item: CrisisMandate, reconciliationNote: string) => command(`/api/v1/crisis/mandates/${item.id}/close`, { expected_version: item.version, reconciliation_note: reconciliationNote, corrective_actions: [] });
export const expireCrisisMandate = (item: CrisisMandate, reconciliationNote: string) => command(`/api/v1/crisis/mandates/${item.id}/expire`, { expected_version: item.version, reconciliation_note: reconciliationNote, corrective_actions: [] });
export const proposeRationingRule = (payload: Record<string, unknown>) => command("/api/v1/crisis/rationing-rules", payload);
export const approveRationingRule = (item: RationingRule) => command(`/api/v1/crisis/rationing-rules/${item.id}/approval`, { expected_version: item.version, terms_hash: item.terms_hash });
export const previewRationingPlan = (ruleId: string, eligibleMembers: Array<{ member_id: string; weight: number }>) => command(`/api/v1/crisis/rationing-rules/${ruleId}/previews`, { eligible_members: eligibleMembers });
export const confirmRationingPlan = (item: RationingPlan) => command(`/api/v1/crisis/rationing-plans/${item.id}/confirmation`, { expected_version: item.version, allocations_hash: item.allocations_hash });
export const cancelRationingPlan = (item: RationingPlan, rationale: string) => command(`/api/v1/crisis/rationing-plans/${item.id}/cancel`, { expected_version: item.version, rationale });
export const issueRation = (allocationId: string, acknowledgement: string, evidenceIds: string[]) => command(`/api/v1/crisis/rationing-allocations/${allocationId}/issuance`, { acknowledgement, evidence_ids: evidenceIds });
export const issueCrisisPaperForm = (payload: Record<string, unknown>) => command("/api/v1/crisis/paper-forms", payload);
export const recordCrisisPaperForm = (formId: string, checksum: string, payload: Record<string, unknown>) => command(`/api/v1/crisis/paper-forms/${formId}/record`, { checksum, payload });
