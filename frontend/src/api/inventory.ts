import {
  AdminApiError,
  commandHeaders,
  request,
  requestBlob,
  requestDirect,
} from "./admin";

export type UnitOfMeasure = {
  id: string;
  cooperative_id: string;
  code: string;
  name: string;
  symbol: string;
  dimension: string;
  decimal_scale: number;
  status: string;
  created_event_id: string;
};

export type Product = {
  id: string;
  cooperative_id: string;
  sku: string;
  name: string;
  description: string;
  default_unit_id: string;
  quantity_tolerance: string;
  requires_evidence: boolean;
  shelf_life_required: boolean;
  status: string;
  created_event_id: string;
};

export type Warehouse = {
  id: string;
  cooperative_id: string;
  code: string;
  name: string;
  address_text: string;
  storage_conditions: string;
  status: string;
  created_event_id: string;
};

export type InventoryMember = {
  member_id: string;
  cooperative_id: string;
  display_name: string;
  member_number: string;
};

export type InventoryCustodian = {
  assignment_id: string;
  cooperative_id: string;
  warehouse_id: string;
  member_id: string;
  user_id: string;
  display_name: string;
  role_code: string;
};

export type Evidence = {
  id: string;
  cooperative_id: string;
  expected_sha256: string;
  expected_size: number;
  mime_type: string;
  kind: string;
  original_name: string;
  access_scope: string;
  retention_until: string | null;
  status: string;
  encryption_algorithm: string | null;
  created_by_user_id: string;
  created_event_id: string;
  completed_event_id: string | null;
  created_at: string;
  ready_at: string | null;
};

export type InventoryLot = {
  id: string;
  cooperative_id: string;
  lot_number: string;
  product_id: string;
  warehouse_id: string;
  owner_member_id: string;
  unit_id: string;
  declared_quantity: string;
  current_quantity: string | null;
  declared_quality: string;
  verified_quality: string | null;
  expires_at: string | null;
  storage_conditions: string;
  status: string;
  received_by_member_id: string;
  custodian_assignment_id: string;
  registered_event_id: string;
  verified_event_id: string | null;
  created_at: string;
  updated_at: string;
  version: number;
};

export type InventoryDiscrepancy = {
  id: string;
  lot_id: string;
  expected_quantity: string;
  actual_quantity: string;
  variance: string;
  reason_code: string;
  notes: string;
  status: string;
  recorded_by_user_id: string;
  event_id: string;
  created_at: string;
};

export type CustodyTransfer = {
  id: string;
  lot_id: string;
  from_warehouse_id: string;
  to_warehouse_id: string;
  from_assignment_id: string;
  to_assignment_id: string;
  place: string;
  notes: string;
  status: string;
  offered_by_user_id: string;
  accepted_by_user_id: string | null;
  offered_event_id: string;
  accepted_event_id: string | null;
  offered_at: string;
  accepted_at: string | null;
};

export type HistoryEvent = {
  event_id: string;
  event_type: string;
  aggregate_version: number;
  occurred_at: string;
  event_hash: string;
  payload: Record<string, unknown>;
};

export type ReceiptAct = {
  lot: InventoryLot;
  product: Product;
  unit: UnitOfMeasure;
  warehouse: Warehouse;
  owner_name: string;
  receiver_name: string;
  attester_name: string | null;
  custodian_name: string;
  attestation: Record<string, unknown> | null;
  evidence: Evidence[];
  signed_events: HistoryEvent[];
  generated_at: string;
};

type CommandResult = { event_id: string; object_id: string; replayed: boolean };

export const getUnits = () => request<UnitOfMeasure[]>("/api/v1/units");
export const getProducts = () => request<Product[]>("/api/v1/products");
export const getWarehouses = () => request<Warehouse[]>("/api/v1/warehouses");
export const getInventoryMembers = () =>
  request<InventoryMember[]>("/api/v1/inventory/members");
export const getInventoryCustodians = () =>
  request<InventoryCustodian[]>("/api/v1/inventory/custodians");
export const getLots = () => request<InventoryLot[]>("/api/v1/inventory/lots");
export const getDiscrepancies = () =>
  request<InventoryDiscrepancy[]>("/api/v1/inventory/discrepancies");
export const getCustodyTransfers = () =>
  request<CustodyTransfer[]>("/api/v1/inventory/custody-transfers");

export const createUnit = (payload: {
  cooperative_id: string;
  code: string;
  name: string;
  symbol: string;
  dimension: string;
  decimal_scale: number;
}) => request<CommandResult>("/api/v1/units", {
  method: "POST",
  headers: commandHeaders(),
  body: JSON.stringify(payload),
});

