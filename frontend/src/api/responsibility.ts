import { commandHeaders, request, type RoleCode } from "./admin";

export type ResponsibilityCandidate = {
  role_assignment_id: string;
  user_id: string;
  member_id: string;
  display_name: string;
  role_code: RoleCode;
};

export type ResponsibilityAssignment = {
  id: string;
  cooperative_id: string;
  member_id: string;
  role_assignment_id: string;
  subject_type: string;
  subject_id: string;
  scope: string;
  max_exposure: string;
  exposure_unit: string;
  valid_from: string;
  valid_until: string | null;
  status: string;
  created_by_user_id: string;
  approved_by_user_id: string | null;
  accepted_by_user_id: string | null;
  created_event_id: string;
  approved_event_id: string | null;
  accepted_event_id: string | null;
  created_at: string;
  approved_at: string | null;
  accepted_at: string | null;
  version: number;
};

export type ResponsibilityProposal = {
  cooperative_id: string;
  member_id: string;
  role_assignment_id: string;
  subject_type: string;
  subject_id: string;
  scope: string;
  max_exposure: string;
  exposure_unit: string;
  valid_until: string | null;
  expected_summary_hash?: string;
};

export type CanonicalPreview = {
  canonicalization_profile: string;
  canonical_json: string;
  summary_hash: string;
};

export type EventSignature = {
  key_id: string;
  key_fingerprint: string;
  algorithm: string;
  scope: string;
  signature_base64: string;
  signed_at: string;
};

export type SignedEvent = {
  event_id: string;
  event_type: string;
  node_id: string;
  local_sequence: number;
  aggregate_type: string;
  aggregate_id: string;
  aggregate_version: number;
  occurred_at: string;
  recorded_at: string;
  previous_event_hash: string | null;
  payload_hash: string;
  event_hash: string;
  canonicalization_profile: string;
  canonical_json: string;
  envelope: Record<string, unknown>;
  signatures: EventSignature[];
};

export type IntegrityReport = {
  ok: boolean;
  node_id: string;
  checked_events: number;
  last_sequence: number;
  last_event_hash: string | null;
  failures: Array<{ sequence: number; event_id: string; code: string }>;
};

export type OutboxStatus = {
  pending: number;
  processing: number;
  published: number;
  quarantined: number;
  oldest_pending_at: string | null;
};

type CommandResult = { event_id: string; object_id: string; replayed: boolean };

export const getResponsibilityCandidates = (cooperativeId: string) =>
  request<ResponsibilityCandidate[]>(
    `/api/v1/responsibility/candidates?cooperative_id=${encodeURIComponent(cooperativeId)}`,
  );

export const getResponsibilityAssignments = () =>
  request<ResponsibilityAssignment[]>("/api/v1/responsibility/assignments?limit=500");

export const previewResponsibility = (payload: ResponsibilityProposal) =>
  request<CanonicalPreview>("/api/v1/responsibility/preview", {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const proposeResponsibility = (payload: ResponsibilityProposal) =>
  request<CommandResult>("/api/v1/responsibility/assignments", {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify(payload),
  });

export const decideResponsibility = (assignmentId: string, approve: boolean) =>
  request<CommandResult>(`/api/v1/responsibility/assignments/${assignmentId}/decision`, {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify({
      decision: approve ? "APPROVE" : "REJECT",
      reason_code: "INDEPENDENT_REVIEW",
    }),
  });

export const acceptResponsibility = (assignment: ResponsibilityAssignment) =>
  request<CommandResult>(`/api/v1/responsibility/assignments/${assignment.id}/accept`, {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify({ expected_version: assignment.version }),
  });

export const getSignedEvents = () =>
  request<SignedEvent[]>("/api/v1/journal/events?limit=200");

export const getJournalIntegrity = () =>
  request<IntegrityReport>("/api/v1/journal/integrity");

export const getOutboxStatus = () => request<OutboxStatus>("/api/v1/journal/outbox");
