import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Principal } from "./api/admin";
import { getInventoryMembers } from "./api/inventory";
import {
  approveAllocation,
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
  resolveComplaint,
  reviewAidApplication,
  type AidApplication,
  type Allocation,
  type Campaign,
  type Complaint,
  type Delivery,
} from "./api/solidarity";
import SolidarityView from "./SolidarityView";

vi.mock("./api/inventory", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/inventory")>();
  return { ...actual, getInventoryMembers: vi.fn(), uploadEvidence: vi.fn() };
});

vi.mock("./api/solidarity", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/solidarity")>();
  return Object.fromEntries(
    Object.entries(actual).map(([key, value]) => [
      key,
      typeof value === "function" ? vi.fn() : value,
    ]),
  );
});

const cooperativeId = "20000000-0000-4000-8000-000000000001";
const operatorId = "20000000-0000-4000-8000-000000000002";
const recipientId = "20000000-0000-4000-8000-000000000003";
const campaign = {
  id: "campaign-operations",
  cooperative_id: cooperativeId,
  fund_id: "fund-1",
  campaign_code: "AID-OPS",
  title: "Операционная помощь",
  public_purpose: "Проверяемое распределение помощи.",
  accepted_forms: ["GOODS"],
  starts_at: "2035-01-01T00:00:00Z",
  ends_at: "2035-02-01T00:00:00Z",
  residue_rule: "RETAIN_IN_FUND",
  terms_hash: `sha256:${"a".repeat(64)}`,
  status: "OPEN",
  created_by_member_id: operatorId,
  opened_by_member_id: operatorId,
  closed_by_member_id: null,
  created_at: "2035-01-01T00:00:00Z",
  opened_at: "2035-01-01T01:00:00Z",
  closed_at: null,
  version: 2,
} as Campaign;
const application = {
  id: "application-1",
  campaign_id: campaign.id,
  recipient_member_id: recipientId,
  need_category: "BASIC_FOOD",
  requested_form: "GOODS",
  requested_unit_code: "KG",
  requested_quantity: "5",
  privacy_scope: "RESTRICTED",
  status: "SUBMITTED",
  submitted_by_member_id: recipientId,
  reviewed_by_member_id: null,
  eligibility_note: null,
  submitted_at: "2035-01-02T00:00:00Z",
  reviewed_at: null,
  version: 1,
} as AidApplication;
const allocation = {
  id: "allocation-1",
  campaign_id: campaign.id,
  application_id: application.id,
  recipient_member_id: recipientId,
  contribution_form: "GOODS",
  unit_code: "KG",
  quantity: "5",
  public_summary: "Продуктовый набор",
  rationale: "Заявка признана допустимой.",
  policy_terms_hash: campaign.terms_hash,
  allocation_hash: `sha256:${"b".repeat(64)}`,
  status: "PROPOSED",
  proposed_by_member_id: operatorId,
  created_at: "2035-01-03T00:00:00Z",
  version: 1,
} as Allocation;
const delivery = {
  id: "delivery-1",
  allocation_id: allocation.id,
  recipient_member_id: recipientId,
  attestor_kind: "RECIPIENT",
  attested_by_member_id: recipientId,
  acknowledgement: "Помощь получена полностью.",
  delivered_event_id: "event-1",
  delivered_at: "2035-01-04T00:00:00Z",
} as Delivery;
const complaint = {
  id: "complaint-1",
  campaign_id: campaign.id,
  allocation_id: allocation.id,
  contribution_id: null,
  complainant_member_id: recipientId,
  category: "ALLOCATION",
  summary: "Требуется повторная проверка количества.",
  privacy_scope: "RESTRICTED",
  status: "OPEN",
  resolved_by_member_id: null,
  resolution_action: null,
  resolution_note: null,
  opened_at: "2035-01-03T02:00:00Z",
  resolved_at: null,
  version: 1,
} as Complaint;
const principal: Principal = {
  user_id: "user-1",
  login: "solidarity-controller",
  member_id: operatorId,
  must_change_password: false,
  roles: [
    {
      assignment_id: "role-controller",
      role: "SOLIDARITY_CONTROLLER",
      cooperative_id: cooperativeId,
    },
    {
      assignment_id: "role-operator",
      role: "SOLIDARITY_OPERATOR",
      cooperative_id: cooperativeId,
    },
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

describe("SolidarityView operations", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getInventoryMembers).mockResolvedValue([
      {
        member_id: operatorId,
        cooperative_id: cooperativeId,
        display_name: "Оператор",
        member_number: "D-1",
      },
      {
        member_id: recipientId,
        cooperative_id: cooperativeId,
        display_name: "Получатель",
        member_number: "D-2",
      },
    ]);
    vi.mocked(getFunds).mockResolvedValue([]);
    vi.mocked(getCampaigns).mockResolvedValue([campaign]);
    vi.mocked(getPledges).mockResolvedValue([]);
    vi.mocked(getContributions).mockResolvedValue([]);
    vi.mocked(getAidApplications).mockResolvedValue([application]);
    vi.mocked(getAllocations).mockResolvedValue([allocation]);
    vi.mocked(getDeliveries).mockResolvedValue([delivery]);
    vi.mocked(getComplaints).mockResolvedValue([complaint]);
    vi.mocked(getCampaignReports).mockResolvedValue([]);
    vi.mocked(getCampaignBalances).mockResolvedValue([
      {
        contribution_form: "GOODS",
        unit_code: "KG",
        verified: "10",
        reserved_or_delivered: "5",
        available: "5",
      },
    ]);
    vi.mocked(getSolidarityOperatorWorkspace).mockResolvedValue({
      campaigns: [campaign],
      verified_contributions: [],
      eligible_applications: [application],
      active_allocations: [allocation],
    });
    vi.mocked(getSolidarityControllerWorkspace).mockResolvedValue({
      draft_funds: [],
      draft_campaigns: [],
      received_contributions: [],
      submitted_applications: [application],
      proposed_allocations: [allocation],
      open_complaints: [complaint],
    });
    vi.mocked(reviewAidApplication).mockResolvedValue({
      event_id: "event-review",
      object_id: application.id,
      replayed: false,
    });
    vi.mocked(approveAllocation).mockResolvedValue({
      event_id: "event-approval",
      object_id: allocation.id,
      replayed: false,
    });
    vi.mocked(resolveComplaint).mockResolvedValue({
      event_id: "event-resolution",
      object_id: complaint.id,
      replayed: false,
    });
  });

  it("reviews applications, allocations, and complaints from their work tabs", async () => {
    const user = userEvent.setup();
    renderView();

    await user.click(await screen.findByRole("button", { name: "Заявки" }));
    await user.click(await screen.findByTitle("Допустить"));
    await waitFor(() => expect(reviewAidApplication).toHaveBeenCalledWith(
      application,
      true,
      "Критерии кампании подтверждены.",
    ));

    await user.click(screen.getByRole("button", { name: "Распределения" }));
    expect(await screen.findByText("Помощь получена полностью.")).toBeInTheDocument();
    await user.click(await screen.findByTitle("Утвердить"));
    await waitFor(() => expect(approveAllocation).toHaveBeenCalledWith(
      allocation,
      true,
      "Конфликт интересов отсутствует.",
    ));

    await user.click(screen.getByRole("button", { name: "Жалобы" }));
    await user.click(await screen.findByTitle("Восстановить распределение"));
    await waitFor(() => expect(resolveComplaint).toHaveBeenCalledWith(
      complaint,
      expect.objectContaining({
        accepted: true,
        resolution_action: "RESTORE_ALLOCATION",
      }),
    ));
  });
});
