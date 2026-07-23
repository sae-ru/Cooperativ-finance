import { request } from "./admin";

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
  active_crisis_mandates: number;
  issued_crisis_forms: number;
};

export function getOperationalSnapshot(): Promise<OperationalSnapshot> {
  return request<OperationalSnapshot>("/api/v1/operations/snapshot");
}
