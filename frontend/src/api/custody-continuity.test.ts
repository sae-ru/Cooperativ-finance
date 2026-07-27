import { afterEach, describe, expect, it, vi } from "vitest";

import {
  type CustodyContinuityCase,
  type CustodyContinuityItem,
  attestCustodyContinuityItem,
  decideCustodyContinuity,
  decideCustodyContinuityCandidate,
  getCustodyContinuityCandidates,
  getCustodyContinuityCases,
  getCustodyContinuitySources,
  requestCustodyContinuity,
} from "./custody-continuity";

function envelope(data: unknown): Response {
  return new Response(JSON.stringify({ data }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("custody continuity API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("binds case and lot versions to every personal command", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(
      envelope({
        event_id: "event-1",
        object_id: "case-1",
        status: "INVENTORY_PENDING",
        replayed: false,
      }),
    ));
    vi.stubGlobal("fetch", fetchMock);
    const continuityCase = {
      id: "case-1",
      version: 4,
    } as CustodyContinuityCase;
    const item = { id: "item-1", version: 3 } as CustodyContinuityItem;

    await getCustodyContinuityCases();
    await getCustodyContinuitySources();
    await getCustodyContinuityCandidates("coop 1", "warehouse/1");
    await requestCustodyContinuity({
      member_continuity_case_id: "member-case-1",
      source_assignment_id: "assignment-1",
      expected_source_assignment_version: 7,
      target_role_assignment_id: "role-2",
      handover_place: "Receiving desk",
      temporary_valid_until: "2026-07-29T10:00:00Z",
      evidence_refs: ["case:notice-1"],
    });
    await attestCustodyContinuityItem(continuityCase, item, {
      actual_quantity: "25",
      condition_notes: "Matches",
      evidence_ids: ["evidence-1"],
    });
    await decideCustodyContinuity(
      continuityCase,
      true,
      "INDEPENDENT_INVENTORY_REVIEW",
    );
    await decideCustodyContinuityCandidate(
      continuityCase,
      true,
      ["evidence-2"],
    );

    expect(fetchMock.mock.calls.slice(0, 3).map((call) => call[0])).toEqual([
      "/api/v1/inventory/custody-continuity-cases",
      "/api/v1/inventory/custody-continuity-sources",
      "/api/v1/inventory/custody-continuity-candidates?cooperative_id=coop%201&warehouse_id=warehouse%2F1",
    ]);
    expect(JSON.parse(String(fetchMock.mock.calls[3]?.[1]?.body))).toMatchObject({
      expected_source_assignment_version: 7,
      target_role_assignment_id: "role-2",
    });
    expect(JSON.parse(String(fetchMock.mock.calls[4]?.[1]?.body))).toMatchObject({
      expected_case_version: 4,
      expected_item_version: 3,
      actual_quantity: "25",
    });
    expect(JSON.parse(String(fetchMock.mock.calls[5]?.[1]?.body))).toEqual({
      approve: true,
      expected_version: 4,
      reason_code: "INDEPENDENT_INVENTORY_REVIEW",
    });
    expect(JSON.parse(String(fetchMock.mock.calls[6]?.[1]?.body))).toEqual({
      accept: true,
      expected_version: 4,
      evidence_ids: ["evidence-2"],
      reason_code: "PERSONAL_ACCEPTANCE",
    });
  });
});
