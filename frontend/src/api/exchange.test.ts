import { afterEach, describe, expect, it, vi } from "vitest";

import {
  acceptFulfillment,
  confirmDeal,
  createLogisticsOrder,
  getAcceptances,
  getDeal,
  getDeals,
  getDisputes,
  getFulfillments,
  getLogisticsOrders,
  getObligations,
  markOverdue,
  openDispute,
  proposeDeal,
  resolveDispute,
  reviseDeal,
  submitFulfillment,
  transitionLogisticsOrder,
  type Deal,
  type Dispute,
  type Fulfillment,
  type LogisticsOrder,
  type Obligation,
  type ObligationDraft,
} from "./exchange";

function envelope(data: unknown, status = 200): Response {
  return new Response(JSON.stringify({ data }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const deal: Deal = {
  id: "deal-1",
  cooperative_id: "coop-1",
  title: "Поставка",
  status: "ACTIVE",
  terms_version: 2,
  terms_hash: `sha256:${"a".repeat(64)}`,
  proposed_by_member_id: "member-admin",
  proposed_event_id: "event-1",
  confirmed_event_id: "event-2",
  confirmed_at: "2026-07-20T10:00:00Z",
  created_at: "2026-07-20T09:00:00Z",
  updated_at: "2026-07-20T10:00:00Z",
  version: 4,
};

const obligation: Obligation = {
  id: "obligation-1",
  deal_id: deal.id,
  cooperative_id: deal.cooperative_id,
  sequence_no: 1,
  terms_version: deal.terms_version,
  debtor_member_id: "member-a",
  creditor_member_id: "member-b",
  subject_type: "PRODUCT",
  subject_id: null,
  description: "Капуста",
  quality_criteria: "Первый сорт",
  fulfillment_place: "Склад",
  due_at: "2026-07-21T10:00:00Z",
  unit_id: "unit-1",
  quantity_total: "10",
  quantity_submitted: "2",
  quantity_fulfilled: "4",
  quantity_cleared: "0",
  partial_allowed: true,
  evidence_required: true,
  confirmation_method: "Акт",
  substitute_policy: "По согласию",
  valuation_source: "Без денежной оценки",
  liquidity_class: "UNASSESSED",
  clearing_allowed: false,

  status: "PARTIALLY_FULFILLED",
  created_event_id: "event-3",
  last_event_id: "event-4",
  created_at: "2026-07-20T10:00:00Z",
  updated_at: "2026-07-20T11:00:00Z",
  version: 3,
};

const fulfillment: Fulfillment = {
  id: "fulfillment-1",
  obligation_id: obligation.id,
  logistics_order_id: null,
  quantity: "2",
  accepted_quantity: "0",
  quality_claim: "Первый сорт",
  location_text: "Склад",
  performed_at: "2026-07-20T11:00:00Z",
  status: "SUBMITTED",
  performed_by_member_id: obligation.debtor_member_id,
  submitted_event_id: "event-5",
  accepted_event_id: null,
  created_at: "2026-07-20T11:00:00Z",
  updated_at: "2026-07-20T11:00:00Z",
  version: 1,
};

const logisticsOrder: LogisticsOrder = {
  id: "logistics-1",
  obligation_id: obligation.id,
  cooperative_id: deal.cooperative_id,
  carrier_member_id: "member-carrier",
  quantity: "2",
  unit_id: obligation.unit_id,
  origin_text: "Поле",
  destination_text: "Склад",
  origin_contact_name: null,
  origin_contact_phone: null,
  origin_instructions: null,
  destination_contact_name: null,
  destination_contact_phone: null,
  destination_instructions: null,
  pickup_due_at: "2026-07-20T10:00:00Z",
  delivery_due_at: "2026-07-20T12:00:00Z",
  status: "OFFERED",
  carrier_user_id: null,
  offered_event_id: "event-6",
  accepted_event_id: null,
  pickup_event_id: null,
  delivered_event_id: null,
  accepted_at: null,
  picked_up_at: null,
  delivered_at: null,
  created_at: "2026-07-20T09:00:00Z",
  updated_at: "2026-07-20T09:00:00Z",
  version: 1,
};

const dispute: Dispute = {
  id: "dispute-1",
  obligation_id: obligation.id,
  fulfillment_id: fulfillment.id,
  reason_code: "QUALITY",
  statement: "Требуется осмотр",
  status: "OPEN",
  previous_obligation_status: "PARTIALLY_FULFILLED",
  previous_fulfillment_status: "SUBMITTED",
  opened_by_member_id: obligation.debtor_member_id,
  event_id: "event-7",
  resolution_action: null,
  resolution_notes: null,
  resolved_by_member_id: null,
  resolution_event_id: null,
  created_at: "2026-07-20T12:00:00Z",
  resolved_at: null,
  version: 1,
};

const draft: ObligationDraft = {
  debtor_member_id: obligation.debtor_member_id,
  creditor_member_id: obligation.creditor_member_id,
  subject_type: "PRODUCT",
  subject_id: null,
  description: obligation.description,
  quality_criteria: obligation.quality_criteria,
  fulfillment_place: obligation.fulfillment_place,
  due_at: obligation.due_at,
  unit_id: obligation.unit_id,
  quantity: obligation.quantity_total,
  partial_allowed: true,
  evidence_required: true,
  confirmation_method: obligation.confirmation_method,
  substitute_policy: obligation.substitute_policy,
  valuation_source: obligation.valuation_source,
  liquidity_class: obligation.liquidity_class,
  clearing_allowed: false,

};

describe("exchange API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("reads participant-scoped registries and direct deal detail", async () => {
    const fetchMock = vi.fn().mockImplementation((path: string) => Promise.resolve(
      path === "/api/v1/exchange/deals/deal-1"
        ? new Response(JSON.stringify({ deal, terms: {} }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          })
        : envelope([]),
    ));
    vi.stubGlobal("fetch", fetchMock);

    await getDeals();
    await getDeal(deal.id);
    await getObligations();
    await getFulfillments(obligation.id);
    await getAcceptances();
    await getLogisticsOrders();
    await getDisputes();

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/v1/exchange/deals",
      "/api/v1/exchange/deals/deal-1",
      "/api/v1/exchange/obligations",
      "/api/v1/exchange/obligations/obligation-1/fulfillments",
      "/api/v1/exchange/acceptances",
      "/api/v1/exchange/logistics-orders",
      "/api/v1/exchange/disputes",
    ]);
  });

  it("sends exact versions and idempotency keys for state-changing commands", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(envelope({
      event_id: "event-result",
      object_id: "object-result",
      replayed: false,
    })));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("crypto", {
      randomUUID: () => "00000000-0000-4000-8000-000000000001",
    });

    await proposeDeal({ cooperative_id: deal.cooperative_id, title: deal.title, obligations: [draft] });
    await reviseDeal(deal, { title: "Новая версия", obligations: [draft] });
    await confirmDeal(deal);
    await submitFulfillment(obligation, {
      quantity: "2",
      quality_claim: "Первый сорт",
      location_text: "Склад",
      performed_at: fulfillment.performed_at,
      logistics_order_id: null,
      evidence_ids: ["evidence-1"],
    });
    await acceptFulfillment(obligation, fulfillment, {
      accepted_quantity: "2",
      quality_status: "Принято",
      notes: "Без замечаний",
      evidence_ids: ["evidence-2"],
    });
    await openDispute(obligation, {
      fulfillment_id: fulfillment.id,
      reason_code: dispute.reason_code,
      statement: dispute.statement,
      evidence_ids: ["evidence-3"],
    });
    await resolveDispute(dispute, {
      resolution_action: "CONTINUE_PERFORMANCE",
      resolution_notes: "Продолжить исполнение",
      evidence_ids: ["evidence-4"],
    });
    await createLogisticsOrder(obligation, {
      carrier_member_id: logisticsOrder.carrier_member_id,
      quantity: logisticsOrder.quantity,
      origin_text: logisticsOrder.origin_text,
      destination_text: logisticsOrder.destination_text,
      pickup_due_at: logisticsOrder.pickup_due_at,
      delivery_due_at: logisticsOrder.delivery_due_at,
    });
    await transitionLogisticsOrder(logisticsOrder, "accept", []);
    await markOverdue(deal.cooperative_id, "2026-07-22T00:00:00Z");

    for (const call of fetchMock.mock.calls) {
      expect((call[1]?.headers as Headers).get("Idempotency-Key"))
        .toBe("00000000-0000-4000-8000-000000000001");
    }
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/v1/exchange/deals",
      "/api/v1/exchange/deals/deal-1/terms",
      "/api/v1/exchange/deals/deal-1/confirmations",
      "/api/v1/exchange/obligations/obligation-1/fulfillments",
      "/api/v1/exchange/fulfillments/fulfillment-1/acceptance",
      "/api/v1/exchange/obligations/obligation-1/disputes",
      "/api/v1/exchange/disputes/dispute-1/resolution",
      "/api/v1/exchange/obligations/obligation-1/logistics-orders",
      "/api/v1/exchange/logistics-orders/logistics-1/accept",
      "/api/v1/exchange/overdue-scans",
    ]);
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toMatchObject({
      expected_version: deal.version,
    });
    expect(JSON.parse(String(fetchMock.mock.calls[4]?.[1]?.body))).toMatchObject({
      expected_fulfillment_version: fulfillment.version,
      expected_obligation_version: obligation.version,
    });
    expect(JSON.parse(String(fetchMock.mock.calls[6]?.[1]?.body))).toEqual({
      resolution_action: "CONTINUE_PERFORMANCE",
      resolution_notes: "Продолжить исполнение",
      evidence_ids: ["evidence-4"],
      expected_version: dispute.version,
    });
  });
});
