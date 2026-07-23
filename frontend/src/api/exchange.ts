import { commandHeaders, request, requestDirect } from "./admin";

export type Deal = {
  id: string;
  cooperative_id: string;
  title: string;
  status: string;
  terms_version: number;
  terms_hash: string;
  proposed_by_member_id: string;
  proposed_event_id: string;
  confirmed_event_id: string | null;
  confirmed_at: string | null;
  created_at: string;
  updated_at: string;
  version: number;
};

export type DealParty = {
  id: string;
  deal_id: string;
  terms_version: number;
  terms_hash: string;
  member_id: string;
  created_event_id: string;
  created_at: string;
};

export type DealConfirmation = {
  id: string;
  deal_id: string;
  terms_version: number;
  terms_hash: string;
  member_id: string;
  role_assignment_id: string;
  event_id: string;
  confirmed_at: string;
};

export type Obligation = {
  id: string;
  deal_id: string;
  cooperative_id: string;
  sequence_no: number;
  terms_version: number;
  debtor_member_id: string;
  creditor_member_id: string;
  subject_type: string;
  subject_id: string | null;
  description: string;
  quality_criteria: string;
  fulfillment_place: string;
  due_at: string;
  unit_id: string;
  quantity_total: string;
  quantity_submitted: string;
  quantity_fulfilled: string;
  quantity_cleared: string;
  partial_allowed: boolean;
  evidence_required: boolean;
  confirmation_method: string;
  substitute_policy: string;
  valuation_source: string;
  liquidity_class: string;
  clearing_allowed: boolean;
  status: string;
  created_event_id: string;
  last_event_id: string;
  created_at: string;
  updated_at: string;
  version: number;
};

export type Fulfillment = {
  id: string;
  obligation_id: string;
  logistics_order_id: string | null;
  quantity: string;
  accepted_quantity: string;
  quality_claim: string;
  location_text: string;
  performed_at: string;
  status: string;
  performed_by_member_id: string;
  submitted_event_id: string;
  accepted_event_id: string | null;
  created_at: string;
  updated_at: string;
  version: number;
};

export type Acceptance = {
  id: string;
  fulfillment_id: string;
  accepted_quantity: string;
  decision: string;
  quality_status: string;
  notes: string;
  accepted_by_member_id: string;
  event_id: string;
  created_at: string;
};

export type LogisticsOrder = {
  id: string;
  obligation_id: string;
  cooperative_id: string;
  carrier_member_id: string;
  quantity: string;
  unit_id: string;
  origin_text: string;
  destination_text: string;
  pickup_due_at: string;
  delivery_due_at: string;
  status: string;
  carrier_user_id: string | null;
  offered_event_id: string;
  accepted_event_id: string | null;
  pickup_event_id: string | null;
  delivered_event_id: string | null;
  accepted_at: string | null;
  picked_up_at: string | null;
  delivered_at: string | null;
  created_at: string;
  updated_at: string;
  version: number;
};

export type Dispute = {
  id: string;
  obligation_id: string;
  fulfillment_id: string | null;
  reason_code: string;
  statement: string;
  status: string;
  previous_obligation_status: string;
  previous_fulfillment_status: string | null;
  opened_by_member_id: string;
  event_id: string;
  resolution_action: string | null;
  resolution_notes: string | null;
  resolved_by_member_id: string | null;
  resolution_event_id: string | null;
  created_at: string;
  resolved_at: string | null;
  version: number;
};

export type ObligationDraft = {
  debtor_member_id: string;
  creditor_member_id: string;
  subject_type: "PRODUCT" | "SERVICE" | "LOGISTICS" | "OTHER";
  subject_id: string | null;
  description: string;
  quality_criteria: string;
  fulfillment_place: string;
  due_at: string;
  unit_id: string;
  quantity: string;
  partial_allowed: boolean;
  evidence_required: boolean;
  confirmation_method: string;
  substitute_policy: string;
  valuation_source: string;
  liquidity_class: string;
  clearing_allowed: boolean;
};

export type DealDetail = {
  deal: Deal;
  terms: Record<string, unknown>;
  parties: DealParty[];
  confirmations: DealConfirmation[];
  obligations: Obligation[];
};

type CommandResult = { event_id: string; object_id: string; replayed: boolean };

export const getDeals = () => request<Deal[]>("/api/v1/exchange/deals");
export const getDeal = (dealId: string) =>
  requestDirect<DealDetail>(`/api/v1/exchange/deals/${dealId}`);
export const getObligations = () =>
  request<Obligation[]>("/api/v1/exchange/obligations");
