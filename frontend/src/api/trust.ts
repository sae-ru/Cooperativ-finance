import { commandHeaders, request } from "./admin";

export type TrustPolicy = {
  id: string;
  cooperative_id: string;
  policy_version: number;
  semantic_version: string;
  appeal_window_seconds: number;
  max_protective_seconds: number;
  panel_quorum: number;
  status: string;
  version: number;
};

export type TrustCase = {
  id: string;
  cooperative_id: string;
  policy_id: string;
  case_reference: string;
  subject_member_id: string;
  claimant_member_id: string;
  source_type: string;
  source_reference: string;
  source_event_ids: string[];
  evidence_refs: Array<Record<string, unknown>>;
  summary: string;
  facts: string;
  requested_outcome: string;
  confidentiality: string;
  status: string;
  opened_by_member_id: string;
  response_text: string | null;
  response_evidence_refs: Array<Record<string, unknown>> | null;
  opened_at: string;
  responded_at: string | null;
  original_decision_at: string | null;
  appeal_until: string | null;
  closed_at: string | null;
  version: number;
};

export type TrustConflict = {
  id: string;
  case_id: string;
  stage: string;
  member_id: string;
  assessment: string;
  relationship: string;
  rationale: string;
  declared_at: string;
};

export type ProtectiveMeasure = {
  id: string;
  case_id: string;
  subject_member_id: string;
  measure_type: string;
  scope: Record<string, unknown>;
  rationale: string;
  status: string;
  starts_at: string;
  expires_at: string;
  review_at: string;
  lift_reason: string | null;
  version: number;
};

export type TrustDecision = {
  id: string;
  case_id: string;
  stage: string;
  decision_round: number;
  related_object_id: string | null;
  outcome: string;
  standard_of_proof: string;
  fault_class: string | null;
  causal_findings: Record<string, unknown>;
  established_loss: string | null;
  reasoning: string;
  consequence_spec: Record<string, unknown>;
  evidence_refs: Array<Record<string, unknown>>;
  panel_snapshot: Array<Record<string, unknown>>;
  policy_version: string;
  issued_by_member_id: string;
  issued_at: string;
};

export type TrustSanction = {
  id: string;
  case_id: string;
  decision_id: string;
  subject_member_id: string;
  measure_type: string;
  severity: string;
  scope: Record<string, unknown>;
  rationale: string;
  status: string;
  starts_at: string;
  expires_at: string | null;
  review_at: string | null;
  appeal_until: string;
  revocation_reason: string | null;
  version: number;
};

export type TrustAppeal = {
  id: string;
  case_id: string;
  original_decision_id: string;
  sanction_id: string | null;
  appellant_member_id: string;
  grounds: string;
  status: string;
  outcome: string | null;
  submitted_at: string;
  decided_at: string | null;
};

export type ReputationEvent = {
  id: string;
  cooperative_id: string;
  case_id: string | null;
  decision_id: string | null;
  subject_member_id: string;
  context: string;
  classification: string;
  severity: number;
  confidence: string;
  observation_start: string;
  observation_end: string;
  appeal_state: string;
  status: string;
  visibility: string;
  corrects_event_id: string | null;
  created_at: string;
};

export type ContextProfile = {
  context: string;
  confirmed_fulfillments: number;
  confirmed_breaches: number;
  self_reported_errors: number;
  rehabilitation_events: number;
  disputed_events: number;
  voided_events: number;
  corrections: number;
  sample_count: number;
  confidence_min: string | null;
  confidence_max: string | null;
  last_observation: string | null;
  source_event_ids: string[];
};

export type ReliabilityProfile = {
  subject_member_id: string;
  contexts: ContextProfile[];
  active_measures: number;
  active_sanctions: number;
  rehabilitation_active: number;
  generated_at: string;
};

export type RehabilitationPlan = {
  id: string;
  case_id: string;
  decision_id: string;
  subject_member_id: string;
  title: string;
  completion_criteria: Record<string, unknown>;
  status: string;
  starts_at: string;
  due_at: string;
  closure_reason: string | null;
  created_at: string;
  closed_at: string | null;
  version: number;
};

