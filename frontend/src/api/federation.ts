import { commandHeaders, request, requestBlob } from "./admin";

export type FederationNode = {
  id: string;
  node_code: string;
  display_name: string;
  owner_organization_id: string;
  territory: string;
  purpose: string;
  status: string;
  trust_level: string;
  capabilities: string[];
  supported_protocols: string[];
  supported_policies: Record<string, number>;
  last_sync_at: string | null;
  last_checkpoint_hash: string | null;
  created_at: string;
  updated_at: string;
  version: number;
};

export type NodeApplication = {
  id: string;
  node_id: string;
  status: string;
  requested_capabilities: string[];
  requested_limits: Record<string, unknown>;
  requested_data_scopes: Record<string, unknown>;
  evidence_ids: string[];
  created_by_user_id: string;
  identity_verified_by_user_id: string | null;
  audit_decided_by_user_id: string | null;
  created_at: string;
  submitted_at: string | null;
  identity_verified_at: string | null;
  audit_decided_at: string | null;
  version: number;
};

export type NodeResponsibility = {
  id: string;
  node_id: string;
  application_id: string;
  member_id: string;
  role_assignment_id: string;
  role_code: string;
  capability_scope: string[];
  responsibility_scope: string;
  max_exposure: string;
  exposure_unit: string;
  valid_from: string;
  valid_until: string | null;
  status: string;
  accepted_by_user_id: string | null;
  accepted_at: string | null;
};

export type NodeChallenge = {
  id: string;
  application_id: string;
  certificate_id: string;
  protocol_version: string;
  status: string;
  response_payload: Record<string, unknown> | null;
  issued_by_user_id: string;
  issued_at: string;
  expires_at: string;
  responded_at: string | null;
};

export type NodeTrustContract = {
  id: string;
  node_id: string;
  application_id: string;
  contract_number: string;
  trust_level: string;
  capabilities: string[];
  event_types: string[];
  federation_limits: Record<string, unknown>;
  max_offline_hours: number;
  required_protocols: string[];
  required_policies: Record<string, number>;
  liability_terms: Record<string, unknown>;
  terms_hash: string;
  status: string;
  valid_from: string;
  valid_until: string;
  created_at: string;
  version: number;
};

export type NodeLimit = {
  id: string;
  node_id: string;
  capability: string;
  unit: string;
  max_package_value: string;
  max_unsettled_obligations: string;
  max_external_rights: string;
  max_clearing_position: string;
  max_offline_hours: number;
  required_confirmations: number;
  terms_hash: string;
  status: string;
  version: number;
};

export type NodeBond = {
  id: string;
  node_id: string;
  reference: string;
  amount: string;
  protected_amount: string;
  maximum_loss: string;
  unit: string;
  capability_scope: string[];
  evidence_ids: string[];
  status: string;
  valid_from: string;
  valid_until: string;
  created_at: string;
};

export type NodeExposure = {
  id: string;
  node_id: string;
  capability: string;
  unit: string;
  current_amount: string;
  reserved_amount: string;
  updated_at: string;
  version: number;
};

export type OfflineEpoch = {
  id: string;
  external_node_id: string | null;
  base_checkpoint_hash: string | null;
  allowed_event_types: string[];
  limits: Record<string, unknown>;
  protocol_version: string;
  policy_versions: Record<string, number>;
  policy_hash: string;
  status: string;
  starts_at: string;
  expires_at: string | null;
  created_at: string;
  closed_at: string | null;
  version: number;
};

export type SyncPackage = {
  id: string;
  peer_node_id: string;
  epoch_id: string | null;
  direction: string;
  status: string;
  source_node_code: string;
  target_node_code: string;
  protocol_version: string;
  sequence_first: number;
  sequence_last: number;
  event_count: number;
  blob_count: number;
  archive_size: number;
  archive_hash: string;
  manifest_hash: string;
  simulation_summary: Record<string, unknown> | null;
  rejection_code: string | null;
  created_at: string;
  expires_at: string;
  verified_at: string | null;
  simulated_at: string | null;
  applied_at: string | null;
  version: number;
};

export type SyncConflict = {
  id: string;
  package_id: string;
  inbox_event_id: string | null;
  conflict_class: string;
  affected_object_type: string;
  affected_object_id: string | null;
  local_event_hash: string | null;
  remote_event_hash: string | null;
  status: string;
  decision: string | null;
  rationale: string | null;
  created_at: string;
  decided_at: string | null;
  version: number;
};

export type SyncReceipt = {
  id: string;
  package_id: string;
  receipt_payload: Record<string, unknown>;
  receipt_hash: string;
  signature_base64: string;
  created_at: string;
};

