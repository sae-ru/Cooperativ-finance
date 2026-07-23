import { commandHeaders, request } from "./admin";

export type Fund = {
  id: string;
  cooperative_id: string;
  fund_code: string;
  name: string;
  purpose: string;
  policy_version: number;
  residue_rule: string;
  admin_expense_limit: string;
  terms_hash: string;
  status: string;
  proposed_by_member_id: string;
  approved_by_member_id: string | null;
  created_at: string;
  approved_at: string | null;
  version: number;
};

export type Campaign = {
  id: string;
  cooperative_id: string;
  fund_id: string;
  campaign_code: string;
  title: string;
  public_purpose: string;
  accepted_forms: string[];
  starts_at: string;
  ends_at: string;
  residue_rule: string;
  terms_hash: string;
  status: string;
  created_by_member_id: string;
  opened_by_member_id: string | null;
  closed_by_member_id: string | null;
  created_at: string;
  opened_at: string | null;
  closed_at: string | null;
  version: number;
};

export type Pledge = {
  id: string;
  campaign_id: string;
  donor_member_id: string;
  contribution_form: string;
  unit_code: string;
  quantity: string;
  description: string;
  status: string;
  expires_at: string;
  fulfilled_contribution_id: string | null;
  created_at: string;
  version: number;
};

export type Contribution = {
  id: string;
  campaign_id: string;
  pledge_id: string | null;
  donor_member_id: string;
  contribution_form: string;
  unit_code: string;
  quantity: string;
  description: string;
  status: string;
  received_by_member_id: string;
  verified_by_member_id: string | null;
  verification_note: string | null;
  received_at: string;
  verified_at: string | null;
  version: number;
};

export type AidApplication = {
  id: string;
  campaign_id: string;
  recipient_member_id: string;
  need_category: string;
  requested_form: string;
  requested_unit_code: string;
  requested_quantity: string;
  privacy_scope: string;
  status: string;
  submitted_by_member_id: string;
  reviewed_by_member_id: string | null;
  eligibility_note: string | null;
  submitted_at: string;
  reviewed_at: string | null;
  version: number;
};

export type Allocation = {
  id: string;
  campaign_id: string;
  application_id: string;
  recipient_member_id: string;
  contribution_form: string;
  unit_code: string;
  quantity: string;
  public_summary: string;
  rationale: string;
  policy_terms_hash: string;
  allocation_hash: string;
  status: string;
  proposed_by_member_id: string;
  created_at: string;
  version: number;
};

export type Delivery = {
  id: string;
  allocation_id: string;
  recipient_member_id: string;
  attestor_kind: string;
  attested_by_member_id: string;
  acknowledgement: string;
  delivered_event_id: string;
  delivered_at: string;
};

export type Complaint = {
  id: string;
  campaign_id: string;
  allocation_id: string | null;
  contribution_id: string | null;
  complainant_member_id: string;
  category: string;
  summary: string;
  privacy_scope: string;
  status: string;
  resolved_by_member_id: string | null;
  resolution_action: string | null;
  resolution_note: string | null;
  opened_at: string;
  resolved_at: string | null;
  version: number;
};

export type CampaignReport = {
  id: string;
  campaign_id: string;
  cooperative_id: string;
  bucket_totals: Array<{
    contribution_form: string;
    unit_code: string;
    verified: string;
    delivered: string;
    residue: string;
  }>;
  contribution_count: number;
  allocation_count: number;
  delivery_count: number;
  complaint_count: number;
  residue_rule: string;
  responsibility_snapshot: Array<Record<string, unknown>>;
  report_hash: string;
  generated_at: string;
};

export type BucketBalance = {
  contribution_form: string;
  unit_code: string;
  verified: string;
  reserved_or_delivered: string;
  available: string;
};

export type OperatorWorkspace = {
  campaigns: Campaign[];
  verified_contributions: Contribution[];
  eligible_applications: AidApplication[];
  active_allocations: Allocation[];
};

export type ControllerWorkspace = {
  draft_funds: Fund[];
  draft_campaigns: Campaign[];
  received_contributions: Contribution[];
  submitted_applications: AidApplication[];
  proposed_allocations: Allocation[];
  open_complaints: Complaint[];
};

type CommandResult = { event_id: string; object_id: string; replayed: boolean };

function command<T extends object>(path: string, payload: T) {
  return request<CommandResult>(path, {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify(payload),
  });
}

function query(path: string, campaignId?: string) {
  return campaignId ? `${path}?campaign_id=${encodeURIComponent(campaignId)}` : path;
}

export const getFunds = () => request<Fund[]>("/api/v1/solidarity/funds");
export const getCampaigns = () => request<Campaign[]>("/api/v1/solidarity/campaigns");
export const getPledges = (campaignId?: string) =>
  request<Pledge[]>(query("/api/v1/solidarity/pledges", campaignId));
export const getContributions = (campaignId?: string) =>
  request<Contribution[]>(query("/api/v1/solidarity/contributions", campaignId));
