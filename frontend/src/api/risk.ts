import type { components } from "./schema";
import { commandHeaders, request } from "./admin";

export type RiskPolicy = components["schemas"]["PolicyResponse"];
export type ShareAccount = components["schemas"]["AccountResponse"];
export type ShareContribution = components["schemas"]["ContributionResponse"];
export type RelatedLink = components["schemas"]["RelatedLinkResponse"];
export type ExposureCommitment = components["schemas"]["CommitmentResponse"];
export type LiabilityCase = components["schemas"]["LiabilityResponse"];
export type CompensationTransfer = components["schemas"]["CompensationResponse"];
export type ExposurePreview = components["schemas"]["ExposurePreviewResponse"];
export type ShareContour = components["schemas"]["ShareContour"];
export type CommitmentType = components["schemas"]["CommitmentType"];
export type FaultClass = components["schemas"]["FaultClass"];

type CommandResult = { event_id: string; object_id: string; replayed: boolean };

export const getRiskPolicies = () => request<RiskPolicy[]>("/api/v1/risk/policies");
export const getShareAccounts = () => request<ShareAccount[]>("/api/v1/risk/accounts");
export const getShareContributions = (accountId: string) =>
  request<ShareContribution[]>(`/api/v1/risk/accounts/${accountId}/contributions`);
export const getRelatedLinks = () =>
  request<RelatedLink[]>("/api/v1/risk/related-links");
export const getExposureCommitments = () =>
  request<ExposureCommitment[]>("/api/v1/risk/commitments");
export const getLiabilityCases = () =>
  request<LiabilityCase[]>("/api/v1/risk/liability-cases");
export const getCompensations = () =>
  request<CompensationTransfer[]>("/api/v1/risk/compensations");

export const previewExposure = (payload: {
  account_id: string;
  policy_id: string;
  commitment_type: CommitmentType;
  amount_reserved: string;
  max_loss: string;
}) => request<ExposurePreview>("/api/v1/risk/exposure-previews", {
  method: "POST",
  body: JSON.stringify(payload),
});

export const proposeRiskPolicy = (payload: {
  cooperative_id: string;
  denomination: string;
  max_member_exposure: string;
  max_related_exposure: string;
  max_guarantee_chain_depth: number;
  protected_amount_rule: string;
  related_party_rule: string;
  approval_reference: string;
  evidence_ids: string[];
}) => request<CommandResult>("/api/v1/risk/policies", {
  method: "POST",
  headers: commandHeaders(),
  body: JSON.stringify(payload),
});

export const approveRiskPolicy = (policy: RiskPolicy, evidenceIds: string[]) =>
  request<CommandResult>(`/api/v1/risk/policies/${policy.id}/approval`, {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify({
      terms_hash: policy.terms_hash,
      expected_version: policy.version,
      evidence_ids: evidenceIds,
    }),
  });

export const openShareAccount = (payload: {
  policy_id: string;
  member_id: string;
  contour: ShareContour;
  opening_balance: string;
  protected_amount: string;
  source_reference: string;
  evidence_ids: string[];
}) => request<CommandResult>("/api/v1/risk/accounts", {
  method: "POST",
  headers: commandHeaders(),
  body: JSON.stringify(payload),
});

export const addShareContribution = (
  account: ShareAccount,
  payload: { amount: string; source_reference: string; evidence_ids: string[] },
) => request<CommandResult>(`/api/v1/risk/accounts/${account.id}/contributions`, {
  method: "POST",
  headers: commandHeaders(),
  body: JSON.stringify({ ...payload, expected_version: account.version }),
});

export const proposeRelatedLink = (payload: {
  cooperative_id: string;
  member_a_id: string;
  member_b_id: string;
  relation_type: "HOUSEHOLD" | "CONTROL" | "RELATED";
  source_statement: string;
  evidence_ids: string[];
}) => request<CommandResult>("/api/v1/risk/related-links", {
  method: "POST",
  headers: commandHeaders(),
  body: JSON.stringify(payload),
});

export const decideRelatedLink = (
  link: RelatedLink,
  approve: boolean,
  decisionNotes: string,
  evidenceIds: string[],
) => request<CommandResult>(`/api/v1/risk/related-links/${link.id}/decision`, {
  method: "POST",
  headers: commandHeaders(),
  body: JSON.stringify({
    approve,
    decision_notes: decisionNotes,
    evidence_ids: evidenceIds,
    expected_version: link.version,
  }),
});

export type ExposureProposal = {
  account_id: string;
  policy_id: string;
  commitment_type: CommitmentType;
  risk_type: string;
  risk_id: string;
  debtor_member_id: string | null;
  beneficiary_member_id: string | null;
  role_assignment_id: string | null;
  amount_reserved: string;
  max_loss: string;
  coverage_ratio: string;
  starts_at: string;
  expires_at: string;
  release_condition: string;
  trigger_conditions: string;
  exclusions: string;
};

export const proposeExposure = (payload: ExposureProposal) =>
  request<CommandResult>("/api/v1/risk/commitments", {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify(payload),
  });

export const acceptExposure = (commitment: ExposureCommitment) =>
  request<CommandResult>(`/api/v1/risk/commitments/${commitment.id}/acceptance`, {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify({
      terms_hash: commitment.terms_hash,
      expected_version: commitment.version,
    }),
  });

export const releaseExposure = (
  commitment: ExposureCommitment,
  reason: string,
  evidenceIds: string[],
) => request<CommandResult>(`/api/v1/risk/commitments/${commitment.id}/release`, {
  method: "POST",
  headers: commandHeaders(),
  body: JSON.stringify({
    reason,
    evidence_ids: evidenceIds,
    expected_version: commitment.version,
  }),
});

export const openLiabilityCase = (payload: {
  commitment_id: string;
  incident_reference: string;
  affected_amount: string;
  facts: string;
  causal_graph: Record<string, unknown>;
  evidence_ids: string[];
}) => request<CommandResult>("/api/v1/risk/liability-cases", {
  method: "POST",
  headers: commandHeaders(),
  body: JSON.stringify(payload),
});

export const assessLiabilityCase = (
  liabilityCase: LiabilityCase,
  payload: {
    fault_class: FaultClass;
    assessed_loss: string;
    rationale: string;
    appeal_until: string;
    evidence_ids: string[];
  },
) => request<CommandResult>(
  `/api/v1/risk/liability-cases/${liabilityCase.id}/assessment`,
  {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify({ ...payload, expected_version: liabilityCase.version }),
  },
);

export const authorizeCompensation = (
  liabilityCase: LiabilityCase,
  payload: {
    trust_case_id: string;
    trust_decision_id: string;
    destination_account_id: string;
    amount: string;
    rationale: string;
    evidence_ids: string[];
    expected_source_account_version: number;
    expected_destination_account_version: number;
    expected_commitment_version: number;
  },
) => request<CommandResult>(
  `/api/v1/risk/liability-cases/${liabilityCase.id}/compensations`,
  {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify({
      ...payload,
      expected_liability_version: liabilityCase.version,
    }),
  },
);

export const acceptCompensation = (transfer: CompensationTransfer) =>
  request<CommandResult>(`/api/v1/risk/compensations/${transfer.id}/acceptance`, {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify({ expected_version: transfer.version }),
  });

export const voidCompensation = (
  transfer: CompensationTransfer,
  reason: string,
  evidenceIds: string[],
) => request<CommandResult>(`/api/v1/risk/compensations/${transfer.id}/void`, {
  method: "POST",
  headers: commandHeaders(),
  body: JSON.stringify({
    reason,
    evidence_ids: evidenceIds,
    expected_version: transfer.version,
  }),
});
