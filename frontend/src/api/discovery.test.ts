import { afterEach, describe, expect, it, vi } from "vitest";

import {
  cancelPurchase,
  commitPurchase,
  createPurchaseIntent,
  getMyLogisticsQuotes,
  getMyOffers,
  getPurchaseIntents,
  getReservationReceipts,
  publishLogisticsQuote,
  publishOffer,
  reservePurchase,
  revokeOffer,
  searchCatalog,
  verifyOffer,
  type FederatedOffer,
  type LogisticsQuoteDraft,
  type OfferDraft,
  type PurchaseIntent,
  type SearchCandidate,
} from "./discovery";

describe("discovery API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("posts explicit search mode and exact decimal strings", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ data: [], mode: "INDEXED", peer_statuses: [], ranking_version: "LANDED_COST_V1", request_id: "request-1" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(searchCatalog({
      mode: "INDEXED",
      product_code: "CABBAGE.WHITE",
      quantity: "10.000",
      unit_code: "KG",
      valuation_unit: "COOP",
      destination_region: "EAST-DISTRICT",
      maximum_age_seconds: 604800,
      trusted_node_codes: [],
      required_certificates: [],
      quality_minimum: "A",
      maximum_goods_cost: "50.00",
      maximum_landed_cost: "100.00",
      latest_delivery: null,
      top_k: 20,
    })).resolves.toEqual({
      data: [],
      mode: "INDEXED",
      peer_statuses: [],
      ranking_version: "LANDED_COST_V1",
      request_id: "request-1",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/federation/catalog/search",
      expect.objectContaining({
        method: "POST",
        body: expect.stringContaining('"quantity":"10.000"'),
      }),
    );
  });

  it("adds an idempotency key to reservation commands", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({
        data: { event_id: "event-1", object_id: "receipt-1", replayed: false },
      }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await reservePurchase("intent-1", "goods");

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/v1/federation/purchase-intents/intent-1/reserve-goods",
    );
    expect(new Headers(init.headers).get("Idempotency-Key")).toBeTruthy();
  });

  it("reuses the original saga versions while commit and cancellation recover", async () => {
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify({
          data: { event_id: "event-2", object_id: "intent-1", replayed: false },
        }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const intent = {
      id: "intent-1",
      summary_hash: "sha256:summary",
      version: 7,
      commit_expected_version: 4,
      cancellation_expected_version: 5,
    } as PurchaseIntent;

    await commitPurchase(intent);
    await cancelPurchase(intent, "operator recovery");
    await commitPurchase({ ...intent, commit_expected_version: null });
    await cancelPurchase({ ...intent, cancellation_expected_version: null }, "cancel");

    const bodies = fetchMock.mock.calls.map((call) => JSON.parse(String(call[1]?.body)));
    expect(bodies).toEqual([
      { summary_hash: "sha256:summary", expected_version: 4 },
      { reason: "operator recovery", expected_version: 5 },
      { summary_hash: "sha256:summary", expected_version: 7 },
      { reason: "cancel", expected_version: 7 },
    ]);
    expect(
      fetchMock.mock.calls.every((call) =>
        Boolean(new Headers(call[1]?.headers).get("Idempotency-Key")),
      ),
    ).toBe(true);
  });
  it("covers offer, logistics, verification, intent, receipt, and revoke contracts", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(
      new Response(JSON.stringify({ data: { event_id: "event-1", object_id: "object-1", replayed: false } }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ));
    vi.stubGlobal("fetch", fetchMock);
    const offerDraft = {
      kind: "PRODUCT",
      product_code: "NAILS.100MM",
      description: "Steel nails",
      quantity_available: "100",
      unit_code: "PCS",
      minimum_batch: "10",
      origin_region: "NORTH",
      pickup_address_text: "Farm road 1",
      pickup_contact_name: "Seller",
      pickup_contact_phone: "+10000000000",
      pickup_instructions: "",
      unit_price: "0.10",
      available_until: "2026-08-01T00:00:00Z",
      image_evidence_id: null,
    } satisfies OfferDraft;
    const logisticsDraft = {
      offer_record_id: "offer-record-1",
      destination_region: "SOUTH",
      capacity: "100",
      transport_cost: "5.00",
      handling_cost: "0",
      delivery_from: "2026-07-29T00:00:00Z",
      delivery_until: "2026-07-30T00:00:00Z",
      liability_limit: "50.00",
      valid_until: "2026-07-28T00:00:00Z",
    } satisfies LogisticsQuoteDraft;
    const offer = { record_id: "offer-record-1", offer_id: "offer-1", offer_version: 2 } as FederatedOffer;
    const candidate = {
      offer,
      quote: { record_id: "quote-record-1", destination_region: "SOUTH" },
      landed_cost: "15.00",
    } as SearchCandidate;

    await publishOffer(offerDraft, "member-1");
    await publishOffer({ ...offerDraft, unit_code: "KG", pickup_instructions: "Call first" }, "member-1");
    await publishLogisticsQuote(logisticsDraft, "carrier-1");
    await publishLogisticsQuote({ ...logisticsDraft, handling_cost: "2.00" }, "carrier-1");
    await getMyLogisticsQuotes();
    await verifyOffer(offer.record_id);
    await getPurchaseIntents();
    await getReservationReceipts("intent-1");
    expect(() => createPurchaseIntent({ ...candidate, quote: null }, "10", { address_text: "Road 2", contact_name: "Buyer", contact_phone: "+1222", instructions: "" }))
      .toThrow("LOGISTICS_QUOTE_REQUIRED");
    await createPurchaseIntent(candidate, "10", { address_text: "Road 2", contact_name: "Buyer", contact_phone: "+1222", instructions: "Gate 3" });
    await getMyOffers();
    await revokeOffer(offer, "Sold out");

    expect(fetchMock).toHaveBeenCalledTimes(11);
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body)).unit_scale).toBe(0);
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body)).unit_scale).toBe(3);
    expect(JSON.parse(String(fetchMock.mock.calls[2]?.[1]?.body)).cost_components).toEqual({ transport: "5.00" });
    expect(JSON.parse(String(fetchMock.mock.calls[3]?.[1]?.body)).cost_components).toEqual({ transport: "5.00", handling: "2.00" });
    expect(fetchMock.mock.calls.map((call) => call[0])).toContain("/api/v1/federation/offers/revoke");
  });
});
