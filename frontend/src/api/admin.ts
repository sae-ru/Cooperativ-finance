export type RoleCode =
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
  roles: Array<{ assignment_id: string; role: RoleCode; cooperative_id: string | null }>;
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
  version: number;
};

export type Member = {
  id: string;
  display_name: string;
  status: string;
  created_at: string;
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

async function refreshAccess(): Promise<AuthSession | null> {
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
export const getUsers = () => request<UserAccount[]>("/api/v1/admin/users");
export const getRoles = () => request<RoleAssignment[]>("/api/v1/admin/roles");
export const getSessions = () => request<ServerSession[]>("/api/v1/admin/sessions");
export const getAudit = () => request<AuditEntry[]>("/api/v1/admin/audit?limit=200");

export const createMember = (payload: {
  display_name: string;
  identifier_type?: string;
  identifier_value?: string;
}) =>
  request<{ event_id: string; object_id: string }>("/api/v1/admin/members", {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify(payload)
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
