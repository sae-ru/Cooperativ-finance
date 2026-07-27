import { afterEach, describe, expect, it, vi } from "vitest";

import {
  approveTrustPolicy,
  closeRehabilitationPlan,
  completeRehabilitationStep,
  createRehabilitationPlan,
  decideTrustAppeal,
  declareTrustConflict,
  finalizeTrustSanction,
  getArbitratorWorkspace,
  getAuditorWorkspace,
  getProtectiveMeasures,
  getRehabilitationPlans,
  getRehabilitationSteps,
  getReliabilityProfile,
  getReputationEvents,
  getTrustAppeals,
  getTrustCases,
  getTrustConflicts,
  getTrustDecisions,
  getTrustPolicies,
  getTrustSanctions,
  imposeProtectiveMeasure,
  issueOriginalDecision,
  liftProtectiveMeasure,
  markTrustCaseReady,
  openTrustCase,
  proposeTrustPolicy,
  proposeTrustSanction,
  recordReputationEvent,
  respondToTrustCase,
  submitTrustAppeal,
  type ProtectiveMeasure,
  type RehabilitationPlan,
  type TrustAppeal,
  type TrustCase,
  type TrustPolicy,
  type TrustSanction,
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
  it("covers every trust read and command contract with explicit versions", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(envelope({ event_id: "event-1", object_id: "object-1", replayed: false })));
    vi.stubGlobal("fetch", fetchMock);
    const policy = { id: "policy-1", version: 2 } as TrustPolicy;
    const item = { id: "case-1", version: 3 } as TrustCase;
    const measure = { id: "measure-1", version: 4 } as ProtectiveMeasure;
    const sanction = { id: "sanction-1", version: 5 } as TrustSanction;
    const plan = { id: "plan-1", version: 6 } as RehabilitationPlan;

    await getTrustConflicts(item.id);
    await getProtectiveMeasures(item.id);
    await getTrustDecisions(item.id);
    await getReliabilityProfile("member-1");
    await getRehabilitationSteps(plan.id);
    await proposeTrustPolicy({ cooperative_id: "coop-1", semantic_version: "1.0", appeal_window_seconds: 3600, max_protective_seconds: 7200, panel_quorum: 2, terms: {} });
    await approveTrustPolicy(policy);
    await openTrustCase({ cooperative_id: "coop-1", case_reference: "CASE-1", subject_member_id: "member-1", claimant_member_id: "member-2", source_type: "DEAL", source_reference: "deal-1", source_event_ids: ["event-0"], evidence_ids: ["evidence-1"], summary: "Summary", facts: "Facts", requested_outcome: "Repair", confidentiality: "NORMAL" });
    await respondToTrustCase(item, "Response", ["evidence-2"]);
    await markTrustCaseReady(item, "Reviewed");
    await declareTrustConflict(item.id, "ORIGINAL", "CLEAR", "No conflict");
    await declareTrustConflict(item.id, "APPEAL", "CONFLICT", "Prior relationship");
    await imposeProtectiveMeasure(item, { measure_type: "LIMIT", scope: {}, rationale: "Risk", expires_at: "2026-08-01T00:00:00Z", review_at: "2026-07-30T00:00:00Z" });
    await liftProtectiveMeasure(measure, "Resolved");
    await issueOriginalDecision(item, { outcome: "SUBSTANTIATED", standard_of_proof: "VERIFIED", fault_class: "NEGLIGENCE", causal_findings: {}, established_loss: "10", reasoning: "Evidence", consequence_spec: {}, evidence_ids: ["evidence-3"] });
    await proposeTrustSanction("decision-1", { measure_type: "LIMIT", severity: "LOW", scope: {}, rationale: "Proportionate", starts_at: "2026-07-27T00:00:00Z", expires_at: null, review_at: null });
    await recordReputationEvent("decision-1", { context: "DELIVERY", classification: "BREACH", severity: 1, confidence: "HIGH", observation_start: "2026-07-01T00:00:00Z", observation_end: "2026-07-02T00:00:00Z", source_event_ids: ["event-1"], evidence_ids: ["evidence-4"], visibility: "MEMBERS" });
    await submitTrustAppeal(item, "decision-1", sanction.id, "New evidence", ["evidence-5"]);
    await finalizeTrustSanction(sanction);
    await createRehabilitationPlan("decision-1", { title: "Repair trust", completion_criteria: {}, starts_at: "2026-07-27T00:00:00Z", due_at: "2026-08-27T00:00:00Z", steps: [{ description: "Complete delivery", completion_criterion: "Accepted" }] });
    await completeRehabilitationStep(plan, "step-1", ["evidence-6"]);
    await closeRehabilitationPlan(plan, "DELIVERY", "Completed");

    const calls = fetchMock.mock.calls;
    expect(calls).toHaveLength(22);
    expect(calls[10]?.[0]).toBe("/api/v1/trust/cases/case-1/conflicts");
    expect(JSON.parse(String(calls[10]?.[1]?.body)).relationship).toBe("No declared relationship");
    expect(JSON.parse(String(calls[11]?.[1]?.body)).relationship).toBe("Declared relationship");
    expect(JSON.parse(String(calls[21]?.[1]?.body))).toMatchObject({ expected_version: 6, context: "DELIVERY" });
  });
});
