import { afterEach, describe, expect, it, vi } from "vitest";

import {
  activateCrisisMandate, approveRationingRule, approveReserveTarget, cancelRationingPlan,
  closeCrisisMandate, confirmRationingPlan, expireCrisisMandate, getCrisisControllerWorkspace,
  getCrisisMandates, getCrisisOperatorWorkspace, getCrisisPaperForms, getCrisisReports,
  getCrisisReviews, getRationingAllocations, getRationingPlans, getRationingRules,
  getRationIssuances, getReserveSnapshots, getReserveTargets, issueCrisisPaperForm,
  issueRation, previewRationingPlan, proposeCrisisMandate, proposeRationingRule,
  proposeReserveTarget, recordCrisisPaperForm, recordReserveSnapshot, reviewCrisisMandate,
  type CrisisMandate, type CrisisPaperForm, type RationingPlan, type RationingRule,
  type ReserveTarget,
} from "./crisis";

function envelope(data: unknown): Response {
  return new Response(JSON.stringify({ data }), { status: 200, headers: { "Content-Type": "application/json" } });
}

describe("crisis command API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("maps the bounded crisis lifecycle to idempotent versioned endpoints", async () => {
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(envelope({ event_id: "event-1", object_id: "object-1", replayed: false })),
    );
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("crypto", { randomUUID: () => "00000000-0000-4000-8000-000000000123" });
    const target = { id: "target-1", version: 1 } as ReserveTarget;
    const mandate = { id: "mandate-1", version: 2, terms_hash: `sha256:${"a".repeat(64)}` } as CrisisMandate;
    const rule = { id: "rule-1", version: 1, terms_hash: `sha256:${"b".repeat(64)}` } as RationingRule;
    const plan = { id: "plan-1", version: 1, allocations_hash: `sha256:${"c".repeat(64)}` } as RationingPlan;
    const form = { id: "form-1", checksum: "A1B2C3D4" } as CrisisPaperForm;

    await Promise.all([
      getReserveTargets(), getReserveSnapshots(), getCrisisMandates(), getCrisisReviews(),
      getRationingRules(), getRationingPlans(), getRationingAllocations(), getRationIssuances(),
      getCrisisPaperForms(), getCrisisReports(), getCrisisOperatorWorkspace(), getCrisisControllerWorkspace(),
    ]);
    await proposeReserveTarget({ cooperative_id: "coop-1", resource_code: "FOOD" });
    await approveReserveTarget(target);
    await recordReserveSnapshot({ target_id: target.id, evidence_ids: ["evidence-1"] });
    await proposeCrisisMandate({ cooperative_id: "coop-1", mandate_code: "CRISIS-1" });
    await activateCrisisMandate(mandate);
    await reviewCrisisMandate(mandate, { decision: "CONTINUE", facts_payload: {}, rationale: "Reviewed" });
    await closeCrisisMandate(mandate, "Reconciled");
    await expireCrisisMandate(mandate, "Expired and reconciled");
    await proposeRationingRule({ mandate_id: mandate.id, target_id: target.id });
    await approveRationingRule(rule);
    await previewRationingPlan(rule.id, [{ member_id: "member-1", weight: 1 }]);
    await confirmRationingPlan(plan);
    await cancelRationingPlan(plan, "Cancelled after review");
    await issueRation("allocation-1", "Issued", ["evidence-2"]);
    await issueCrisisPaperForm({ mandate_id: mandate.id, serial_number: "PAPER-1" });
    await recordCrisisPaperForm(form.id, form.checksum, { note: "Recorded" });

    const urls = fetchMock.mock.calls.map((call) => call[0]);
    expect(urls).toContain("/api/v1/crisis/mandates/mandate-1/activation");
    expect(urls).toContain("/api/v1/crisis/rationing-plans/plan-1/confirmation");
    expect(urls).toContain("/api/v1/crisis/paper-forms/form-1/record");
    expect(fetchMock).toHaveBeenCalledTimes(28);
  });
});
