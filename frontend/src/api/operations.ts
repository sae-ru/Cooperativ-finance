import { request, requestBlob } from "./admin";

export type OperationalSnapshot = {
  generated_at: string;
  schema_revision: string;
  signed_events: number;
  outbox_pending: number;
  outbox_quarantined: number;
  active_sessions: number;
  open_trust_cases: number;
  submitted_appeals: number;
  open_sync_conflicts: number;
  open_node_incidents: number;
  pending_key_rotations: number;
  open_offline_epochs: number;
  issued_federation_forms: number;
  active_federated_prepares: number;
  pending_federated_applies: number;
  expired_federated_prepares: number;
  active_crisis_mandates: number;
  issued_crisis_forms: number;
};

export type HostCheck = {
  name: "storage" | "clock" | "backup" | "certificates" | "ups";
  status: "OK" | "WARNING" | "CRITICAL" | "UNKNOWN";
  code: string;
  observed_at: string;
  metrics: Record<string, number | string | boolean | null>;
};

export type HostReadiness = {
  generated_at: string;
  status: "OPERATIONAL" | "ATTENTION" | "CRITICAL";
  checks: HostCheck[];
};

export type DiagnosticPlan = {
  included: string[];
  excluded: string[];
  encryption: string;
};

export function getOperationalSnapshot(): Promise<OperationalSnapshot> {
  return request<OperationalSnapshot>("/api/v1/operations/snapshot");
}

export function getHostReadiness(): Promise<HostReadiness> {
  return request<HostReadiness>("/api/v1/operations/host-readiness");
}

export function getDiagnosticPlan(): Promise<DiagnosticPlan> {
  return request<DiagnosticPlan>("/api/v1/operations/diagnostic-bundle/plan");
}

export function downloadDiagnosticBundle(passphrase: string): Promise<Blob> {
  return requestBlob("/api/v1/operations/diagnostic-bundle", {
    method: "POST",
    body: JSON.stringify({ passphrase }),
  });
}