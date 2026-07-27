export type RoleCode =
  | "EXCHANGE_PARTICIPANT"
  | "MEMBER_REGISTRAR"
  | "COOPERATIVE_ADMIN"
  | "DATA_STEWARD"
  | "RISK_ADMIN"
  | "SECURITY_ADMIN"
  | "NODE_REGISTRAR"
  | "NODE_TECHNICAL_CUSTODIAN"
  | "NODE_SECURITY_ADMIN"
  | "NODE_BUSINESS_OPERATOR"
  | "NODE_AUDITOR"
  | "AUDITOR"
  | "ARBITRATOR"
  | "WAREHOUSE_CUSTODIAN"
  | "INVENTORY_CONTROLLER"
  | "LOGISTICS_OPERATOR"
  | "RIGHTS_OPERATOR"
  | "CLEARING_OPERATOR"
  | "CLEARING_CONTROLLER"
  | "CLEARING_FINALIZER"
  | "SOLIDARITY_OPERATOR"
  | "SOLIDARITY_CONTROLLER"
  | "CRISIS_OPERATOR"
  | "CRISIS_CONTROLLER";

export type Principal = {
  user_id: string;
  login: string;
  member_id: string | null;
  must_change_password: boolean;
  roles: Array<{
    assignment_id: string;
    role: RoleCode;
    cooperative_id: string | null;
    source?: "ASSIGNMENT" | "BREAK_GLASS";
    expires_at?: string | null;
  }>;
};

export type AuthSession = {
  access_token: string;
  access_expires_at: string;
  refresh_expires_at: string;
  principal: Principal;
};

export type AdminOverview = {
  members: number;
  active_members: number;
  cooperatives: number;
  users: number;
  active_sessions: number;
  pending_role_approvals: number;
};

export type Cooperative = {
  id: string;
  code: string;
  name: string;
  status: "ACTIVE" | "SUSPENDED";
  created_at: string;
  updated_at: string;
  version: number;
};

export type Member = {
  id: string;
  display_name: string;
  registered_by_cooperative_id: string | null;
  merged_into_member_id?: string | null;
  status: string;
  created_at: string;
  updated_at: string;
  version: number;
};

export type MemberDuplicateCandidate = {
  member_id: string;
  display_name: string;
  registered_by_cooperative_id: string | null;
  merged_into_member_id?: string | null;
  status: string;
  match_basis: "EXACT_IDENTIFIER" | "NORMALIZED_NAME";
};

export type MemberDuplicateCheck = {
  candidates: MemberDuplicateCandidate[];
  exact_identifier_match: boolean;
  normalized_name_match: boolean;
};

export type MemberImportBatch = {
  id: string;
  cooperative_id: string;
  source_name: string;
  source_sha256: string;
  status: "STAGED" | "PREVIEWED" | "APPROVED" | "REJECTED" | "APPLIED";
  row_count: number;
  ready_count: number;
  invalid_count: number;
  duplicate_count: number;
  applied_count: number;
  created_by_user_id: string;
  reviewed_by_user_id: string | null;
  decision_reason_code: string | null;
  created_at: string;
  previewed_at: string | null;
  reviewed_at: string | null;
  applied_at: string | null;
  updated_at: string;
  version: number;
};

export type MemberImportRow = {
  id: string;
  batch_id: string;
  row_number: number;
  display_name: string;
  identifier_type: string | null;
  status: "STAGED" | "READY" | "INVALID" | "DUPLICATE" | "APPLIED";
  error_code: string | null;
  match_basis: string | null;
  candidate_member_id: string | null;
  created_member_id: string | null;
  created_at: string;
  applied_at: string | null;
};

export type MemberMergeCaseStatus =
  | "PENDING_REVIEW"
  | "BLOCKED"
  | "APPROVED"
  | "REJECTED"
  | "EXPIRED";

export type MemberMergeCase = {
  id: string;
  cooperative_id: string;
  source_member_id: string;
  survivor_member_id: string;
  source_expected_version: number;
  survivor_expected_version: number;
  evidence_refs: string[];
  reason_code: string;
  blocker_summary: {
    codes?: string[];
    references?: Record<string, number>;
  };
  status: MemberMergeCaseStatus;
  requested_by_user_id: string;
  decided_by_user_id: string | null;
  decision_reason_code: string | null;
  created_at: string;
  expires_at: string;
  decided_at: string | null;
  updated_at: string;
  version: number;
};

