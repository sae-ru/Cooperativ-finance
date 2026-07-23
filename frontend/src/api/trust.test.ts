import { afterEach, describe, expect, it, vi } from "vitest";

import {
  decideTrustAppeal,
  getArbitratorWorkspace,
  getAuditorWorkspace,
  getRehabilitationPlans,
  getReputationEvents,
  getTrustAppeals,
  getTrustCases,
  getTrustPolicies,
  getTrustSanctions,
  type TrustAppeal,
} from "./trust";

function envelope(data: unknown): Response {
  return new Response(JSON.stringify({ data }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("trust API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("reads cases, consequences, reputation, and both role workspaces", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(envelope([])));
    vi.stubGlobal("fetch", fetchMock);

    await getTrustPolicies();
    await getTrustCases();
    await getTrustAppeals();
    await getTrustSanctions();
    await getReputationEvents();
    await getRehabilitationPlans();
    await getArbitratorWorkspace();
    await getAuditorWorkspace();

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/v1/trust/policies",
      "/api/v1/trust/cases",
      "/api/v1/trust/appeals",
      "/api/v1/trust/sanctions",
      "/api/v1/trust/reputation/events",
      "/api/v1/trust/rehabilitation-plans",
      "/api/v1/trust/workspaces/arbitrator",
      "/api/v1/trust/workspaces/auditor",
    ]);
  });

  it("sends the current case version and independent appeal outcome", async () => {
    const fetchMock = vi.fn().mockResolvedValue(envelope({
      event_id: "event-1",
      object_id: "decision-2",
      replayed: false,
    }));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("crypto", { randomUUID: () => "00000000-0000-4000-8000-000000000099" });
    const appeal = { id: "appeal-1" } as TrustAppeal;

    await decideTrustAppeal(appeal, 6, {
      outcome: "OVERTURNED",
      reasoning: "The original timestamp was wrong.",
      evidence_ids: ["evidence-1"],
    });

    const init = fetchMock.mock.calls[0]![1] as RequestInit;
    expect(JSON.parse(String(init.body))).toMatchObject({
      expected_case_version: 6,
      outcome: "OVERTURNED",
      evidence_ids: ["evidence-1"],
    });
    expect(new Headers(init.headers).get("Idempotency-Key"))
      .toBe("00000000-0000-4000-8000-000000000099");
  });
});