export type RehabilitationStep = {
  id: string;
  plan_id: string;
  sequence: number;
  description: string;
  completion_criterion: string;
  status: string;
  evidence_refs: Array<Record<string, unknown>>;
  completed_at: string | null;
};

export type ArbitratorWorkspace = {
  ready_cases: TrustCase[];
  submitted_appeals: TrustAppeal[];
  active_measures: ProtectiveMeasure[];
};

export type AuditorWorkspace = {
  cases_needing_review: TrustCase[];
  active_measures: ProtectiveMeasure[];
  disputed_reputation_events: ReputationEvent[];
  active_rehabilitation_plans: RehabilitationPlan[];
};

type CommandResult = { event_id: string; object_id: string; replayed: boolean };

function command<T extends object>(path: string, payload: T) {
  return request<CommandResult>(path, {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify(payload),
  });
}

export const getTrustPolicies = () => request<TrustPolicy[]>("/api/v1/trust/policies");
export const getTrustCases = () => request<TrustCase[]>("/api/v1/trust/cases");
export const getTrustConflicts = (caseId: string) =>
  request<TrustConflict[]>(`/api/v1/trust/cases/${caseId}/conflicts`);
export const getProtectiveMeasures = (caseId: string) =>
  request<ProtectiveMeasure[]>(`/api/v1/trust/cases/${caseId}/measures`);
export const getTrustDecisions = (caseId: string) =>
  request<TrustDecision[]>(`/api/v1/trust/cases/${caseId}/decisions`);
export const getTrustAppeals = () => request<TrustAppeal[]>("/api/v1/trust/appeals");
export const getTrustSanctions = () => request<TrustSanction[]>("/api/v1/trust/sanctions");
export const getReputationEvents = () =>
  request<ReputationEvent[]>("/api/v1/trust/reputation/events");
export const getReliabilityProfile = (memberId: string) =>
  request<ReliabilityProfile>(`/api/v1/trust/reputation/profiles/${memberId}`);
export const getRehabilitationPlans = () =>
  request<RehabilitationPlan[]>("/api/v1/trust/rehabilitation-plans");
export const getRehabilitationSteps = (planId: string) =>
  request<RehabilitationStep[]>(`/api/v1/trust/rehabilitation-plans/${planId}/steps`);
export const getArbitratorWorkspace = () =>
  request<ArbitratorWorkspace>("/api/v1/trust/workspaces/arbitrator");
export const getAuditorWorkspace = () =>
  request<AuditorWorkspace>("/api/v1/trust/workspaces/auditor");

export const proposeTrustPolicy = (payload: {
  cooperative_id: string;
  semantic_version: string;
  appeal_window_seconds: number;
  max_protective_seconds: number;
  panel_quorum: number;
  terms: Record<string, unknown>;
}) => command("/api/v1/trust/policies", payload);

export const approveTrustPolicy = (policy: TrustPolicy) =>
  command(`/api/v1/trust/policies/${policy.id}/approval`, { expected_version: policy.version });

export const openTrustCase = (payload: {
  cooperative_id: string;
  case_reference: string;
  subject_member_id: string;
  claimant_member_id: string;
  source_type: string;
  source_reference: string;
  source_event_ids: string[];
  evidence_ids: string[];
  summary: string;
  facts: string;
  requested_outcome: string;
  confidentiality: "NORMAL" | "RESTRICTED";
}) => command("/api/v1/trust/cases", payload);

export const respondToTrustCase = (
  item: TrustCase,
  responseText: string,
  evidenceIds: string[],
) => command(`/api/v1/trust/cases/${item.id}/responses`, {
  expected_version: item.version,
  response_text: responseText,
  evidence_ids: evidenceIds,
});

export const markTrustCaseReady = (item: TrustCase, reviewNote: string) =>
  command(`/api/v1/trust/cases/${item.id}/ready`, {
    expected_version: item.version,
    review_note: reviewNote,
  });

export const declareTrustConflict = (
  caseId: string,
  stage: "ORIGINAL" | "APPEAL" | "REHABILITATION",
  assessment: "CLEAR" | "CONFLICT",
  rationale: string,
) => command(`/api/v1/trust/cases/${caseId}/conflicts`, {
  stage,
  assessment,
  relationship: assessment === "CLEAR" ? "No declared relationship" : "Declared relationship",
  rationale,
});