export type MemberContinuityCaseType = "VOLUNTARY_EXIT" | "DEATH_OR_INCAPACITY";
export type MemberContinuityCaseStatus =
  | "PENDING_REVIEW"
  | "CONFIRMED"
  | "REJECTED"
  | "BLOCKED";

export type MemberContinuityCase = {
  id: string;
  cooperative_id: string;
  member_id: string;
  case_type: MemberContinuityCaseType;
  previous_member_status: string;
  contained_member_version: number;
  reference_summary: {
    groups?: Record<string, number>;
    total_references?: number;
  };
  review_blockers: string[];
  evidence_refs: string[];
  reason_code: string;
  status: MemberContinuityCaseStatus;
  requested_by_user_id: string;
  decided_by_user_id: string | null;
  decision_reason_code: string | null;
  disabled_user_count: number;
  suspended_membership_count: number;
  created_at: string;
  decided_at: string | null;
  updated_at: string;
  version: number;
};

export type Membership = {
  id: string;
  cooperative_id: string;
  member_id: string;
  member_number: string;
  status: string;
  joined_at: string | null;
  ended_at: string | null;
  created_at: string;
  updated_at: string;
  version: number;
};

export type UserAccount = {
  id: string;
  login: string;
  member_id: string | null;
  status: string;
  must_change_password: boolean;
  locked_until: string | null;
  last_login_at: string | null;
  created_at: string;
  version: number;
};

export type RoleAssignment = {
  id: string;
  user_id: string;
  role_code: RoleCode;
  cooperative_id: string | null;
  status: string;
  granted_by_user_id: string | null;
  approved_by_user_id: string | null;
  created_at: string;
  version: number;
};

export type ServerSession = {
  id: string;
  user_id: string;
  status: string;
  access_expires_at: string;
  refresh_expires_at: string;
  created_at: string;
  last_seen_at: string;
  revoked_at: string | null;
};

export type AuditEntry = {
  id: string;
  occurred_at: string;
  actor_user_id: string | null;
  action: string;
  object_type: string;
  object_id: string | null;
  outcome: string;
  reason_code: string | null;
};

export type ServiceScope = "catalog:read" | "clearing:accounting:read";
export type ServiceClientOperation = "CREATE" | "UPDATE" | "ROTATE" | "REACTIVATE";
export type ServiceClientStatus = "ACTIVE" | "SUSPENDED" | "REVOKED";
export type ServiceClientRequestStatus = "PENDING" | "APPROVED" | "REJECTED";

export type ServiceClient = {
  id: string;
  client_code: string;
  owner_cooperative_id: string;
  display_name: string;
  technical_contact_name: string;
  technical_contact_email: string;
  scopes: ServiceScope[];
  network_allowlist: string[];
  rate_limit_per_minute: number;
  status: ServiceClientStatus;
  effective_status: ServiceClientStatus | "EXPIRED";
  expires_at: string;
  registered_by_user_id: string;
  approved_by_user_id: string;
  created_at: string;
  updated_at: string;
  suspended_at: string | null;
  revoked_at: string | null;
  version: number;
};

export type ServiceClientConfig = {
  display_name: string;
  technical_contact_name: string;
  technical_contact_email: string;
  scopes: ServiceScope[];
  network_allowlist: string[];
  rate_limit_per_minute: number;
  expires_at: string;
};

export type ServiceClientRequest = {
  id: string;
  service_client_id: string | null;
  owner_cooperative_id: string;
  operation: ServiceClientOperation;
  proposed_config: ServiceClientConfig | null;
  expected_client_version: number | null;
  reason_code: string;
  status: ServiceClientRequestStatus;
  requested_by_user_id: string;
  decided_by_user_id: string | null;
  decision_reason_code: string | null;
  issued_credential_id: string | null;
  created_at: string;
  expires_at: string;
  decided_at: string | null;
  version: number;
};

export type ServiceClientDecision = {
  event_id: string;
  object_id: string;
  replayed: boolean;
  service_client_id: string | null;
  client_code: string | null;
  credential_secret: string | null;
  credential_expires_at: string | null;
};

export class AdminApiError extends Error {
  constructor(
    public readonly code: string,
    public readonly requestId: string | null,
    public readonly status: number,
  ) {
    super(code);
    this.name = "AdminApiError";
  }
}

let accessToken: string | null = null;
let refreshPromise: Promise<AuthSession | null> | null = null;