export const getAidApplications = (campaignId?: string) =>
  request<AidApplication[]>(query("/api/v1/solidarity/applications", campaignId));
export const getAllocations = (campaignId?: string) =>
  request<Allocation[]>(query("/api/v1/solidarity/allocations", campaignId));
export const getDeliveries = (campaignId?: string) =>
  request<Delivery[]>(query("/api/v1/solidarity/deliveries", campaignId));
export const getComplaints = (campaignId?: string) =>
  request<Complaint[]>(query("/api/v1/solidarity/complaints", campaignId));
export const getCampaignReports = (campaignId?: string) =>
  request<CampaignReport[]>(query("/api/v1/solidarity/reports", campaignId));
export const getCampaignBalances = (campaignId: string) =>
  request<BucketBalance[]>(`/api/v1/solidarity/campaigns/${campaignId}/balances`);
export const getSolidarityOperatorWorkspace = () =>
  request<OperatorWorkspace>("/api/v1/solidarity/workspaces/operator");
export const getSolidarityControllerWorkspace = () =>
  request<ControllerWorkspace>("/api/v1/solidarity/workspaces/controller");

export const proposeFund = (payload: {
  cooperative_id: string;
  fund_code: string;
  name: string;
  purpose: string;
  residue_rule: string;
  admin_expense_limit: string;
  terms: Record<string, unknown>;
}) => command("/api/v1/solidarity/funds", payload);

export const approveFund = (item: Fund) =>
  command(`/api/v1/solidarity/funds/${item.id}/approval`, {
    expected_version: item.version,
  });

export const createCampaign = (payload: {
  fund_id: string;
  campaign_code: string;
  title: string;
  public_purpose: string;
  eligibility_policy: Record<string, unknown>;
  accepted_forms: string[];
  starts_at: string;
  ends_at: string;
}) => command("/api/v1/solidarity/campaigns", payload);

export const openCampaign = (item: Campaign) =>
  command(`/api/v1/solidarity/campaigns/${item.id}/open`, {
    expected_version: item.version,
  });

export const closeCampaign = (item: Campaign, reconciliationNote: string) =>
  command(`/api/v1/solidarity/campaigns/${item.id}/close`, {
    expected_version: item.version,
    reconciliation_note: reconciliationNote,
  });

export const createPledge = (
  campaignId: string,
  payload: {
    donor_member_id: string;
    contribution_form: string;
    unit_code: string;
    quantity: string;
    description: string;
    expires_at: string;
  },
) => command(`/api/v1/solidarity/campaigns/${campaignId}/pledges`, payload);

export const receiveContribution = (payload: {
  campaign_id: string;
  pledge_id: string | null;
  donor_member_id: string;
  contribution_form: string;
  unit_code: string;
  quantity: string;
  description: string;
  evidence_ids: string[];
}) => command("/api/v1/solidarity/contributions", payload);

export const verifyContribution = (item: Contribution, accepted: boolean, note: string) =>
  command(`/api/v1/solidarity/contributions/${item.id}/verification`, {
    expected_version: item.version,
    accepted,
    verification_note: note,
  });

export const submitAidApplication = (
  campaignId: string,
  payload: {
    recipient_member_id: string;
    need_category: string;
    requested_form: string;
    requested_unit_code: string;
    requested_quantity: string;
    privacy_scope: string;
    evidence_ids: string[];
  },
) => command(`/api/v1/solidarity/campaigns/${campaignId}/applications`, payload);

export const reviewAidApplication = (
  item: AidApplication,
  eligible: boolean,
  note: string,
) =>
  command(`/api/v1/solidarity/applications/${item.id}/review`, {
    expected_version: item.version,
    eligible,
    eligibility_note: note,
  });

export const proposeAllocation = (
  applicationId: string,
  payload: { quantity: string; public_summary: string; rationale: string },
) => command(`/api/v1/solidarity/applications/${applicationId}/allocations`, payload);

export const approveAllocation = (
  item: Allocation,
  approved: boolean,
  conflictStatement: string,
) =>
  command(`/api/v1/solidarity/allocations/${item.id}/approval`, {
    expected_version: item.version,
    allocation_hash: item.allocation_hash,
    approved,
    conflict_statement: conflictStatement,
  });

export const recordDelivery = (
  item: Allocation,
  payload: { attestor_kind: string; acknowledgement: string; evidence_ids: string[] },
) =>
  command(`/api/v1/solidarity/allocations/${item.id}/delivery`, {
    expected_version: item.version,
    ...payload,
  });

export const openComplaint = (
  campaignId: string,
  payload: {
    allocation_id: string | null;
    contribution_id: string | null;
    category: string;
    summary: string;
    privacy_scope: string;
    evidence_ids: string[];
  },
) => command(`/api/v1/solidarity/campaigns/${campaignId}/complaints`, payload);

export const resolveComplaint = (
  item: Complaint,
  payload: { accepted: boolean; resolution_action: string; resolution_note: string },
) =>
  command(`/api/v1/solidarity/complaints/${item.id}/resolution`, {
    expected_version: item.version,
    ...payload,
  });