export type NodeIncident = {
  id: string;
  node_id: string;
  incident_type: string;
  severity: string;
  status: string;
  description: string;
  evidence_ids: string[];
  corrective_actions: unknown[];
  created_at: string;
  resolved_at: string | null;
  version: number;
};

export type FederationPaperForm = {
  id: string;
  external_node_id: string;
  epoch_id: string;
  serial_number: string;
  qr_reference: string;
  checksum: string;
  form_type: string;
  form_version: number;
  participant_refs: string[];
  operation_constraints: Record<string, unknown>;
  status: string;
  issued_at: string;
  expires_at: string;
  payload: Record<string, unknown> | null;
  payload_hash: string | null;
  signatures: unknown[] | null;
  evidence_ids: string[] | null;
  issued_by_user_id: string;
  issued_by_member_id: string;
  recorded_by_user_id: string | null;
  recorded_by_member_id: string | null;
  recorded_at: string | null;
  voided_by_user_id: string | null;
  voided_by_member_id: string | null;
  voided_at: string | null;
  void_reason: string | null;
  version: number;
};

export type NodeKeyRotation = {
  id: string;
  node_id: string;
  old_certificate_id: string;
  new_certificate_id: string;
  reason: string;
  status: string;
  requested_by_user_id: string;
  decided_by_user_id: string | null;
  continuity_verified: boolean;
  created_at: string;
  decided_at: string | null;
  version: number;
};

export type CommandResult = { event_id: string; object_id: string; replayed: boolean };
export type ExportResult = CommandResult & { archive_hash: string; download_path: string };

function command(path: string, payload: Record<string, unknown> = {}) {
  return request<CommandResult>(path, {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify(payload)
  });
}

export const getFederationNodes = () => request<FederationNode[]>("/api/v1/federation/nodes");
export const getNodeApplications = () => request<NodeApplication[]>("/api/v1/federation/nodes/applications");
export const getNodeResponsibilities = () => request<NodeResponsibility[]>("/api/v1/federation/responsibilities");
export const getNodeChallenges = () => request<NodeChallenge[]>("/api/v1/federation/challenges");
export const getNodeContracts = () => request<NodeTrustContract[]>("/api/v1/federation/trust-contracts");
export const getNodeLimits = () => request<NodeLimit[]>("/api/v1/federation/bilateral-limits");
export const getNodeBonds = () => request<NodeBond[]>("/api/v1/federation/bonds");
export const getNodeExposures = () => request<NodeExposure[]>("/api/v1/federation/exposures");
export const getOfflineEpochs = () => request<OfflineEpoch[]>("/api/v1/federation/offline-epochs");
export const getSyncPackages = () => request<SyncPackage[]>("/api/v1/federation/sync/packages");
export const getSyncConflicts = () => request<SyncConflict[]>("/api/v1/federation/sync/conflicts");
export const getSyncReceipts = () => request<SyncReceipt[]>("/api/v1/federation/sync/receipts");
export const getNodeIncidents = () => request<NodeIncident[]>("/api/v1/federation/incidents");
export const getFederationPaperForms = () =>
  request<FederationPaperForm[]>("/api/v1/federation/paper-forms");
export const getNodeKeyRotations = () =>
  request<NodeKeyRotation[]>("/api/v1/federation/key-rotations");

export const createNodeApplication = (payload: Record<string, unknown>) =>
  command("/api/v1/federation/nodes/applications", payload);
export const acceptNodeResponsibility = (item: NodeResponsibility) =>
  command(`/api/v1/federation/nodes/applications/${item.application_id}/responsibilities/${item.id}/accept`);
export const submitNodeApplication = (item: NodeApplication) =>
  command(`/api/v1/federation/nodes/applications/${item.id}/submit`, { expected_version: item.version });
export const verifyNodeIdentity = (item: NodeApplication, verificationSummary: string) =>
  command(`/api/v1/federation/nodes/applications/${item.id}/identity-verification`, {
    expected_version: item.version,
    verification_summary: verificationSummary
  });
export const issueNodeChallenge = (item: NodeApplication) =>
  request<CommandResult & { nonce: string }>(
    `/api/v1/federation/nodes/applications/${item.id}/challenge`,
    {
      method: "POST",
      headers: commandHeaders(),
      body: JSON.stringify({ expected_version: item.version, protocol_version: "1.0" })
    }
  );
export const recordNodeChallengeResponse = (
  challengeId: string,
  nonce: string,
  signatureBase64: string,
  responsePayload: Record<string, unknown>
) => command(`/api/v1/federation/challenges/${challengeId}/response`, {
  nonce,
  signature_base64: signatureBase64,
  response_payload: responsePayload
});
export const decideNodeAudit = (item: NodeApplication, approve: boolean, rationale: string) =>
  command(`/api/v1/federation/nodes/applications/${item.id}/audit-decision`, {
    expected_version: item.version,
    approve,
    rationale
  });
