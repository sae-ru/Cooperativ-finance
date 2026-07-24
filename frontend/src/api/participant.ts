import { commandHeaders, request, requestBlob } from "./admin";

export type ParticipantMembership = {
  id: string;
  cooperative_id: string;
  cooperative_code: string;
  cooperative_name: string;
  cooperative_status: string;
  member_number: string;
  membership_status: string;
  joined_at: string | null;
};

export type ParticipantShareAccount = {
  id: string;
  cooperative_id: string;
  contour: string;
  denomination: string;
  balance: string;
  available: string;
  protected: string;
  reserved: string;
  executed_not_settled: string;
  status: string;
  policy: null | {
    id: string;
    version: number;
    terms_hash: string;
    approval_event_id: string | null;
    approved_at: string | null;
    max_member_exposure: string;
  };
  sources: Array<{
    amount: string;
    source_reference: string;
    event_id: string;
    created_at: string;
  }>;
};

export type ParticipantOffer = {
  record_id: string;
  offer_id: string;
  offer_version: number;
  kind: "PRODUCT" | "SERVICE";
  has_image: boolean;
  product_code: string;
  description: string;
  quantity_available: string;
  unit_code: string;
  minimum_batch: string;
  unit_price: string;
  valuation_unit: string;
  price_policy_version: string;
  origin_region: string;
  pickup_address_text: string | null;
  pickup_contact_name: string | null;
  pickup_contact_phone: string | null;
  pickup_instructions: string | null;
  status: string;
  availability_until: string;
  created_at: string;
  payload_hash: string;
};

export type ParticipantAddress = {
  id: string;
  cooperative_id: string;
  label: string;
  purpose: "PICKUP" | "DELIVERY" | "BOTH";
  region_code: string;
  address_text: string;
  contact_name: string;
  contact_phone: string;
  instructions: string | null;
  is_default_pickup: boolean;
  is_default_delivery: boolean;
  status: "ACTIVE" | "ARCHIVED";
  created_at: string;
  updated_at: string;
  version: number;
};

export type ParticipantAddressDraft = Omit<
  ParticipantAddress,
  "id" | "status" | "created_at" | "updated_at" | "version"
>;

export type ParticipantPurchase = {
  id: string;
  status: string;
  description: string;
  quantity: string;
  unit_code: string;
  landed_cost: string;
  created_at: string;
  committed_at: string | null;
};

export type ParticipantSale = {
  id: string;
  status: string;
  description: string;
  quantity: string;
  unit_code: string;
  goods_value: string;
  delivery_address_text: string | null;
  delivery_contact_name: string | null;
  delivery_contact_phone: string | null;
  delivery_instructions: string | null;
  created_at: string;
  committed_at: string | null;
};

export type ParticipantObligation = {
  id: string;
  deal_id: string;
  cooperative_id: string;
  debtor_member_id: string;
  creditor_member_id: string;
  source_purchase_intent_id: string | null;
  direction: "OWE" | "RECEIVE";
  subject_type: string;
  description: string;
  quantity_total: string;
  quantity_submitted: string;
  quantity_fulfilled: string;
  quantity_cleared: string;
  unit_id: string;
  unit_code: string;
  unit_symbol: string;
  unit_dimension: string;
  due_at: string;
  fulfillment_place: string;
  partial_allowed: boolean;
  evidence_required: boolean;
  status: string;
  version: number;
  valuation_source: string;
  clearing_allowed: boolean;
};

export type ParticipantDashboard = {
  profile: {
    member_id: string;
    display_name: string;
    member_status: string;
    login: string;
    last_login_at: string | null;
    member_since: string;
  };
  memberships: ParticipantMembership[];
  shares: {
    denomination: string | null;
    total_balance: string;
    available: string;
    protected: string;
    reserved: string;
    accounts: ParticipantShareAccount[];
    account_missing: boolean;
  };
  exchange_position: {
    earned_settled: string;
    expected_incoming: string;
    expected_outgoing: string;
  };
  offers: ParticipantOffer[];
  purchases: ParticipantPurchase[];
  sales: ParticipantSale[];
  obligations: ParticipantObligation[];
  commitments: Array<{
    id: string;
    type: string;
    risk_type: string;
    amount_reserved: string;
    max_loss: string;
    status: string;
    expires_at: string;
    release_condition: string;
  }>;
  generated_at: string;
  cooperative_count: number;
};

type CommandResult = { event_id: string; object_id: string; replayed: boolean };

export const getParticipantDashboard = () =>
  request<ParticipantDashboard>("/api/v1/participant/dashboard");

export const getParticipantAddresses = () =>
  request<ParticipantAddress[]>("/api/v1/participant/addresses");

export const createParticipantAddress = (draft: ParticipantAddressDraft) =>
  request<CommandResult>("/api/v1/participant/addresses", {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify(draft),
  });

export const updateParticipantAddress = (
  address: ParticipantAddress,
  draft: ParticipantAddressDraft,
) =>
  request<CommandResult>(`/api/v1/participant/addresses/${address.id}`, {
    method: "PUT",
    headers: commandHeaders(),
    body: JSON.stringify({ ...draft, expected_version: address.version }),
  });

export const archiveParticipantAddress = (address: ParticipantAddress) =>
  request<CommandResult>(`/api/v1/participant/addresses/${address.id}/archive`, {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify({ expected_version: address.version }),
  });

export const revokeOwnOffer = (offer: ParticipantOffer, reason: string) =>
  request<CommandResult>("/api/v1/federation/offers/revoke", {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify({
      offer_id: offer.offer_id,
      expected_version: offer.offer_version,
      reason,
    }),
  });

export const materializePurchaseDeal = (intentId: string) =>
  request<CommandResult>(
    `/api/v1/federation/purchase-intents/${intentId}/materialize-deal`,
    { method: "POST" },
  );

export const getOfferImage = (recordId: string) =>
  requestBlob(`/api/v1/federation/catalog/offers/${recordId}/image`);