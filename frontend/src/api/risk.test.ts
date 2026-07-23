import { afterEach, describe, expect, it, vi } from "vitest";

import {
  acceptExposure,
  addShareContribution,
  approveRiskPolicy,
  assessLiabilityCase,
  decideRelatedLink,
  getExposureCommitments,
  getLiabilityCases,
  getRelatedLinks,
  getRiskPolicies,
  getShareAccounts,
  getShareContributions,
  openLiabilityCase,
  openShareAccount,
  previewExposure,
  proposeExposure,
  proposeRelatedLink,
  proposeRiskPolicy,
  releaseExposure,
  type ExposureCommitment,
  type LiabilityCase,
  type RelatedLink,
  type RiskPolicy,
  type ShareAccount,
} from "./risk";

function envelope(data: unknown): Response {
  return new Response(JSON.stringify({ data }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

const policy = {
  id: "policy-1",
  cooperative_id: "coop-1",
  terms_hash: `sha256:${"a".repeat(64)}`,
  version: 1,
} as RiskPolicy;
const account = { id: "account-1", version: 3 } as ShareAccount;
const link = { id: "link-1", version: 2 } as RelatedLink;
const commitment = {
  id: "commitment-1",
  terms_hash: `sha256:${"b".repeat(64)}`,
  version: 4,
} as ExposureCommitment;
const liability = { id: "case-1", version: 2 } as LiabilityCase;

describe("bounded risk API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("reads every participant-scoped risk registry", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(envelope([])));
    vi.stubGlobal("fetch", fetchMock);

    await getRiskPolicies();
    await getShareAccounts();
    await getShareContributions(account.id);
    await getRelatedLinks();
    await getExposureCommitments();
    await getLiabilityCases();

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/v1/risk/policies",
      "/api/v1/risk/accounts",
      "/api/v1/risk/accounts/account-1/contributions",
      "/api/v1/risk/related-links",
      "/api/v1/risk/commitments",
      "/api/v1/risk/liability-cases",
    ]);
  });

  it("sends versions, exact terms hashes, and idempotency keys", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(envelope({
      event_id: "event-result",
      object_id: "object-result",
      replayed: false,
    })));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("crypto", {
      randomUUID: () => "00000000-0000-4000-8000-000000000006",
    });

    await previewExposure({
      account_id: account.id,
      policy_id: policy.id,
      commitment_type: "DIRECT_OBLIGATION",
      amount_reserved: "10",
      max_loss: "8",
    });
    await proposeRiskPolicy({
      cooperative_id: policy.cooperative_id,
      denomination: "SHARE",
      max_member_exposure: "100",
      max_related_exposure: "150",
      max_guarantee_chain_depth: 3,
      protected_amount_rule: "Protected shares are excluded.",
      related_party_rule: "Related parties share one limit.",
      approval_reference: "BOARD-1",
      evidence_ids: ["evidence-1"],
    });
    await approveRiskPolicy(policy, ["evidence-2"]);
    await openShareAccount({
      policy_id: policy.id,
      member_id: "member-1",
      contour: "GUARANTEE",
      opening_balance: "100",
      protected_amount: "40",
      source_reference: "REGISTER-1",
      evidence_ids: ["evidence-3"],
    });
    await addShareContribution(account, {
      amount: "10",
      source_reference: "REGISTER-2",
      evidence_ids: ["evidence-4"],
    });
    await proposeRelatedLink({
      cooperative_id: policy.cooperative_id,
      member_a_id: "member-1",
      member_b_id: "member-2",
      relation_type: "RELATED",
      source_statement: "Common control.",
      evidence_ids: ["evidence-5"],
    });
    await decideRelatedLink(link, true, "Confirmed.", ["evidence-6"]);
    await proposeExposure({
      account_id: account.id,
      policy_id: policy.id,
      commitment_type: "DIRECT_OBLIGATION",
      risk_type: "DELIVERY",
      risk_id: "risk-1",
      debtor_member_id: "member-1",
      beneficiary_member_id: null,
      role_assignment_id: null,
      amount_reserved: "10",
      max_loss: "8",
      coverage_ratio: "0.8",
      starts_at: "2026-07-20T00:00:00Z",
      expires_at: "2027-07-20T00:00:00Z",
      release_condition: "Verified completion.",
      trigger_conditions: "Documented default.",
      exclusions: "Protected shares.",
    });
    await acceptExposure(commitment);
    await releaseExposure(commitment, "Completed.", ["evidence-7"]);
    await openLiabilityCase({
      commitment_id: commitment.id,
      incident_reference: "INCIDENT-1",
      affected_amount: "8",
      facts: "Documented facts.",
      causal_graph: { cause: "default", effect: "loss" },
      evidence_ids: ["evidence-8"],
    });
    await assessLiabilityCase(liability, {
      fault_class: "NEGLIGENCE",
      assessed_loss: "5",
      rationale: "Independent assessment.",
      appeal_until: "2026-08-20T00:00:00Z",
      evidence_ids: ["evidence-9"],
    });

    expect((fetchMock.mock.calls[0]?.[1]?.headers as Headers).get("Idempotency-Key"))
      .toBeNull();
    for (const call of fetchMock.mock.calls.slice(1)) {
      expect((call[1]?.headers as Headers).get("Idempotency-Key"))
        .toBe("00000000-0000-4000-8000-000000000006");
    }
    expect(JSON.parse(String(fetchMock.mock.calls[2]?.[1]?.body))).toMatchObject({
      terms_hash: policy.terms_hash,
      expected_version: policy.version,
    });
    expect(JSON.parse(String(fetchMock.mock.calls[4]?.[1]?.body))).toMatchObject({
      expected_version: account.version,
    });
    expect(JSON.parse(String(fetchMock.mock.calls[8]?.[1]?.body))).toMatchObject({
      terms_hash: commitment.terms_hash,
      expected_version: commitment.version,
    });
    expect(JSON.parse(String(fetchMock.mock.calls[11]?.[1]?.body))).toMatchObject({
      expected_version: liability.version,
    });
  });
});