function cookie(name: string): string | null {
  const prefix = `${encodeURIComponent(name)}=`;
  const item = document.cookie.split("; ").find((value) => value.startsWith(prefix));
  return item ? decodeURIComponent(item.slice(prefix.length)) : null;
}

async function parseError(response: Response): Promise<AdminApiError> {
  const value = (await response.json().catch(() => null)) as
    | { error?: { code?: string }; request_id?: string }
    | null;
  return new AdminApiError(
    value?.error?.code ?? `HTTP_${response.status}`,
    value?.request_id ?? response.headers.get("X-Request-ID"),
    response.status,
  );
}

async function performRefresh(): Promise<AuthSession | null> {
  const csrf = cookie("coop_csrf");
  if (!csrf) return null;
  const response = await fetch("/api/v1/auth/refresh", {
    method: "POST",
    credentials: "same-origin",
    headers: { Accept: "application/json", "X-CSRF-Token": csrf }
  });
  if (!response.ok) {
    accessToken = null;
    return null;
  }
  const envelope = (await response.json()) as { data: AuthSession };
  accessToken = envelope.data.access_token;
  return envelope.data;
}

async function refreshAccess(): Promise<AuthSession | null> {
  if (refreshPromise) return refreshPromise;
  refreshPromise = performRefresh();
  try {
    return await refreshPromise;
  } finally {
    refreshPromise = null;
  }
}

async function authorizedFetch(
  path: string,
  init: RequestInit = {},
  retry = true,
): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (typeof init.body === "string" && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  const response = await fetch(path, { ...init, headers, credentials: "same-origin" });
  if (response.status === 401 && retry && (await refreshAccess())) {
    return authorizedFetch(path, init, false);
  }
  if (!response.ok) throw await parseError(response);
  return response;
}

export async function request<T>(
  path: string,
  init: RequestInit = {},
  retry = true,
): Promise<T> {
  const response = await authorizedFetch(path, init, retry);
  if (response.status === 204) return undefined as T;
  const envelope = (await response.json()) as { data: T };
  return envelope.data;
}

export async function requestDirect<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await authorizedFetch(path, init);
  return response.json() as Promise<T>;
}

export async function requestBlob(path: string): Promise<Blob> {
  return (await authorizedFetch(path)).blob();
}
export function commandHeaders(): HeadersInit {
  return { "Idempotency-Key": crypto.randomUUID() };
}

export async function login(loginValue: string, password: string): Promise<AuthSession> {
  const response = await fetch("/api/v1/auth/login", {
    method: "POST",
    credentials: "same-origin",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({ login: loginValue, password })
  });
  if (!response.ok) throw await parseError(response);
  const envelope = (await response.json()) as { data: AuthSession };
  accessToken = envelope.data.access_token;
  return envelope.data;
}

export const restoreSession = refreshAccess;

export async function logout(): Promise<void> {
  try {
    await request<void>("/api/v1/auth/logout", { method: "POST" }, false);
  } finally {
    accessToken = null;
  }
}

export async function changePassword(
  currentPassword: string,
  newPassword: string,
): Promise<AuthSession> {
  const data = await request<AuthSession>("/api/v1/auth/change-password", {
    method: "POST",
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword })
  });
  accessToken = data.access_token;
  return data;
}

export const getOverview = () => request<AdminOverview>("/api/v1/admin/overview");
export const getCooperatives = () =>
  request<Cooperative[]>("/api/v1/admin/cooperatives");
export const getMembers = () => request<Member[]>("/api/v1/admin/members?limit=500");
export const getMemberships = () => request<Membership[]>("/api/v1/admin/memberships");
export const getMemberImports = () =>
  request<MemberImportBatch[]>("/api/v1/admin/imports?limit=500");
export const getMemberMergeCases = () =>
  request<MemberMergeCase[]>("/api/v1/admin/member-merge-cases");
export const getMemberContinuityCases = () =>
  request<MemberContinuityCase[]>("/api/v1/admin/member-continuity-cases");
export const getMemberImportRows = (batchId: string) =>
  request<MemberImportRow[]>(`/api/v1/admin/imports/${batchId}/rows`);
export const getUsers = () => request<UserAccount[]>("/api/v1/admin/users");
export const getRoles = () => request<RoleAssignment[]>("/api/v1/admin/roles");
export const getSessions = () => request<ServerSession[]>("/api/v1/admin/sessions");
export const getAudit = () => request<AuditEntry[]>("/api/v1/admin/audit?limit=200");
export const getServiceClients = () =>
  request<ServiceClient[]>("/api/v1/admin/service-clients");
