import { commandHeaders, request, requestDirect } from "./admin";

export type SearchMode = "DIRECT" | "INDEXED" | "CACHED_OFFLINE";

export type FederatedOffer = {
  record_id: string;
  offer_id: string;
  offer_version: number;
  home_node_code: string;
  seller_ref: string;
  product_code: string;
  description: string;
  quality_grade: string;
  certificate_refs: string[];
  quantity_available: string;
  quantity_is_band: boolean;
  unit_code: string;
  unit_scale: number;
  minimum_batch: string;
  divisible: boolean;
  origin_region: string;
  origin_precision: string;
  availability_from: string;
  availability_until: string;
  fulfillment_deadline: string;
  unit_price: string;
  mandatory_fee_per_unit: string;
  valuation_unit: string;
  price_policy_version: string;
  handling_requirements: Record<string, unknown>;
  counterparty_policy: Record<string, unknown>;
  geography_policy: Record<string, unknown>;
  guarantee_terms: Record<string, unknown>;
  source_mode: string;
  node_sequence: number;
  signed_at: string;
  valid_until: string;
  signer_fingerprint: string;
  payload_hash: string;
};

export type LogisticsQuote = {
  record_id: string;
  quote_id: string;
  quote_version: number;
  home_node_code: string;
  carrier_ref: string;
  destination_region: string;
  route_legs: unknown[];
  custody_transfers: number;
  capacity: string;
  unit_code: string;
  cost_components: Record<string, string | number>;
  valuation_unit: string;
  cost_status: string;
  delivery_from: string;
  delivery_until: string;
  liability_limit: string;
  bond_ref: string | null;
  assumptions: string[];
  signed_at: string;
  valid_until: string;
  signer_fingerprint: string;
};

export type SearchCandidate = {
  offer: FederatedOffer;
  quote: LogisticsQuote | null;
  freshness: string;
  signature_verified: boolean;
  goods_cost: string;
  logistics_cost: string | null;
  mandatory_cost: string | null;
  landed_cost: string | null;
  cost_status: string | null;
};

export type SearchFilters = {
  mode: SearchMode;
  product_code: string;
  quantity: string;
  unit_code: string;
  valuation_unit: string;
  destination_region: string;
  maximum_age_seconds: number;
  trusted_node_codes: string[];
  required_certificates: string[];
  quality_minimum: string | null;
  maximum_goods_cost: string | null;
  maximum_landed_cost: string | null;
  latest_delivery: string | null;
  top_k: number;
};

export type OfferDraft = {
  product_code: string;
  description: string;
  quantity_available: string;
  unit_code: string;
  minimum_batch: string;
  origin_region: string;
  unit_price: string;
  available_until: string;
};
export type PeerStatus = {
  node_code: string;
  status: string;
  result_code: string;
  imported_offers: number;
  imported_quotes: number;
};

export type SearchResponse = {
  data: SearchCandidate[];
  mode: SearchMode;
  peer_statuses: PeerStatus[];
  ranking_version: string;
  request_id: string;
};

export type ArtifactVerification = {
  valid: boolean;
  freshness: string;
  home_node_code: string;
  signer_fingerprint: string;
  valid_until: string;
};

export type PurchaseIntent = {
  id: string;
  buyer_node_code: string;
  buyer_member_id: string;
  offer_record_id: string;
  quote_record_id: string;
  quantity: string;
  unit_code: string;
  destination_region: string;
  max_landed_cost: string;
  landed_cost_breakdown: Record<string, unknown>;
  cost_status: string;
  summary_hash: string;
  status: string;
  commit_request_hash: string | null;
  commit_expected_version: number | null;
  cancellation_expected_version: number | null;
  created_at: string;
  expires_at: string;
  committed_at: string | null;
  closed_at: string | null;
  version: number;
};

export type ReservationReceipt = {
  id: string;
  intent_id: string;
  kind: string;
  resource_ref: string;
  home_node_code: string;
  amount: string;
  unit_code: string;
  status: string;
  receipt_hash: string;
  signer_fingerprint: string;
  remote_commit_hash: string | null;
  remote_commit_signer_fingerprint: string | null;
  remote_release_hash: string | null;
  remote_release_signer_fingerprint: string | null;
  created_at: string;
  expires_at: string;
  closed_at: string | null;
  version: number;
};

