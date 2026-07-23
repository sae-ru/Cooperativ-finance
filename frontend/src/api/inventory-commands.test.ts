import { afterEach, describe, expect, it, vi } from "vitest";

import {
  acceptCustody,
  attestLot,
  createProduct,
  createUnit,
  createWarehouse,
  downloadEvidence,
  getCustodyTransfers,
  getDiscrepancies,
  offerCustody,
  recordDiscrepancy,
  type CustodyTransfer,
  type InventoryLot,
} from "./inventory";

function envelope(data: unknown): Response {
  return new Response(JSON.stringify({ data }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("inventory command API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("binds optimistic versions and evidence to every physical command", async () => {
    const fetchMock = vi.fn().mockImplementation((path: string) => Promise.resolve(
      path.endsWith("/content")
        ? new Response("proof", { status: 200, headers: { "Content-Type": "text/plain" } })
        : envelope({ event_id: "event-1", object_id: "object-1", replayed: false }),
    ));
    vi.stubGlobal("fetch", fetchMock);
    const lot = { id: "lot-1", version: 7 } as InventoryLot;
    const transfer = { id: "transfer-1" } as CustodyTransfer;

    await getDiscrepancies();
    await getCustodyTransfers();
    await createUnit({ cooperative_id: "coop-1", code: "KG", name: "Kilogram", symbol: "kg", dimension: "MASS", decimal_scale: 3 });
    await createProduct({ cooperative_id: "coop-1", sku: "SKU-1", name: "Product", description: "Description", default_unit_id: "unit-1", quantity_tolerance: "0.010", requires_evidence: true, shelf_life_required: false });
    await createWarehouse({ cooperative_id: "coop-1", code: "WH-1", name: "Warehouse", address_text: "Address", storage_conditions: "Dry" });
    await attestLot(lot, { measured_quantity: "10.000", quality_decision: "ACCEPTED", verified_quality: "Grade A", measurements: { temperature: "4" }, notes: "Checked", evidence_ids: ["evidence-1"] });
    await recordDiscrepancy(lot, { actual_quantity: "9.500", reason_code: "COUNT", notes: "Counted", evidence_ids: ["evidence-2"] });
    await offerCustody(lot, { to_warehouse_id: "warehouse-2", to_assignment_id: "assignment-2", place: "Gate", notes: "Sealed", evidence_ids: ["evidence-3"] });
    await acceptCustody(transfer, lot, ["evidence-4"]);
    expect(await (await downloadEvidence("evidence-4")).text()).toBe("proof");

    expect(fetchMock.mock.calls.slice(0, 5).map((call) => call[0])).toEqual([
      "/api/v1/inventory/discrepancies",
      "/api/v1/inventory/custody-transfers",
      "/api/v1/units",
      "/api/v1/products",
      "/api/v1/warehouses",
    ]);
    expect(JSON.parse(String(fetchMock.mock.calls[5]?.[1]?.body))).toMatchObject({ expected_version: 7, measured_quantity: "10.000" });
    expect(JSON.parse(String(fetchMock.mock.calls[6]?.[1]?.body))).toMatchObject({ expected_version: 7, actual_quantity: "9.500" });
    expect(JSON.parse(String(fetchMock.mock.calls[7]?.[1]?.body))).toMatchObject({ expected_version: 7, to_assignment_id: "assignment-2" });
    expect(JSON.parse(String(fetchMock.mock.calls[8]?.[1]?.body))).toEqual({ evidence_ids: ["evidence-4"], expected_lot_version: 7 });
    expect(fetchMock.mock.calls[9]?.[0]).toBe("/api/v1/evidence/evidence-4/content");
  });
});
