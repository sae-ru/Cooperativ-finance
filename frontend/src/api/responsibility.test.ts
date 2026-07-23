import { afterEach, describe, expect, it, vi } from "vitest";

import {
  acceptResponsibility,
  decideResponsibility,
  getJournalIntegrity,
  getOutboxStatus,
  getResponsibilityAssignments,
  getResponsibilityCandidates,
  getSignedEvents,
  previewResponsibility,
  proposeResponsibility,
  type ResponsibilityAssignment,
  type ResponsibilityProposal,
} from "./responsibility";

const cooperativeId = "30000000-0000-0000-0000-000000000001";
const assignmentId = "60000000-0000-0000-0000-000000000001";

function response(data: unknown): Response {
  return new Response(JSON.stringify({ data }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("responsibility API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("uses scoped query and idempotent command contracts", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(response({ ok: true })));
    vi.stubGlobal("fetch", fetchMock);
    const proposal: ResponsibilityProposal = {
      cooperative_id: cooperativeId,
      member_id: "40000000-0000-0000-0000-000000000001",
      role_assignment_id: "50000000-0000-0000-0000-000000000001",
      subject_type: "warehouse_zone",
      subject_id: "70000000-0000-0000-0000-000000000001",
      scope: "Custody",
      max_exposure: "10.0000",
      exposure_unit: "SHARE_UNIT",
      valid_until: null,
    };
    const assignment = {
      id: assignmentId,
      version: 2,
    } as ResponsibilityAssignment;

    await getResponsibilityCandidates(cooperativeId);
    await getResponsibilityAssignments();
    await previewResponsibility(proposal);
    await proposeResponsibility({ ...proposal, expected_summary_hash: `sha256:${"a".repeat(64)}` });
    await decideResponsibility(assignmentId, true);
    await decideResponsibility(assignmentId, false);
    await acceptResponsibility(assignment);
    await getSignedEvents();
    await getJournalIntegrity();
    await getOutboxStatus();

    expect(fetchMock).toHaveBeenCalledTimes(10);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      `/api/v1/responsibility/candidates?cooperative_id=${cooperativeId}`,
    );
    expect(fetchMock.mock.calls[1]?.[0]).toContain("/api/v1/responsibility/assignments");
    expect(fetchMock.mock.calls[2]?.[1]).toMatchObject({ method: "POST" });
    const proposalHeaders = fetchMock.mock.calls[3]?.[1]?.headers as Headers;
    expect(proposalHeaders.get("Idempotency-Key")).toMatch(/^[0-9a-f-]{36}$/);
    expect(JSON.parse(String(fetchMock.mock.calls[4]?.[1]?.body))).toMatchObject({
      decision: "APPROVE",
    });
    expect(JSON.parse(String(fetchMock.mock.calls[5]?.[1]?.body))).toMatchObject({
      decision: "REJECT",
    });
    expect(JSON.parse(String(fetchMock.mock.calls[6]?.[1]?.body))).toEqual({ expected_version: 2 });
    expect(fetchMock.mock.calls.slice(7).map((call) => call[0])).toEqual([
      "/api/v1/journal/events?limit=200",
      "/api/v1/journal/integrity",
      "/api/v1/journal/outbox",
    ]);
  });
});