export const imposeProtectiveMeasure = (
  item: TrustCase,
  payload: {
    measure_type: string;
    scope: Record<string, unknown>;
    rationale: string;
    expires_at: string;
    review_at: string;
  },
) => command(`/api/v1/trust/cases/${item.id}/protective-measures`, {
  ...payload,
  expected_case_version: item.version,
});

export const liftProtectiveMeasure = (item: ProtectiveMeasure, reason: string) =>
  command(`/api/v1/trust/protective-measures/${item.id}/lift`, {
    expected_version: item.version,
    reason,
  });

export const issueOriginalDecision = (
  item: TrustCase,
  payload: {
    outcome: "SUBSTANTIATED" | "PARTLY_SUBSTANTIATED" | "UNSUBSTANTIATED";
    standard_of_proof: string;
    fault_class: string | null;
    causal_findings: Record<string, unknown>;
    established_loss: string | null;
    reasoning: string;
    consequence_spec: Record<string, unknown>;
    evidence_ids: string[];
  },
) => command(`/api/v1/trust/cases/${item.id}/decisions`, {
  ...payload,
  expected_case_version: item.version,
});

export const proposeTrustSanction = (
  decisionId: string,
  payload: {
    measure_type: string;
    severity: string;
    scope: Record<string, unknown>;
    rationale: string;
    starts_at: string;
    expires_at: string | null;
    review_at: string | null;
  },
) => command(`/api/v1/trust/decisions/${decisionId}/sanctions`, payload);

export const recordReputationEvent = (
  decisionId: string,
  payload: {
    context: string;
    classification: string;
    severity: number;
    confidence: string;
    observation_start: string;
    observation_end: string;
    source_event_ids: string[];
    evidence_ids: string[];
    visibility: string;
  },
) => command(`/api/v1/trust/decisions/${decisionId}/reputation-events`, payload);

export const submitTrustAppeal = (
  item: TrustCase,
  originalDecisionId: string,
  sanctionId: string | null,
  grounds: string,
  evidenceIds: string[],
) => command(`/api/v1/trust/cases/${item.id}/appeals`, {
  original_decision_id: originalDecisionId,
  sanction_id: sanctionId,
  expected_case_version: item.version,
  grounds,
  evidence_ids: evidenceIds,
});

export const decideTrustAppeal = (
  appeal: TrustAppeal,
  caseVersion: number,
  payload: {
    outcome: "AFFIRMED" | "MODIFIED" | "OVERTURNED" | "REMANDED";
    reasoning: string;
    evidence_ids: string[];
  },
) => command(`/api/v1/trust/appeals/${appeal.id}/decision`, {
  expected_case_version: caseVersion,
  outcome: payload.outcome,
  standard_of_proof: "Independent review of verified evidence",
  causal_findings: { independent_review: true },
  reasoning: payload.reasoning,
  consequence_spec: { apply_appeal_outcome: true },
  evidence_ids: payload.evidence_ids,
});

export const finalizeTrustSanction = (item: TrustSanction) =>
  command(`/api/v1/trust/sanctions/${item.id}/finalize`, {
    expected_version: item.version,
  });

export const createRehabilitationPlan = (
  decisionId: string,
  payload: {
    title: string;
    completion_criteria: Record<string, unknown>;
    starts_at: string;
    due_at: string;
    steps: Array<{ description: string; completion_criterion: string }>;
  },
) => command(`/api/v1/trust/decisions/${decisionId}/rehabilitation-plans`, payload);

export const completeRehabilitationStep = (
  plan: RehabilitationPlan,
  stepId: string,
  evidenceIds: string[],
) => command(`/api/v1/trust/rehabilitation-plans/${plan.id}/steps/${stepId}/complete`, {
  expected_plan_version: plan.version,
  evidence_ids: evidenceIds,
});

export const closeRehabilitationPlan = (
  plan: RehabilitationPlan,
  context: string,
  closureReason: string,
) => command(`/api/v1/trust/rehabilitation-plans/${plan.id}/close`, {
  expected_version: plan.version,
  context,
  closure_reason: closureReason,
});
