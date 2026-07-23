import { afterEach, describe, expect, it, vi } from "vitest";

import {
  approveAllocation,
  getAidApplications,
  getAllocations,
  getCampaignReports,
  getCampaigns,
  getComplaints,
  getContributions,
  getFunds,
  type Allocation,
} from "./solidarity";

function envelope(data: unknown): Response {
  return new Response(JSON.stringify({ data }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("solidarity API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("reads public aggregates and privacy-scoped operational records", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(envelope([])));
    vi.stubGlobal("fetch", fetchMock);

    await getFunds();
    await getCampaigns();
    await getContributions("campaign-1");
    await getAidApplications("campaign-1");
    await getAllocations("campaign-1");
    await getComplaints("campaign-1");
    await getCampaignReports("campaign-1");

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/v1/solidarity/funds",
      "/api/v1/solidarity/campaigns",
      "/api/v1/solidarity/contributions?campaign_id=campaign-1",
      "/api/v1/solidarity/applications?campaign_id=campaign-1",
      "/api/v1/solidarity/allocations?campaign_id=campaign-1",
      "/api/v1/solidarity/complaints?campaign_id=campaign-1",
      "/api/v1/solidarity/reports?campaign_id=campaign-1",
    ]);
  });

  it("binds allocation approval to its version and immutable hash", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      envelope({ event_id: "event-1", object_id: "allocation-1", replayed: false }),
    );
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("crypto", { randomUUID: () => "00000000-0000-4000-8000-000000000099" });
    const allocation = {
      id: "allocation-1",
      version: 3,
      allocation_hash: `sha256:${"a".repeat(64)}`,
    } as Allocation;

    await approveAllocation(allocation, true, "No conflict declared.");

    const init = fetchMock.mock.calls[0]![1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({
      expected_version: 3,
      allocation_hash: allocation.allocation_hash,
      approved: true,
      conflict_statement: "No conflict declared.",
    });
    expect(new Headers(init.headers).get("Idempotency-Key"))
      .toBe("00000000-0000-4000-8000-000000000099");
  });
});