export const proposeNodeContract = (payload: Record<string, unknown>) =>
  command("/api/v1/federation/trust-contracts", payload);
export const approveNodeContract = (item: NodeTrustContract) =>
  command(`/api/v1/federation/trust-contracts/${item.id}/approval`, {
    expected_version: item.version,
    terms_hash: item.terms_hash
  });
export const proposeNodeLimit = (nodeId: string, payload: Record<string, unknown>) =>
  command(`/api/v1/federation/nodes/${nodeId}/bilateral-limits`, payload);
export const approveNodeLimit = (item: NodeLimit) =>
  command(`/api/v1/federation/bilateral-limits/${item.id}/approval`, {
    expected_version: item.version,
    terms_hash: item.terms_hash
  });
export const registerNodeBond = (nodeId: string, payload: Record<string, unknown>) =>
  command(`/api/v1/federation/nodes/${nodeId}/bonds`, payload);
export const activateFederationNode = (item: FederationNode) =>
  command(`/api/v1/federation/nodes/${item.id}/activate`, { expected_version: item.version });
export const changeFederationNodeStatus = (
  item: FederationNode,
  action: "suspend" | "quarantine" | "revoke",
  rationale: string
) => command(`/api/v1/federation/nodes/${item.id}/${action}`, {
  expected_version: item.version,
  rationale
});
export const openNodeIncident = (nodeId: string, payload: Record<string, unknown>) =>
  command(`/api/v1/federation/nodes/${nodeId}/incidents`, payload);
export const resolveNodeIncident = (item: NodeIncident, rationale: string) =>
  command(`/api/v1/federation/incidents/${item.id}/resolution`, {
    expected_version: item.version,
    corrective_actions: [{ action: "Integrity and custody controls independently rechecked." }],
    rationale
  });
export const openOfflineEpoch = (nodeId: string, payload: Record<string, unknown>) =>
  command(`/api/v1/federation/nodes/${nodeId}/offline-epochs`, payload);
export const closeOfflineEpoch = (item: OfflineEpoch) =>
  command(`/api/v1/federation/offline-epochs/${item.id}/close`, {
    expected_version: item.version,
    reconciliation: { packages_reconciled: true, physical_records_checked: true }
  });
export const issueFederationPaperForm = (epochId: string, payload: Record<string, unknown>) =>
  command(`/api/v1/federation/offline-epochs/${epochId}/paper-forms`, payload);
export const recordFederationPaperForm = (
  item: FederationPaperForm,
  payload: Record<string, unknown>
) => command(`/api/v1/federation/paper-forms/${item.id}/record`, {
  expected_version: item.version,
  checksum: item.checksum,
  ...payload
});
export const voidFederationPaperForm = (item: FederationPaperForm, rationale: string) =>
  command(`/api/v1/federation/paper-forms/${item.id}/void`, {
    expected_version: item.version,
    rationale
  });
export const requestNodeKeyRotation = (nodeId: string, payload: Record<string, unknown>) =>
  command(`/api/v1/federation/nodes/${nodeId}/key-rotations`, payload);
export const decideNodeKeyRotation = (item: NodeKeyRotation, approve: boolean) =>
  command(`/api/v1/federation/key-rotations/${item.id}/decision`, {
    expected_version: item.version,
    approve
  });
export const reserveNodeExposure = (nodeId: string, payload: Record<string, unknown>) =>
  command(`/api/v1/federation/nodes/${nodeId}/exposure-reservations`, payload);
export const exportSyncPackage = (payload: Record<string, unknown>) =>
  request<ExportResult>("/api/v1/federation/sync/packages/export", {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify(payload)
  });
export const importSyncPackage = (archive: File) => request<CommandResult>(
  "/api/v1/federation/sync/packages/import",
  {
    method: "POST",
    headers: { ...commandHeaders(), "Content-Type": "application/zip" },
    body: archive
  }
);
export const applySyncPackage = (item: SyncPackage) =>
  command(`/api/v1/federation/sync/packages/${item.id}/apply`, {
    expected_version: item.version,
    manifest_hash: item.manifest_hash
  });
export const resolveSyncConflict = (
  item: SyncConflict,
  decision: "ACCEPT_REMOTE" | "KEEP_LOCAL" | "REJECT_PACKAGE",
  rationale: string
) => command(`/api/v1/federation/sync/conflicts/${item.id}/resolution`, {
  expected_version: item.version,
  decision,
  rationale,
  evidence_ids: []
});
export const downloadSyncArchive = (packageId: string) =>
  requestBlob(`/api/v1/federation/sync/packages/${packageId}/archive`);
