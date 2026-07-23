import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Principal } from "./api/admin";
import { getInventoryMembers } from "./api/inventory";
import {
  getAidApplications,
  getAllocations,
  getCampaignBalances,
  getCampaignReports,
  getCampaigns,
  getComplaints,
  getContributions,
  getDeliveries,
  getFunds,
  getPledges,
  getSolidarityControllerWorkspace,
  getSolidarityOperatorWorkspace,
  verifyContribution,
  type Campaign,
  type Contribution,
} from "./api/solidarity";
import SolidarityView from "./SolidarityView";

vi.mock("./api/inventory", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/inventory")>();
  return { ...actual, getInventoryMembers: vi.fn(), uploadEvidence: vi.fn() };
});

vi.mock("./api/solidarity", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/solidarity")>();
  return Object.fromEntries(Object.entries(actual).map(([key, value]) => [
    key,
    typeof value === "function" ? vi.fn() : value,
  ]));
});

const cooperativeId = "10000000-0000-4000-8000-000000000001";
const annaId = "10000000-0000-4000-8000-000000000002";
const pavelId = "10000000-0000-4000-8000-000000000003";
const campaign = {
  id: "campaign-1",
  cooperative_id: cooperativeId,
  fund_id: "fund-1",
  campaign_code: "DEMO-AID-001",
  title: "Продовольственная помощь",
  public_purpose: "Проверенная помощь без раскрытия получателя.",
  accepted_forms: ["GOODS"],
  starts_at: "2035-01-01T00:00:00Z",
  ends_at: "2035-02-01T00:00:00Z",
  residue_rule: "RETAIN_IN_FUND",
  terms_hash: `sha256:${"a".repeat(64)}`,
  status: "OPEN",
  created_by_member_id: annaId,
  opened_by_member_id: pavelId,
  closed_by_member_id: null,
  created_at: "2035-01-01T00:00:00Z",
  opened_at: "2035-01-01T01:00:00Z",
  closed_at: null,
  version: 2,
} as Campaign;
const contribution = {
  id: "contribution-1",
  campaign_id: campaign.id,
  pledge_id: "pledge-1",
  donor_member_id: annaId,
  contribution_form: "GOODS",
  unit_code: "KG",
  quantity: "10",
  description: "Капуста принята на склад фонда.",
  status: "RECEIVED",
  received_by_member_id: annaId,
  verified_by_member_id: null,
  verification_note: null,
  received_at: "2035-01-02T00:00:00Z",
  verified_at: null,
  version: 1,
} as Contribution;
const principal: Principal = {
  user_id: "user-1",
  login: "solidarity-controller",
  member_id: pavelId,
  must_change_password: false,
  roles: [
    { assignment_id: "role-1", role: "SOLIDARITY_CONTROLLER", cooperative_id: cooperativeId },
    { assignment_id: "role-2", role: "SOLIDARITY_OPERATOR", cooperative_id: cooperativeId },
  ],
};

function renderView() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <SolidarityView principal={principal} />
    </QueryClientProvider>,
  );
}

describe("SolidarityView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getInventoryMembers).mockResolvedValue([
      { member_id: annaId, cooperative_id: cooperativeId, display_name: "Анна Петрова", member_number: "D-1" },
      { member_id: pavelId, cooperative_id: cooperativeId, display_name: "Павел Лебедев", member_number: "D-2" },
    ]);
    vi.mocked(getFunds).mockResolvedValue([]);
    vi.mocked(getCampaigns).mockResolvedValue([campaign]);
    vi.mocked(getPledges).mockResolvedValue([{
      id: "pledge-1", campaign_id: campaign.id, donor_member_id: annaId,
      contribution_form: "GOODS", unit_code: "KG", quantity: "10",
      description: "Обещано десять килограммов.", status: "ACTIVE",
      expires_at: "2035-01-09T00:00:00Z", fulfilled_contribution_id: null,
      created_at: "2035-01-02T00:00:00Z", version: 1,
    }]);
    vi.mocked(getContributions).mockResolvedValue([contribution]);
    vi.mocked(getAidApplications).mockResolvedValue([]);
    vi.mocked(getAllocations).mockResolvedValue([]);
    vi.mocked(getDeliveries).mockResolvedValue([]);
    vi.mocked(getComplaints).mockResolvedValue([]);
    vi.mocked(getCampaignReports).mockResolvedValue([{
      id: "report-1", campaign_id: campaign.id, cooperative_id: cooperativeId,
      bucket_totals: [{ contribution_form: "GOODS", unit_code: "KG", verified: "10", delivered: "10", residue: "0" }],
      contribution_count: 1, allocation_count: 1, delivery_count: 1, complaint_count: 0,
      residue_rule: "RETAIN_IN_FUND", responsibility_snapshot: [],
      report_hash: `sha256:${"b".repeat(64)}`, generated_at: "2035-02-01T00:00:00Z",
    }]);
    vi.mocked(getCampaignBalances).mockResolvedValue([
      { contribution_form: "GOODS", unit_code: "KG", verified: "10", reserved_or_delivered: "0", available: "10" },
    ]);
    vi.mocked(getSolidarityOperatorWorkspace).mockResolvedValue({ campaigns: [campaign], verified_contributions: [], eligible_applications: [], active_allocations: [] });
    vi.mocked(getSolidarityControllerWorkspace).mockResolvedValue({ draft_funds: [], draft_campaigns: [], received_contributions: [contribution], submitted_applications: [], proposed_allocations: [], open_complaints: [] });
    vi.mocked(verifyContribution).mockResolvedValue({ event_id: "event-1", object_id: contribution.id, replayed: false });
  });

  it("keeps promises separate from verified balance and shows aggregate reports", async () => {
    const user = userEvent.setup();
    renderView();

    expect(await screen.findByRole("heading", { name: "Солидарный фонд" })).toBeInTheDocument();
    expect(await screen.findByText("10", { selector: ".solidarity-scope-strip strong" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Взносы" }));
    expect(await screen.findByText("Обещания, не включённые в баланс")).toBeInTheDocument();
    expect(screen.getByText("Капуста принята на склад фонда.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Отчёты" }));
    expect(await screen.findByText("Агрегированные отчёты")).toBeInTheDocument();
    expect(screen.getByText("Продовольственная помощь")).toBeInTheDocument();
  });

  it("lets an independent controller verify a received contribution", async () => {
    const user = userEvent.setup();
    renderView();

    await user.click(await screen.findByRole("button", { name: "Взносы" }));
    await user.click(await screen.findByTitle("Подтвердить"));

    expect(verifyContribution).toHaveBeenCalledWith(
      contribution,
      true,
      "Факт, количество и документ проверены.",
    );
  });
});
