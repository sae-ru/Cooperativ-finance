import { afterEach, describe, expect, it, vi } from "vitest";

import {
  approveAllocation,
  approveFund,
  closeCampaign,
  createCampaign,
  createPledge,
  getAidApplications,
  getAllocations,
  getCampaignBalances,
  getCampaignReports,
  getComplaints,
  getContributions,
  getDeliveries,
  getPledges,
  getSolidarityControllerWorkspace,
  getSolidarityOperatorWorkspace,
  openCampaign,
  openComplaint,
  proposeAllocation,
  proposeFund,
  receiveContribution,
  recordDelivery,
  resolveComplaint,
  reviewAidApplication,
  submitAidApplication,
  verifyContribution,
  type AidApplication,
  type Allocation,
  type Campaign,
  type Complaint,
  type Contribution,
  type Fund,
} from "./solidarity";

function envelope(data: unknown): Response {
  return new Response(JSON.stringify({ data }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("solidarity command API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("maps every lifecycle command to a version-bound endpoint", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(envelope({
      event_id: "event-1",
      object_id: "object-1",
      replayed: false,
    })));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("crypto", { randomUUID: () => "00000000-0000-4000-8000-000000000123" });

    const fund = { id: "fund-1", version: 2 } as Fund;
    const campaign = { id: "campaign-1", version: 3 } as Campaign;
    const contribution = { id: "contribution-1", version: 4 } as Contribution;
    const application = { id: "application-1", version: 5 } as AidApplication;
    const allocation = {
      id: "allocation-1",
      version: 6,
      allocation_hash: `sha256:${"a".repeat(64)}`,
    } as Allocation;
    const complaint = { id: "complaint-1", version: 7 } as Complaint;

    await Promise.all([
      getPledges(),
      getContributions(),
      getAidApplications(),
      getAllocations(),
      getDeliveries(),
      getComplaints(),
      getCampaignReports(),
      getCampaignBalances(campaign.id),
      getSolidarityOperatorWorkspace(),
      getSolidarityControllerWorkspace(),
    ]);
    await proposeFund({
      cooperative_id: "cooperative-1",
      fund_code: "FOOD",
      name: "Food fund",
      purpose: "Verified voluntary aid",
      residue_rule: "RETAIN_IN_FUND",
      admin_expense_limit: "0",
      terms: { no_debt: true },
    });
    await approveFund(fund);
    await createCampaign({
      fund_id: fund.id,
      campaign_code: "FOOD-1",
      title: "Food support",
      public_purpose: "Verified food support",
      eligibility_policy: { need_categories: ["BASIC_FOOD"] },
      accepted_forms: ["GOODS"],
      starts_at: "2035-01-01T00:00:00Z",
      ends_at: "2035-02-01T00:00:00Z",
    });
    await openCampaign(campaign);
    await closeCampaign(campaign, "Balances reconciled.");
    await createPledge(campaign.id, {
      donor_member_id: "member-1",
      contribution_form: "GOODS",
      unit_code: "KG",
      quantity: "10",
      description: "Cabbage",
      expires_at: "2035-01-10T00:00:00Z",
    });
    await receiveContribution({
      campaign_id: campaign.id,
      pledge_id: "pledge-1",
      donor_member_id: "member-1",
      contribution_form: "GOODS",
      unit_code: "KG",
      quantity: "10",
      description: "Cabbage received",
      evidence_ids: ["evidence-1"],
    });
    await verifyContribution(contribution, false, "Mismatch recorded.");
    await submitAidApplication(campaign.id, {
      recipient_member_id: "member-2",
      need_category: "BASIC_FOOD",
      requested_form: "GOODS",
      requested_unit_code: "KG",
      requested_quantity: "5",
      privacy_scope: "RESTRICTED",
      evidence_ids: [],
    });
    await reviewAidApplication(application, false, "Criteria not confirmed.");
    await proposeAllocation(application.id, {
      quantity: "5",
      public_summary: "One food allocation",
      rationale: "Eligible request",
    });
    await approveAllocation(allocation, false, "Conflict review failed.");
    await recordDelivery(allocation, {
      attestor_kind: "RECIPIENT",
      acknowledgement: "Received",
      evidence_ids: ["evidence-2"],
    });
    await openComplaint(campaign.id, {
      allocation_id: allocation.id,
      contribution_id: null,
      category: "DELIVERY",
      summary: "Quantity requires review",
      privacy_scope: "RESTRICTED",
      evidence_ids: [],
    });
    await resolveComplaint(complaint, {
      accepted: true,
      resolution_action: "RESTORE_ALLOCATION",
      resolution_note: "Allocation restored",
    });

    const urls = fetchMock.mock.calls.map((call) => call[0]);
    expect(urls).toContain("/api/v1/solidarity/campaigns/campaign-1/close");
    expect(urls).toContain("/api/v1/solidarity/allocations/allocation-1/delivery");
    expect(urls).toContain("/api/v1/solidarity/complaints/complaint-1/resolution");
    expect(fetchMock).toHaveBeenCalledTimes(25);
  });
});
