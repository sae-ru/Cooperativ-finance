import { afterEach, describe, expect, it, vi } from "vitest";

import {
  completeRightRedemption,
  freezeCommodityRight,
  getCommodityRights,
  getLotBalances,
  getRightProof,
  getRightRedemptions,
  issueCommodityRight,
  requestRightRedemption,
  transferCommodityRight,
  unfreezeCommodityRight,
  type CommodityRight,
  type RightRedemption,
} from "./rights";

function envelope(data: unknown, status = 200): Response {
  return new Response(JSON.stringify({ data }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const right: CommodityRight = {
  id: "right-1",
  cooperative_id: "coop-1",
  lot_id: "lot-1",
  owner_member_id: "member-1",
  original_owner_member_id: "member-1",
  quantity: "12.500",
  unit_id: "unit-1",
  status: "ISSUED",
  redeem_warehouse_id: "warehouse-1",
  valid_until: null,
  reservation_id: "reservation-1",
  issued_by_member_id: "member-2",
  issued_role_assignment_id: "role-1",
  issued_event_id: "event-1",
  frozen_previous_status: null,
  freeze_reason: null,
  frozen_event_id: null,
  redeemed_event_id: null,
  created_at: "2026-07-20T10:00:00Z",
  updated_at: "2026-07-20T10:00:00Z",
  version: 1,
};

const redemption: RightRedemption = {
  id: "redemption-1",
  right_id: right.id,
  lot_id: right.lot_id,
  owner_member_id: right.owner_member_id,
  warehouse_id: right.redeem_warehouse_id,
  custodian_assignment_id: "custodian-role-1",
  quantity: right.quantity,
  status: "REQUESTED",
  requested_by_user_id: "user-1",
  fulfilled_by_user_id: null,
  requested_event_id: "event-2",
  completed_event_id: null,
  requested_at: "2026-07-20T11:00:00Z",
  completed_at: null,
};

describe("commodity rights API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("reads registry, backing, redemptions, and direct proof endpoints", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(envelope([]))
      .mockResolvedValueOnce(envelope([]))
      .mockResolvedValueOnce(envelope([]))
      .mockResolvedValueOnce(new Response(JSON.stringify({ proof_hash: "abc" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }));
    vi.stubGlobal("fetch", fetchMock);

    await getLotBalances();
    await getCommodityRights();
    await getRightRedemptions();
    expect(await getRightProof(right.id)).toEqual({ proof_hash: "abc" });

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/v1/rights/balances",
      "/api/v1/rights",
      "/api/v1/rights/redemptions",
      "/api/v1/rights/right-1/proof",
    ]);
  });

  it("sends optimistic versions, evidence links, and idempotency keys", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(envelope({
      event_id: "event-result",
      object_id: right.id,
      replayed: false,
    })));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("crypto", {
      randomUUID: () => "00000000-0000-4000-8000-000000000001",
    });

    await issueCommodityRight({
      lot_id: right.lot_id,
      owner_member_id: right.owner_member_id,
      quantity: right.quantity,
      redeem_warehouse_id: right.redeem_warehouse_id,
      valid_until: null,
      expected_balance_version: 4,
    });
    await transferCommodityRight(right, "member-3", ["evidence-1"]);
    await freezeCommodityRight(right, "PROTECTIVE_REVIEW", "CASE-1");
    await unfreezeCommodityRight({ ...right, status: "FROZEN", version: 2 }, "CASE-2");
    await requestRightRedemption(right);
    await completeRightRedemption(redemption, { ...right, version: 2 }, ["evidence-2"]);

    const calls = fetchMock.mock.calls;
    expect(calls.map((call) => call[0])).toEqual([
      "/api/v1/rights",
      "/api/v1/rights/right-1/transfer",
      "/api/v1/rights/right-1/freeze",
      "/api/v1/rights/right-1/unfreeze",
      "/api/v1/rights/right-1/redemptions",
      "/api/v1/rights/redemptions/redemption-1/complete",
    ]);
    for (const call of calls) {
      expect((call[1]?.headers as Headers).get("Idempotency-Key"))
        .toBe("00000000-0000-4000-8000-000000000001");
    }
    expect(JSON.parse(String(calls[1]?.[1]?.body))).toEqual({
      from_member_id: "member-1",
      to_member_id: "member-3",
      evidence_ids: ["evidence-1"],
      expected_version: 1,
    });
    expect(JSON.parse(String(calls[5]?.[1]?.body))).toEqual({
      evidence_ids: ["evidence-2"],
      expected_right_version: 2,
    });
  });
});
