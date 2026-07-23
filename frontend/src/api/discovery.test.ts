import { afterEach, describe, expect, it, vi } from "vitest";

import {
  cancelPurchase,
  commitPurchase,
  reservePurchase,
  searchCatalog,
  type PurchaseIntent,
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
});
