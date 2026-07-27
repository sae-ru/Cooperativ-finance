import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Principal } from "./api/admin";
import {
  acceptExposure,
  approveRiskPolicy,
  getExposureCommitments,
  getLiabilityCases,
  getRelatedLinks,
  getRiskPolicies,
  getShareAccounts,
  previewExposure,
  proposeExposure,
  type ExposureCommitment,
  type RiskPolicy,
  type ShareAccount,
} from "./api/risk";
import RiskView from "./RiskView";

vi.mock("./api/admin", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/admin")>();
  return {
    ...actual,
    getCooperatives: vi.fn(),
  };
});

vi.mock("./api/inventory", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/inventory")>();
  return {
    ...actual,
    getInventoryMembers: vi.fn(),
    uploadEvidence: vi.fn(),
  };
});

vi.mock("./api/risk", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/risk")>();
  return {
    ...actual,
    getRiskPolicies: vi.fn(),
    getShareAccounts: vi.fn(),
    getShareContributions: vi.fn(),
    getRelatedLinks: vi.fn(),
    getExposureCommitments: vi.fn(),
    getLiabilityCases: vi.fn(),
    previewExposure: vi.fn(),
    proposeRiskPolicy: vi.fn(),
    approveRiskPolicy: vi.fn(),
    openShareAccount: vi.fn(),
    addShareContribution: vi.fn(),
    proposeRelatedLink: vi.fn(),
    decideRelatedLink: vi.fn(),
    proposeExposure: vi.fn(),
    acceptExposure: vi.fn(),
    releaseExposure: vi.fn(),
    openLiabilityCase: vi.fn(),
    assessLiabilityCase: vi.fn(),
  };
});

const cooperativeId = "10000000-0000-4000-8000-000000000001";
const ownerId = "10000000-0000-4000-8000-000000000002";
const operatorId = "10000000-0000-4000-8000-000000000003";

const activePolicy = {
  id: "20000000-0000-4000-8000-000000000001",
  cooperative_id: cooperativeId,
  policy_version: 1,
  denomination: "SHARE",
  max_member_exposure: "100.000000000000",
  max_related_exposure: "150.000000000000",
  max_guarantee_chain_depth: 3,
  terms_hash: `sha256:${"a".repeat(64)}`,
  terms_payload: {},
  status: "ACTIVE",
  proposed_by_member_id: operatorId,
  proposed_event_id: "20000000-0000-4000-8000-000000000002",
  approved_by_member_id: ownerId,
  approved_event_id: "20000000-0000-4000-8000-000000000003",
  created_at: "2026-07-20T10:00:00Z",
  approved_at: "2026-07-20T10:10:00Z",
  version: 2,
} as RiskPolicy;

const account = {
  id: "30000000-0000-4000-8000-000000000001",
  cooperative_id: cooperativeId,
  member_id: ownerId,
  opening_policy_id: activePolicy.id,
  contour: "GUARANTEE",
  denomination: "SHARE",
  balance: "100.000000000000",
  protected_amount: "40.000000000000",
  executed_not_settled: "0.000000000000",
  status: "ACTIVE",
  created_event_id: "30000000-0000-4000-8000-000000000002",
  last_event_id: "30000000-0000-4000-8000-000000000002",
  created_at: "2026-07-20T10:00:00Z",
  updated_at: "2026-07-20T10:00:00Z",
  version: 1,
} as ShareAccount;

const commitment = {
  id: "40000000-0000-4000-8000-000000000001",
  cooperative_id: cooperativeId,
  policy_id: activePolicy.id,
  account_id: account.id,
  owner_member_id: ownerId,
  commitment_type: "DIRECT_OBLIGATION",
  risk_type: "DELIVERY",
  risk_id: "40000000-0000-4000-8000-000000000002",
  debtor_member_id: ownerId,
  beneficiary_member_id: null,
  role_assignment_id: null,
  amount_reserved: "30.000000000000",
  max_loss: "25.000000000000",
  coverage_ratio: "0.833333",
  starts_at: "2026-07-20T10:00:00Z",
  expires_at: "2027-07-20T10:00:00Z",
  release_condition: "Verified completion.",
  trigger_conditions: "Documented default.",
  exclusions: "Protected shares.",
  terms_hash: `sha256:${"b".repeat(64)}`,
  terms_payload: {},
  status: "PROPOSED",
  proposed_by_member_id: operatorId,
  proposed_event_id: "40000000-0000-4000-8000-000000000003",
  accepted_by_user_id: null,
  accepted_event_id: null,
  released_event_id: null,
  release_reason: null,
  created_at: "2026-07-20T10:00:00Z",
  accepted_at: null,
  released_at: null,
  version: 1,
} as ExposureCommitment;

function principal(memberId: string | null, roles: Principal["roles"] = []): Principal {
  return {
    user_id: "50000000-0000-4000-8000-000000000001",
    login: "risk.operator",
    member_id: memberId,
    must_change_password: false,
    roles,
  };
}

function renderView(value: Principal) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <RiskView principal={value} />
    </QueryClientProvider>,
  );
}

