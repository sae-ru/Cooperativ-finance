import { afterEach, describe, expect, it, vi } from "vitest";

import {
  getInventoryCustodians,
  getInventoryMembers,
  getLots,
  getProducts,
  getReceiptAct,
  getUnits,
  getWarehouses,
  registerLot,
  uploadEvidence,
} from "./inventory";

function envelope(data: unknown, status = 200): Response {
  return new Response(JSON.stringify({ data }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("inventory API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("uses inventory collection and exact quantity command contracts", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(envelope([])));
    vi.stubGlobal("fetch", fetchMock);

    await getUnits();
    await getProducts();
    await getWarehouses();
    await getInventoryMembers();
    await getInventoryCustodians();
    await getLots();
    await registerLot({
      cooperative_id: "coop-1",
      lot_number: "LOT-1",
      product_id: "product-1",
      warehouse_id: "warehouse-1",
      owner_member_id: "member-1",
      declared_quantity: "10.125",
      unit_id: "unit-1",
      declared_quality: "Grade A",
      expires_at: null,
      storage_conditions: "Dry",
      custodian_assignment_id: "assignment-1",
      evidence_ids: ["evidence-1"],
    });

    expect(fetchMock.mock.calls.slice(0, 6).map((call) => call[0])).toEqual([
      "/api/v1/units",
      "/api/v1/products",
      "/api/v1/warehouses",
      "/api/v1/inventory/members",
      "/api/v1/inventory/custodians",
      "/api/v1/inventory/lots",
    ]);
    const command = fetchMock.mock.calls[6]?.[1] as RequestInit;
    expect(command.method).toBe("POST");
    expect((command.headers as Headers).get("Idempotency-Key")).toMatch(/^[0-9a-f-]{36}$/);
    expect(JSON.parse(String(command.body))).toMatchObject({ declared_quantity: "10.125" });
  });

  it("keeps receipt acts direct and uploads binary evidence with its MIME type", async () => {
    const act = { lot: { lot_number: "LOT-1" }, signed_events: [] };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(act), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(envelope({ object_id: "evidence-1", event_id: "event-1", replayed: false }, 201))
      .mockResolvedValueOnce(envelope({ object_id: "evidence-1", event_id: "event-2", replayed: false }));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("crypto", {
      randomUUID: () => "00000000-0000-4000-8000-000000000001",
      subtle: { digest: vi.fn().mockResolvedValue(new Uint8Array(32).buffer) },
    });

    expect(await getReceiptAct("lot-1")).toEqual(act);
    const file = new File(["proof"], "proof.txt", { type: "text/plain" });
    Object.defineProperty(file, "arrayBuffer", {
      value: async () => new TextEncoder().encode("proof").buffer,
    });
    expect(await uploadEvidence("coop-1", file, "RECEIPT")).toBe("evidence-1");

    const intent = JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body));
    expect(intent).toMatchObject({
      expected_sha256: "0".repeat(64),
      expected_size: 5,
      mime_type: "text/plain",
    });
    const upload = fetchMock.mock.calls[2]?.[1] as RequestInit;
    expect((upload.headers as Headers).get("Content-Type")).toBe("text/plain");
    expect(upload.body).toBe(file);
  });
});