type CommandResult = { event_id: string; object_id: string; replayed: boolean };

export function publishOffer(draft: OfferDraft, sellerRef: string) {
  const now = new Date();
  const availableUntil = new Date(draft.available_until);
  const unitScale = draft.unit_code === "PCS" ? 0 : 3;
  return request<CommandResult>("/api/v1/federation/offers/publish", {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify({
      seller_ref: sellerRef,
      product_code: draft.product_code,
      description: draft.description,
      quality_grade: "A",
      certificate_refs: [],
      quantity_available: draft.quantity_available,
      quantity_is_band: false,
      unit_code: draft.unit_code,
      unit_scale: unitScale,
      minimum_batch: draft.minimum_batch,
      divisible: true,
      origin_region: draft.origin_region,
      origin_precision: "DISTRICT",
      availability_from: now.toISOString(),
      availability_until: availableUntil.toISOString(),
      fulfillment_deadline: new Date(availableUntil.getTime() + 24 * 60 * 60_000).toISOString(),
      unit_price: draft.unit_price,
      mandatory_fee_per_unit: "0",
      valuation_unit: "COOP",
      price_policy_version: "MARKET-UI-V1",
      handling_requirements: {},
      counterparty_policy: {},
      geography_policy: {},
      guarantee_terms: {},
      source_mode: "DIRECT",
      node_sequence: Date.now(),
      signed_at: now.toISOString(),
      valid_until: availableUntil.toISOString(),
    }),
  });
}
export async function searchCatalog(filters: SearchFilters): Promise<SearchResponse> {
  return requestDirect<SearchResponse>(
    "/api/v1/federation/catalog/search",
    { method: "POST", body: JSON.stringify(filters) },
  );
}

export const verifyOffer = (recordId: string) =>
  request<ArtifactVerification>(`/api/v1/federation/catalog/offers/${recordId}/verify`, {
    method: "POST",
    body: JSON.stringify({ live: true, maximum_age_seconds: 604800 }),
  });

export const getPurchaseIntents = () =>
  request<PurchaseIntent[]>("/api/v1/federation/purchase-intents");

export const getReservationReceipts = (intentId: string) =>
  request<ReservationReceipt[]>(
    `/api/v1/federation/purchase-intents/${intentId}/receipts`,
  );

export const createPurchaseIntent = (candidate: SearchCandidate, quantity: string) => {
  if (!candidate.quote || !candidate.landed_cost) throw new Error("LOGISTICS_QUOTE_REQUIRED");
  return request<CommandResult>("/api/v1/federation/purchase-intents", {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify({
      offer_record_id: candidate.offer.record_id,
      quote_record_id: candidate.quote.record_id,
      quantity,
      destination_region: candidate.quote.destination_region,
      max_landed_cost: candidate.landed_cost,
      expires_at: new Date(Date.now() + 30 * 60_000).toISOString(),
    }),
  });
};

export const reservePurchase = (intentId: string, kind: "goods" | "logistics") =>
  request<CommandResult>(
    `/api/v1/federation/purchase-intents/${intentId}/reserve-${kind}`,
    {
      method: "POST",
      headers: commandHeaders(),
      body: JSON.stringify({ expires_at: new Date(Date.now() + 20 * 60_000).toISOString() }),
    },
  );

export const commitPurchase = (intent: PurchaseIntent) =>
  request<CommandResult>(`/api/v1/federation/purchase-intents/${intent.id}/commit`, {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify({
      summary_hash: intent.summary_hash,
      expected_version: intent.commit_expected_version ?? intent.version,
    }),
  });

export const cancelPurchase = (intent: PurchaseIntent, reason: string) =>
  request<CommandResult>(`/api/v1/federation/purchase-intents/${intent.id}/cancel`, {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify({
      reason,
      expected_version: intent.cancellation_expected_version ?? intent.version,
    }),
  });
