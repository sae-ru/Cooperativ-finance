import { afterEach, describe, expect, it, vi } from "vitest";

import {
  approveClearingPreview,
  collectClearingCycle,
  createClearingCycle,
  finalizeClearingCycle,
  freezeClearingInput,
  getClearingAccountingExport,
  getClearingApprovals,
  getClearingCycles,
  getClearingDisputes,
  getClearingEntries,
  getClearingInput,
  getClearingPolicies,
  getClearingPositions,
  getClearingProof,
  getClearingStatements,
  markClearingReady,
  previewClearingCycle,
  reconcileClearingCycle,
  verifyClearingProof,
  type ClearingCycle,
} from "./clearing";

function envelope(data: unknown): Response {
  return new Response(JSON.stringify({ data }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

const cycle = {
  id: "cycle-1",
  cooperative_id: "coop-1",
  policy_id: "policy-1",
  cycle_code: "WEEK-1",
  status: "PREVIEWED",
  input_hash: `sha256:${"a".repeat(64)}`,
  result_hash: `sha256:${"b".repeat(64)}`,
  version: 4,
} as ClearingCycle;

describe("local clearing API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("reads the complete cycle evidence surface", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(envelope([])));
    vi.stubGlobal("fetch", fetchMock);

    await getClearingPolicies();
    await getClearingCycles();
    await getClearingInput(cycle.id);
    await getClearingEntries(cycle.id);
    await getClearingPositions(cycle.id);
    await getClearingApprovals(cycle.id);
    await getClearingDisputes(cycle.id);
    await getClearingProof(cycle.id);
    await getClearingStatements(cycle.id, "member-1");
    await getClearingAccountingExport(cycle.id);

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/v1/clearing/policies",
      "/api/v1/clearing/cycles",
      "/api/v1/clearing/cycles/cycle-1/input",
      "/api/v1/clearing/cycles/cycle-1/entries",
      "/api/v1/clearing/cycles/cycle-1/positions",
      "/api/v1/clearing/cycles/cycle-1/approvals",
      "/api/v1/clearing/cycles/cycle-1/disputes",
      "/api/v1/clearing/cycles/cycle-1/proof",
      "/api/v1/clearing/cycles/cycle-1/statements/member-1",
      "/api/v1/clearing/cycles/cycle-1/accounting-export",
    ]);
  });

  it("sends cycle versions and exact approved hashes", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(envelope({
      event_id: "event-1",
      object_id: cycle.id,
      replayed: false,
    })));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("crypto", { randomUUID: () => "00000000-0000-4000-8000-000000000099" });

    await createClearingCycle({
      cooperative_id: cycle.cooperative_id,
      policy_id: cycle.policy_id,
      cycle_code: cycle.cycle_code,
      period_start: "2026-07-01T00:00:00Z",
      period_end: "2026-07-08T00:00:00Z",
    });
    await collectClearingCycle(cycle);
    await approveClearingPreview(cycle);
    await finalizeClearingCycle(cycle);
    await verifyClearingProof({ proof_hash: `sha256:${"c".repeat(64)}` });
    await freezeClearingInput(cycle);
    await previewClearingCycle(cycle);
    await markClearingReady(cycle);
    await reconcileClearingCycle(cycle);

    const calls = fetchMock.mock.calls;
    expect(JSON.parse(String((calls[1]![1] as RequestInit).body))).toEqual({
      expected_version: 4,
    });
    expect(JSON.parse(String((calls[2]![1] as RequestInit).body))).toEqual({
      expected_version: 4,
      input_hash: cycle.input_hash,
      result_hash: cycle.result_hash,
    });
    expect(JSON.parse(String((calls[3]![1] as RequestInit).body))).toEqual({
      expected_version: 4,
      result_hash: cycle.result_hash,
    });
    expect(new Headers((calls[3]![1] as RequestInit).headers).get("Idempotency-Key"))
      .toBe("00000000-0000-4000-8000-000000000099");
  });
});
