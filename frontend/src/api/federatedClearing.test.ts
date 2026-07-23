import { afterEach, describe, expect, it, vi } from "vitest";

import {
  approveLocalFederatedCycle,
  collectFederatedApprovals,
  collectFederatedSnapshots,
  commitFederatedCycle,
  createFederatedClearingCycle,
  createFederatedClearingPolicy,
  createInterNodeObligation,
  getFederatedClearingCycles,
  getFederatedClearingPolicies,
  getFederatedCycleEvidence,
  getInterNodeObligations,
  prepareFederatedCycle,
  publishFederatedProposal,
  recoverFederatedCycle,
  releaseFederatedCycle,
} from "./federatedClearing";

function envelope(data: unknown): Response {
  return new Response(JSON.stringify({ data }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("federated clearing API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("reads the operator evidence surface", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(envelope([])));
    vi.stubGlobal("fetch", fetchMock);

    await getFederatedClearingPolicies();
    await getInterNodeObligations();
    await getFederatedClearingCycles();
    await getFederatedCycleEvidence("cycle-1");

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/v1/federated-clearing/policies",
      "/api/v1/federated-clearing/obligations",
      "/api/v1/federated-clearing/cycles",
      "/api/v1/federated-clearing/cycles/cycle-1",
    ]);
  });

  it("sends idempotent commands to the exact lifecycle endpoints", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(envelope({
      cycle_id: "cycle-1",
      object_id: "cycle-1",
      event_id: "event-1",
      status: "DRAFT",
      replayed: false,
      nodes: [],
    })));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("crypto", { randomUUID: () => "00000000-0000-4000-8000-000000000099" });

    await createFederatedClearingPolicy({
      policy_code: "REGIONAL-WEEKLY",
      policy_version: 1,
      valuation_unit: "DEMO",
      decimal_scale: 2,
      rounding_mode: "DOWN",
      minimum_operation: "0.01",
      max_iterations: 10000,
      max_cycle_length: 8,
      prepare_ttl_seconds: 900,
    });
    await createInterNodeObligation({
      debtor_node_code: "node-a",
      creditor_node_code: "node-b",
      unit_code: "DEMO",
      amount: "40.00",
      source_reference: "SUPPLY-1",
      source_event_hash: `sha256:${"a".repeat(64)}`,
      liquidity_class: "STANDARD",
    });
    await createFederatedClearingCycle({
      cycle_code: "REGION-WEEK-1",
      policy_id: "policy-1",
      period_start: "2035-01-01T00:00:00Z",
      period_end: "2035-01-08T00:00:00Z",
      participant_node_codes: ["node-a", "node-b"],
    });
    await collectFederatedSnapshots("cycle-1");
    await prepareFederatedCycle("cycle-1");
    await publishFederatedProposal("cycle-1");
    await collectFederatedApprovals("cycle-1");
    await approveLocalFederatedCycle("cycle-1");
    await commitFederatedCycle("cycle-1");
    await recoverFederatedCycle("cycle-1");
    await releaseFederatedCycle("cycle-1", true);

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/v1/federated-clearing/policies",
      "/api/v1/federated-clearing/obligations",
      "/api/v1/federated-clearing/cycles",
      "/api/v1/federated-clearing/cycles/cycle-1/snapshots/collect",
      "/api/v1/federated-clearing/cycles/cycle-1/prepare",
      "/api/v1/federated-clearing/cycles/cycle-1/proposal",
      "/api/v1/federated-clearing/cycles/cycle-1/approvals/collect",
      "/api/v1/federated-clearing/cycles/cycle-1/approvals/local",
      "/api/v1/federated-clearing/cycles/cycle-1/commit",
      "/api/v1/federated-clearing/cycles/cycle-1/recover",
      "/api/v1/federated-clearing/cycles/cycle-1/release",
    ]);
    for (const [, init] of fetchMock.mock.calls) {
      expect(new Headers((init as RequestInit).headers).get("Idempotency-Key"))
        .toBe("00000000-0000-4000-8000-000000000099");
    }
    expect(JSON.parse(String((fetchMock.mock.calls[10]![1] as RequestInit).body)))
      .toEqual({ expired: true });
  });
});