describe("RiskView", () => {
  beforeEach(async () => {
    const admin = await import("./api/admin");
    const inventory = await import("./api/inventory");
    vi.mocked(admin.getCooperatives).mockResolvedValue([
      { id: cooperativeId, code: "DEMO", name: "Demo", status: "ACTIVE", created_at: "2026-07-20T00:00:00Z", updated_at: "2026-07-20T00:00:00Z", version: 1 },
    ]);
    vi.mocked(inventory.getInventoryMembers).mockResolvedValue([
      { member_id: ownerId, cooperative_id: cooperativeId, display_name: "Владелец пая", member_number: "M-1" },
      { member_id: operatorId, cooperative_id: cooperativeId, display_name: "Риск-оператор", member_number: "M-2" },
    ]);
    vi.mocked(inventory.uploadEvidence).mockResolvedValue(
      "60000000-0000-4000-8000-000000000001",
    );
    vi.mocked(getRiskPolicies).mockResolvedValue([activePolicy]);
    vi.mocked(getShareAccounts).mockResolvedValue([account]);
    vi.mocked(getExposureCommitments).mockResolvedValue([commitment]);
    vi.mocked(getRelatedLinks).mockResolvedValue([]);
    vi.mocked(getLiabilityCases).mockResolvedValue([]);
    vi.mocked(acceptExposure).mockResolvedValue({
      event_id: "event-1",
      object_id: commitment.id,
      replayed: false,
    });
    vi.mocked(approveRiskPolicy).mockResolvedValue({
      event_id: "event-2",
      object_id: activePolicy.id,
      replayed: false,
    });
    vi.mocked(previewExposure).mockResolvedValue({
      account_available_before: "60.000000000000",
      account_available_after: "50.000000000000",
      member_exposure_before: "0.000000000000",
      member_exposure_after: "8.000000000000",
      related_exposure_before: "0.000000000000",
      related_exposure_after: "8.000000000000",
      max_member_exposure: "100.000000000000",
      max_related_exposure: "150.000000000000",
      allowed: true,
      reason_code: null,
    });
    vi.mocked(proposeExposure).mockResolvedValue({
      event_id: "event-3",
      object_id: "commitment-new",
      replayed: false,
    });
  });

  it("shows protected shares and lets the exact owner accept hashed terms", async () => {
    const user = userEvent.setup();
    renderView(principal(ownerId));

    expect(await screen.findByRole("heading", { name: "Риск и паи" })).toBeInTheDocument();
    expect(screen.getByText("40")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Риски" }));
    await user.click(screen.getByRole("button", { name: "Принять" }));

    await waitFor(() => expect(acceptExposure).toHaveBeenCalled());
    expect(vi.mocked(acceptExposure).mock.calls[0]?.[0]).toEqual(commitment);
  });

  it("keeps policy approval independent and evidence-backed", async () => {
    const user = userEvent.setup();
    const inventory = await import("./api/inventory");
    const proposedPolicy = { ...activePolicy, status: "PROPOSED", approved_at: null, approved_by_member_id: null, approved_event_id: null, version: 1 };
    vi.mocked(getRiskPolicies).mockResolvedValue([proposedPolicy]);
    renderView(principal(ownerId, [{
      assignment_id: "70000000-0000-4000-8000-000000000001",
      role: "RISK_ADMIN",
      cooperative_id: cooperativeId,
    }]));

    await screen.findByRole("heading", { name: "Риск и паи" });
    await user.click(screen.getByRole("button", { name: "Политики" }));
    const file = new File(["approval"], "approval.txt", { type: "text/plain" });
    await user.upload(screen.getByLabelText("Основание утверждения"), file);
    await user.click(screen.getByRole("button", { name: "Утвердить" }));

    await waitFor(() => {
      expect(inventory.uploadEvidence).toHaveBeenCalledWith(
        cooperativeId,
        file,
        "RISK_POLICY_APPROVAL",
      );
      expect(approveRiskPolicy).toHaveBeenCalledWith(
        proposedPolicy,
        ["60000000-0000-4000-8000-000000000001"],
      );
    });
  });

  it("previews all limits before proposing a personal reserve", async () => {
    const user = userEvent.setup();
    renderView(principal(operatorId, [{
      assignment_id: "70000000-0000-4000-8000-000000000002",
      role: "RISK_ADMIN",
      cooperative_id: cooperativeId,
    }]));

    await screen.findByRole("heading", { name: "Риск и паи" });
    await user.click(screen.getByRole("button", { name: "Риски" }));
    await user.type(screen.getByLabelText("Резерв"), "10");
    await user.type(screen.getByLabelText("Максимальный ущерб"), "8");
    await user.click(screen.getByRole("button", { name: "Рассчитать" }));

    await waitFor(() => expect(previewExposure).toHaveBeenCalledWith({
      account_id: account.id,
      policy_id: activePolicy.id,
      commitment_type: "DIRECT_OBLIGATION",
      amount_reserved: "10",
      max_loss: "8",
    }));
    await user.click(screen.getByRole("button", { name: "Предложить" }));

    await waitFor(() => expect(proposeExposure).toHaveBeenCalledWith(
      expect.objectContaining({
        account_id: account.id,
        policy_id: activePolicy.id,
        commitment_type: "DIRECT_OBLIGATION",
        amount_reserved: "10",
        max_loss: "8",
        coverage_ratio: "1",
      }),
    ));
  });
});