export const getFulfillments = (obligationId: string) =>
  request<Fulfillment[]>(`/api/v1/exchange/obligations/${obligationId}/fulfillments`);
export const getAcceptances = () =>
  request<Acceptance[]>("/api/v1/exchange/acceptances");
export const getLogisticsOrders = () =>
  request<LogisticsOrder[]>("/api/v1/exchange/logistics-orders");
export const getDisputes = () =>
  request<Dispute[]>("/api/v1/exchange/disputes");

export const proposeDeal = (payload: {
  cooperative_id: string;
  title: string;
  obligations: ObligationDraft[];
}) => request<CommandResult>("/api/v1/exchange/deals", {
  method: "POST",
  headers: commandHeaders(),
  body: JSON.stringify(payload),
});

export const reviseDeal = (
  deal: Deal,
  payload: { title: string; obligations: ObligationDraft[] },
) => request<CommandResult>(`/api/v1/exchange/deals/${deal.id}/terms`, {
  method: "PUT",
  headers: commandHeaders(),
  body: JSON.stringify({ ...payload, expected_version: deal.version }),
});
export const confirmDeal = (deal: Deal) =>
  request<CommandResult>(`/api/v1/exchange/deals/${deal.id}/confirmations`, {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify({
      terms_version: deal.terms_version,
      terms_hash: deal.terms_hash,
      expected_version: deal.version,
    }),
  });

export const submitFulfillment = (
  obligation: Obligation,
  payload: {
    quantity: string;
    quality_claim: string;
    location_text: string;
    performed_at: string;
    logistics_order_id: string | null;
    evidence_ids: string[];
  },
) => request<CommandResult>(`/api/v1/exchange/obligations/${obligation.id}/fulfillments`, {
  method: "POST",
  headers: commandHeaders(),
  body: JSON.stringify({ ...payload, expected_version: obligation.version }),
});

export const acceptFulfillment = (
  obligation: Obligation,
  fulfillment: Fulfillment,
  payload: {
    accepted_quantity: string;
    quality_status: string;
    notes: string;
    evidence_ids: string[];
  },
) => request<CommandResult>(`/api/v1/exchange/fulfillments/${fulfillment.id}/acceptance`, {
  method: "POST",
  headers: commandHeaders(),
  body: JSON.stringify({
    ...payload,
    expected_fulfillment_version: fulfillment.version,
    expected_obligation_version: obligation.version,
  }),
});

export const openDispute = (
  obligation: Obligation,
  payload: {
    fulfillment_id: string | null;
    reason_code: string;
    statement: string;
    evidence_ids: string[];
  },
) => request<CommandResult>(`/api/v1/exchange/obligations/${obligation.id}/disputes`, {
  method: "POST",
  headers: commandHeaders(),
  body: JSON.stringify({ ...payload, expected_version: obligation.version }),
});

export const resolveDispute = (
  dispute: Dispute,
  payload: {
    resolution_action:
      | "REJECT_CLAIM"
      | "CONTINUE_PERFORMANCE"
      | "DEFAULT_OBLIGATION"
      | "CLOSE_OBLIGATION";
    resolution_notes: string;
    evidence_ids: string[];
  },
) => request<CommandResult>(`/api/v1/exchange/disputes/${dispute.id}/resolution`, {
  method: "POST",
  headers: commandHeaders(),
  body: JSON.stringify({ ...payload, expected_version: dispute.version }),
});
export const createLogisticsOrder = (
  obligation: Obligation,
  payload: {
    carrier_member_id: string;
    quantity: string;
    origin_text: string;
    destination_text: string;
    pickup_due_at: string;
    delivery_due_at: string;
  },
) => request<CommandResult>(
  `/api/v1/exchange/obligations/${obligation.id}/logistics-orders`,
  {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify({ ...payload, expected_obligation_version: obligation.version }),
  },
);

export const transitionLogisticsOrder = (
  order: LogisticsOrder,
  action: "accept" | "pickup" | "deliver",
  evidenceIds: string[],
) => request<CommandResult>(`/api/v1/exchange/logistics-orders/${order.id}/${action}`, {
  method: "POST",
  headers: commandHeaders(),
  body: JSON.stringify({ evidence_ids: evidenceIds, expected_version: order.version }),
});

export const markOverdue = (cooperativeId: string, asOf: string) =>
  request<CommandResult>("/api/v1/exchange/overdue-scans", {
    method: "POST",
    headers: commandHeaders(),
    body: JSON.stringify({ cooperative_id: cooperativeId, as_of: asOf }),
  });
