import { afterEach, describe, expect, it, vi } from "vitest";

import {
  beginAntifraudReview,
  decideAntifraudSignal,
  getAntifraudOverview,
  getAntifraudScans,
  getAntifraudSignals,
  runAntifraudScan,
  type AntifraudSignal,
} from "./antifraud";

function envelope(data: unknown): Response {
  return new Response(JSON.stringify({ data }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

const signal = {
  id: "10000000-0000-4000-8000-000000000001",
  version: 2,
} as AntifraudSignal;

describe("anti-fraud API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("reads scoped overview, scans, and filtered signals", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(envelope([])));
    vi.stubGlobal("fetch", fetchMock);

    await getAntifraudOverview("coop-1");
    await getAntifraudScans("coop-1");
    await getAntifraudSignals({
      cooperativeId: "coop-1",
      status: "OPEN",
      severity: "HIGH",
    });

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/v1/antifraud/overview?cooperative_id=coop-1",
      "/api/v1/antifraud/scans?cooperative_id=coop-1",
      "/api/v1/antifraud/signals?cooperative_id=coop-1&status=OPEN&severity=HIGH",
    ]);
  });

  it("binds idempotency and optimistic versions to every command", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(envelope({
      event_id: "event-1",
      object_id: signal.id,
      replayed: false,
    })));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("crypto", {
      randomUUID: () => "00000000-0000-4000-8000-000000000006",
    });

    await runAntifraudScan("coop-1", 168);
    await beginAntifraudReview(signal);
    await decideAntifraudSignal(signal, {
      decision: "CLEARED",
      rationale: "Independent evidence supports the correction.",
      evidence_ids: ["evidence-1"],
    });

    for (const call of fetchMock.mock.calls) {
      expect((call[1]?.headers as Headers).get("Idempotency-Key"))
        .toBe("00000000-0000-4000-8000-000000000006");
    }
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toEqual({
      expected_version: 2,
    });
    expect(JSON.parse(String(fetchMock.mock.calls[2]?.[1]?.body))).toEqual({
      decision: "CLEARED",
      rationale: "Independent evidence supports the correction.",
      evidence_ids: ["evidence-1"],
      expected_version: 2,
    });
  });
});