export const getServiceClientRequests = () =>
  request<ServiceClientRequest[]>("/api/v1/admin/service-client-requests");

export const createCooperative = (payload: { code: string; name: string }) =>
  request<{ event_id: string; object_id: string }>("/api/v1/admin/cooperatives", {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify(payload)
  });

export const transitionCooperative = (cooperative: Cooperative, targetStatus: Cooperative["status"]) =>
  request<{ event_id: string; object_id: string }>(
    `/api/v1/admin/cooperatives/${cooperative.id}/transitions`,
    {
      method: "POST",
      headers: commandHeaders(),
      body: JSON.stringify({
        target_status: targetStatus,
        reason_code: "OPERATOR_CONFIRMED",
        expected_version: cooperative.version
      })
    },
  );
export const checkMemberDuplicates = (payload: {
  cooperative_id: string;
  display_name: string;
  identifier_type?: string;
  identifier_value?: string;
}) =>
  request<MemberDuplicateCheck>("/api/v1/admin/members/duplicate-check", {
    method: "POST",
    body: JSON.stringify(payload)
  });

export const createMember = (payload: {
  cooperative_id: string;
  display_name: string;
  identifier_type?: string;
  identifier_value?: string;
  duplicate_resolution_code?: string;
}) =>
  request<{ event_id: string; object_id: string }>("/api/v1/admin/members", {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify(payload)
  });

export const stageMemberImport = (payload: {
  cooperative_id: string;
  source_name: string;
  csv_text: string;
}) =>
  request<{ event_id: string; object_id: string }>("/api/v1/admin/imports", {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify(payload),
  });

export const previewMemberImport = (batch: MemberImportBatch) =>
  request<{ event_id: string; object_id: string }>(
    `/api/v1/admin/imports/${batch.id}/dry-run`,
    {
      method: "POST",
      headers: commandHeaders(),
      body: JSON.stringify({ expected_version: batch.version }),
    },
  );

export const decideMemberImport = (
  batch: MemberImportBatch,
  approve: boolean,
  reasonCode: string,
) =>
  request<{ event_id: string; object_id: string }>(
    `/api/v1/admin/imports/${batch.id}/decision`,
    {
      method: "POST",
      headers: commandHeaders(),
      body: JSON.stringify({
        approve,
        reason_code: reasonCode,
        expected_version: batch.version,
      }),
    },
  );

export const applyMemberImport = (batch: MemberImportBatch) =>
  request<{ event_id: string; object_id: string }>(
    `/api/v1/admin/imports/${batch.id}/apply`,
    {
      method: "POST",
      headers: commandHeaders(),
      body: JSON.stringify({ expected_version: batch.version }),
    },
  );

export const requestMemberMerge = (payload: {
  cooperative_id: string;
  source_member_id: string;
  survivor_member_id: string;
  source_expected_version: number;
  survivor_expected_version: number;
  evidence_refs: string[];
  reason_code: string;
}) =>
  request<{ event_id: string; object_id: string; status: MemberMergeCaseStatus; replayed: boolean }>(
    "/api/v1/admin/member-merge-cases",
    {
      method: "POST",
      headers: commandHeaders(),
      body: JSON.stringify(payload),
    },
  );

export const decideMemberMerge = (
  mergeCase: MemberMergeCase,
  approve: boolean,
  reasonCode: string,
) =>
  request<{ event_id: string; object_id: string; status: MemberMergeCaseStatus; replayed: boolean }>(
    `/api/v1/admin/member-merge-cases/${mergeCase.id}/decision`,
    {
      method: "POST",
      headers: commandHeaders(),
      body: JSON.stringify({
        approve,
        expected_version: mergeCase.version,
        reason_code: reasonCode,
      }),
    },
  );

export const requestMemberContinuity = (payload: {
  cooperative_id: string;
  member_id: string;
  case_type: MemberContinuityCaseType;
  expected_member_version: number;
  evidence_refs: string[];
  reason_code: string;
}) =>
  request<{
    event_id: string;
    object_id: string;
    status: MemberContinuityCaseStatus;
    replayed: boolean;
  }>("/api/v1/admin/member-continuity-cases", {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify(payload),
  });