export const createProduct = (payload: {
  cooperative_id: string;
  sku: string;
  name: string;
  description: string;
  default_unit_id: string;
  quantity_tolerance: string;
  requires_evidence: boolean;
  shelf_life_required: boolean;
}) => request<CommandResult>("/api/v1/products", {
  method: "POST",
  headers: commandHeaders(),
  body: JSON.stringify(payload),
});

export const createWarehouse = (payload: {
  cooperative_id: string;
  code: string;
  name: string;
  address_text: string;
  storage_conditions: string;
}) => request<CommandResult>("/api/v1/warehouses", {
  method: "POST",
  headers: commandHeaders(),
  body: JSON.stringify(payload),
});

export const registerLot = (payload: {
  cooperative_id: string;
  lot_number: string;
  product_id: string;
  warehouse_id: string;
  owner_member_id: string;
  declared_quantity: string;
  unit_id: string;
  declared_quality: string;
  expires_at: string | null;
  storage_conditions: string;
  custodian_assignment_id: string;
  evidence_ids: string[];
}) => request<CommandResult>("/api/v1/inventory/lots", {
  method: "POST",
  headers: commandHeaders(),
  body: JSON.stringify(payload),
});

export const attestLot = (
  lot: InventoryLot,
  payload: {
    measured_quantity: string;
    quality_decision: "ACCEPTED" | "REJECTED";
    verified_quality: string;
    measurements: Record<string, string>;
    notes: string;
    evidence_ids: string[];
  },
) => request<CommandResult>(`/api/v1/inventory/lots/${lot.id}/attest`, {
  method: "POST",
  headers: commandHeaders(),
  body: JSON.stringify({ ...payload, expected_version: lot.version }),
});

export const recordDiscrepancy = (
  lot: InventoryLot,
  payload: {
    actual_quantity: string;
    reason_code: string;
    notes: string;
    evidence_ids: string[];
  },
) => request<CommandResult>(`/api/v1/inventory/lots/${lot.id}/discrepancies`, {
  method: "POST",
  headers: commandHeaders(),
  body: JSON.stringify({ ...payload, expected_version: lot.version }),
});

export const offerCustody = (
  lot: InventoryLot,
  payload: {
    to_warehouse_id: string;
    to_assignment_id: string;
    place: string;
    notes: string;
    evidence_ids: string[];
  },
) => request<CommandResult>(`/api/v1/inventory/lots/${lot.id}/custody-transfers`, {
  method: "POST",
  headers: commandHeaders(),
  body: JSON.stringify({ ...payload, expected_version: lot.version }),
});

export const acceptCustody = (
  transfer: CustodyTransfer,
  lot: InventoryLot,
  evidenceIds: string[],
) => request<CommandResult>(
  `/api/v1/inventory/custody-transfers/${transfer.id}/accept`,
  {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify({
      evidence_ids: evidenceIds,
      expected_lot_version: lot.version,
    }),
  },
);

export const getReceiptAct = (lotId: string) =>
  requestDirect<ReceiptAct>(`/api/v1/inventory/lots/${lotId}/receipt-act`);

export const downloadEvidence = (evidenceId: string) =>
  requestBlob(`/api/v1/evidence/${evidenceId}/content`);

export async function uploadEvidence(
  cooperativeId: string,
  file: File,
  kind: string,
): Promise<string> {
  return (await uploadEvidenceProof(cooperativeId, file, kind)).evidenceId;
}

export async function uploadEvidenceProof(
  cooperativeId: string,
  file: File,
  kind: string,
): Promise<{ evidenceId: string; completedEventId: string }> {
  const allowed = ["application/pdf", "image/jpeg", "image/png", "image/webp", "text/plain"];
  if (!allowed.includes(file.type)) {
    throw new AdminApiError("EVIDENCE_TYPE_INVALID", null, 400);
  }
  const digest = Array.from(
    new Uint8Array(await crypto.subtle.digest("SHA-256", await file.arrayBuffer())),
    (value) => value.toString(16).padStart(2, "0"),
  ).join("");
  const intent = await request<CommandResult>("/api/v1/evidence/upload-intents", {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify({
      cooperative_id: cooperativeId,
      expected_sha256: digest,
      expected_size: file.size,
      mime_type: file.type,
      kind,
      original_name: file.name,
      access_scope: "COOPERATIVE",
      retention_until: null,
    }),
  });
  const completed = await request<CommandResult>(
    `/api/v1/evidence/upload-intents/${intent.object_id}/content`,
    { method: "PUT", headers: { "Content-Type": file.type }, body: file },
  );
  return { evidenceId: intent.object_id, completedEventId: completed.event_id };
}