export const decideMemberContinuity = (
  continuityCase: MemberContinuityCase,
  approve: boolean,
  reasonCode: string,
) =>
  request<{
    event_id: string;
    object_id: string;
    status: MemberContinuityCaseStatus;
    replayed: boolean;
  }>(`/api/v1/admin/member-continuity-cases/${continuityCase.id}/decision`, {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify({
      approve,
      expected_version: continuityCase.version,
      reason_code: reasonCode,
    }),
  });

export const transitionMember = (member: Member, targetStatus: string) =>
  request<{ event_id: string; object_id: string }>(
    `/api/v1/admin/members/${member.id}/transitions`,
    {
      method: "POST",
      headers: commandHeaders(),
      body: JSON.stringify({
        target_status: targetStatus,
        reason_code: "OPERATOR_CONFIRMED",
        expected_version: member.version
      })
    },
  );

export const createMembership = (payload: {
  cooperative_id: string;
  member_id: string;
  member_number: string;
}) =>
  request<{ event_id: string; object_id: string }>("/api/v1/admin/memberships", {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify(payload)
  });

export const transitionMembership = (membership: Membership, targetStatus: string) =>
  request<{ event_id: string; object_id: string }>(
    `/api/v1/admin/memberships/${membership.id}/transitions`,
    {
      method: "POST",
      headers: commandHeaders(),
      body: JSON.stringify({
        target_status: targetStatus,
        reason_code: "OPERATOR_CONFIRMED",
        expected_version: membership.version
      })
    },
  );
export const createUser = (payload: {
  login: string;
  temporary_password: string;
  member_id: string | null;
}) =>
  request<{ event_id: string; object_id: string }>("/api/v1/admin/users", {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify(payload)
  });

export const transitionUser = (user: UserAccount, targetStatus: "ACTIVE" | "DISABLED") =>
  request<{ event_id: string; object_id: string }>(
    `/api/v1/admin/users/${user.id}/transitions`,
    {
      method: "POST",
      headers: commandHeaders(),
      body: JSON.stringify({
        target_status: targetStatus,
        reason_code: "SECURITY_ADMIN_CONFIRMED",
        expected_version: user.version
      })
    },
  );
export const requestServiceClientChange = (payload: {
  owner_cooperative_id: string;
  operation: ServiceClientOperation;
  service_client_id?: string | null;
  config?: ServiceClientConfig | null;
  expected_client_version?: number | null;
  reason_code: string;
}) =>
  request<{ event_id: string; object_id: string; replayed: boolean }>(
    "/api/v1/admin/service-client-requests",
    {
      method: "POST",
      headers: commandHeaders(),
      body: JSON.stringify(payload),
    },
  );

export const decideServiceClientRequest = (
  changeRequest: ServiceClientRequest,
  approve: boolean,
  reasonCode: string,
) =>
  request<ServiceClientDecision>(
    `/api/v1/admin/service-client-requests/${changeRequest.id}/decision`,
    {
      method: "POST",
      headers: commandHeaders(),
      body: JSON.stringify({
        approve,
        reason_code: reasonCode,
        expected_version: changeRequest.version,
      }),
    },
  );

export const suspendServiceClient = (serviceClient: ServiceClient, reasonCode: string) =>
  request<{ event_id: string; object_id: string; replayed: boolean }>(
    `/api/v1/admin/service-clients/${serviceClient.id}/suspend`,
    {
      method: "POST",
      headers: commandHeaders(),
      body: JSON.stringify({
        reason_code: reasonCode,
        expected_version: serviceClient.version,
      }),
    },
  );

export const revokeServiceClient = (serviceClient: ServiceClient, reasonCode: string) =>
  request<{ event_id: string; object_id: string; replayed: boolean }>(
    `/api/v1/admin/service-clients/${serviceClient.id}/revoke`,
    {
      method: "POST",
      headers: commandHeaders(),
      body: JSON.stringify({
        reason_code: reasonCode,
        expected_version: serviceClient.version,
      }),
    },
  );

export const assignRole = (payload: {
  user_id: string;
  role: RoleCode;
  cooperative_id: string | null;
}) =>
  request<{ event_id: string; object_id: string }>("/api/v1/admin/roles", {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify(payload)
  });

export const decideRole = (assignmentId: string, approve: boolean) =>
  request<{ event_id: string; object_id: string }>(
    `/api/v1/admin/roles/${assignmentId}/decision`,
    {
      method: "POST",
      headers: commandHeaders(),
      body: JSON.stringify({ approve, reason_code: "INDEPENDENT_REVIEW" })
    },
  );

export const revokeSession = (sessionId: string) =>
  request<{ event_id: string; object_id: string }>(
    `/api/v1/admin/sessions/${sessionId}/revoke`,
    {
      method: "POST",
      headers: commandHeaders(),
      body: JSON.stringify({ reason_code: "SECURITY_REVOKED" })
    },
  );

export type SecurityState = {
  totp_enabled: boolean;
  totp_confirmed_at: string | null;
  enrollment_pending: boolean;
  enrollment_expires_at: string | null;
  step_up_active: boolean;
  step_up_method: string | null;
  step_up_expires_at: string | null;
  break_glass_grants: number;
};

export type TotpEnrollment = {
  factor_id: string;
  secret: string;
  provisioning_uri: string;
  expires_at: string;
};

export type StepUpGrant = {
  method: "TOTP";
  verified_at: string;
  expires_at: string;
};

export type AccountRecovery = {
  id: string;
  target_user_id: string;
  requested_by_user_id: string;
  decided_by_user_id: string | null;
  reason_code: string;
  evidence_id: string;
  status: string;
  created_at: string;
  expires_at: string;
  decided_at: string | null;
  version: number;
};

export type BreakGlassGrant = {
  id: string;
  target_user_id: string;
  role_code: RoleCode;
  cooperative_id: string | null;
  requested_by_user_id: string;
  approved_by_user_id: string | null;
  revoked_by_user_id: string | null;
  reason_code: string;
  evidence_id: string;
  requested_duration_minutes: number;
  status: string;
  created_at: string;
  approved_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  version: number;
};

export const getSecurityState = () => request<SecurityState>("/api/v1/auth/security");

export const beginTotpEnrollment = (currentPassword: string, currentTotpCode?: string) =>
  request<TotpEnrollment>("/api/v1/auth/totp/enrollment", {
    method: "POST",
    body: JSON.stringify({
      current_password: currentPassword,
      current_totp_code: currentTotpCode || null,
    }),
  });

export const confirmTotpEnrollment = (code: string) =>
  request<StepUpGrant>("/api/v1/auth/totp/enrollment/confirm", {
    method: "POST",
    body: JSON.stringify({ code }),
  });

export const verifyTotpStepUp = (code: string) =>
  request<StepUpGrant>("/api/v1/auth/step-up/totp", {
    method: "POST",
    body: JSON.stringify({ code }),
  });

export const disableTotp = (currentPassword: string, code: string, reasonCode: string) =>
  request<{ event_id: string; object_id: string }>("/api/v1/auth/totp", {
    method: "DELETE",
    body: JSON.stringify({
      current_password: currentPassword,
      code,
      reason_code: reasonCode,
    }),
  });

export const getAccountRecoveries = () =>
  request<AccountRecovery[]>("/api/v1/admin/account-recoveries");

export const requestAccountRecovery = (payload: {
  target_user_id: string;
  temporary_password: string;
  reason_code: string;
  evidence_id: string;
}) =>
  request<{ event_id: string; object_id: string }>("/api/v1/admin/account-recoveries", {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify(payload),
  });

export const decideAccountRecovery = (id: string, approve: boolean, reasonCode: string) =>
  request<{ event_id: string; object_id: string }>(
    `/api/v1/admin/account-recoveries/${id}/decision`,
    {
      method: "POST",
      headers: commandHeaders(),
      body: JSON.stringify({ approve, reason_code: reasonCode }),
    },
  );

export const getBreakGlassGrants = () =>
  request<BreakGlassGrant[]>("/api/v1/admin/break-glass");

export const requestBreakGlass = (payload: {
  target_user_id: string;
  role: RoleCode;
  cooperative_id: string | null;
  duration_minutes: number;
  reason_code: string;
  evidence_id: string;
}) =>
  request<{ event_id: string; object_id: string }>("/api/v1/admin/break-glass", {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify(payload),
  });

export const decideBreakGlass = (id: string, approve: boolean, reasonCode: string) =>
  request<{ event_id: string; object_id: string }>(
    `/api/v1/admin/break-glass/${id}/decision`,
    {
      method: "POST",
      headers: commandHeaders(),
      body: JSON.stringify({ approve, reason_code: reasonCode }),
    },
  );

export const revokeBreakGlass = (id: string, reasonCode: string) =>
  request<{ event_id: string; object_id: string }>(
    `/api/v1/admin/break-glass/${id}/revoke`,
    {
      method: "POST",
      headers: commandHeaders(),
      body: JSON.stringify({ reason_code: reasonCode }),
    },
  );