import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import MemberHomeView from "./MemberHomeView";
import i18n from "./i18n";
import * as participant from "./api/participant";
import type { ParticipantDashboard } from "./api/participant";
import * as risk from "./api/risk";
import type { CompensationTransfer } from "./api/risk";

vi.mock("./api/participant", async () => {
  const actual = await vi.importActual<typeof import("./api/participant")>("./api/participant");
  return Object.fromEntries(
    Object.entries(actual).map(([key, value]) => [
      key,
      typeof value === "function" ? vi.fn() : value,
    ]),
  );
});

vi.mock("./api/risk", async () => {
  const actual = await vi.importActual<typeof import("./api/risk")>("./api/risk");
  return {
    ...actual,
    acceptCompensation: vi.fn(),
    getCompensations: vi.fn(),
  };
});

const dashboard: ParticipantDashboard = {
  profile: {
    member_id: "member-1",
    display_name: "Ivan Milkman",
    member_status: "ACTIVE",
    login: "ivan-milk",
    last_login_at: null,
    member_since: "2026-07-20T10:00:00Z",
  },
  memberships: [{
    id: "membership-1",
    cooperative_id: "coop-1",
    cooperative_code: "DEMO",
    cooperative_name: "Demonstration cooperative",
    cooperative_status: "ACTIVE",
    member_number: "D-FARM-01",
    membership_status: "ACTIVE",
    joined_at: "2026-07-20T10:00:00Z",
  }],
  shares: {
    denomination: "shares",
    total_balance: "50",
    available: "40",
    protected: "10",
    reserved: "0",
    account_missing: false,
    accounts: [{
      id: "account-1",
      cooperative_id: "coop-1",
      contour: "GUARANTEE",
      denomination: "shares",
      balance: "50",
      available: "40",
      protected: "10",
      reserved: "0",
      executed_not_settled: "0",
      status: "ACTIVE",
      policy: {
        id: "policy-1",
        version: 1,
        terms_hash: "sha256:1234567890abcdef",
        approval_event_id: "event-policy",
        approved_at: "2026-07-20T10:00:00Z",
        max_member_exposure: "100",
      },
      sources: [{
        amount: "50",
        source_reference: "DEMO-SHARE-REGISTER-IVAN",
        event_id: "event-source",
        created_at: "2026-07-20T10:00:00Z",
      }],
    }],
  },
  exchange_position: {
    earned_settled: "7",
    expected_incoming: "12",
    expected_outgoing: "31",
  },
  offers: [{
    record_id: "record-corrupt",
    offer_id: "offer-corrupt",
    offer_version: 1,
    kind: "SERVICE",
    has_image: false,
    product_code: "SERVICE.LEGACY",
    description: "?????? ?????????",
    quantity_available: "8",
    unit_code: "HOUR",
    minimum_batch: "1",
    unit_price: "3",
    valuation_unit: "COOP",
    price_policy_version: "V1",
    origin_region: "EAST",
    pickup_address_text: "12 Farm Road, Barn 2",
    pickup_contact_name: "Ivan Milkman",
    pickup_contact_phone: "+1 555 010 2000",
    pickup_instructions: "Use the farm gate",
    status: "REVOKED",
    availability_until: "2026-07-31T10:00:00Z",
    created_at: "2026-07-24T10:00:00Z",
    payload_hash: "sha256:test",
  }],
  purchases: [],
  sales: [],
  obligations: Array.from({ length: 3 }, (_, index) => ({
    id: `obligation-${index}`,
    deal_id: "deal-1",
    cooperative_id: "coop-1",
    debtor_member_id: "member-1",
    creditor_member_id: "member-2",
    source_purchase_intent_id: "intent-1",
    direction: "OWE" as const,
    subject_type: "MONEY_EQUIVALENT",
    description: "Nails valuation",
    quantity_total: "10",
    quantity_submitted: "0",
    quantity_fulfilled: "0",
    quantity_cleared: "0",
    unit_id: "unit-shares",
    unit_code: "shares",
    unit_symbol: "shares",
    unit_dimension: "VALUE",
    due_at: "2026-07-25T10:00:00Z",
    fulfillment_place: "Member account",
    partial_allowed: false,
    evidence_required: true,
    status: "OPEN",
    version: 1,
    valuation_source: "PURCHASE_INTENT",
    clearing_allowed: true,
  })),
  commitments: [],
  generated_at: "2026-07-24T10:00:00Z",
  cooperative_count: 1,
};

const farmAddress: participant.ParticipantAddress = {
  id: "address-farm",
  cooperative_id: "coop-1",
  label: "Farm",
  purpose: "BOTH",
  region_code: "EAST-DISTRICT",
  address_text: "12 Farm Road, Barn 2",
  contact_name: "Ivan Milkman",
  contact_phone: "+1 555 010 2000",
  instructions: "Use the green gate",
  is_default_pickup: true,
  is_default_delivery: true,
  status: "ACTIVE",
  created_at: "2026-07-24T10:00:00Z",
  updated_at: "2026-07-24T10:00:00Z",
  version: 1,
};

function renderView() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemberHomeView onNavigate={vi.fn()} />
    </QueryClientProvider>,
  );
}

describe("MemberHomeView", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("en");
    document.documentElement.lang = "en";
    vi.clearAllMocks();
    vi.mocked(participant.getParticipantDashboard).mockResolvedValue(dashboard);
    vi.mocked(participant.getParticipantAddresses).mockResolvedValue([]);
    vi.mocked(risk.getCompensations).mockResolvedValue([]);
  });

  it("explains the member's share position, obligations, cooperative, and source", async () => {
    renderView();

    expect(await screen.findByRole("heading", { name: "Ivan Milkman" })).toBeInTheDocument();
    expect(screen.getAllByText(/Demonstration cooperative/)).toHaveLength(2);
    expect(screen.getByText("Cooperative code")).toBeInTheDocument();
    expect(screen.getByText("DEMO")).toBeInTheDocument();
    expect(screen.getByText("Active membership")).toBeInTheDocument();
    expect(screen.getByText("This is the first sign-in")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Unnamed service" })).toBeInTheDocument();
    expect(screen.getAllByText("50 shares")).toHaveLength(1);
    expect(screen.getAllByText("40 shares")).toHaveLength(1);
    expect(screen.getByText("Due from you")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Available for responsibility" })).toHaveTextContent(/31\s*shares/);
    expect(screen.getByText("Obligations: 3")).toBeInTheDocument();
    expect(screen.getByText(/DEMO-SHARE-REGISTER-IVAN/)).toBeInTheDocument();
  });

  it("lets the member edit a private saved pickup and delivery point", async () => {
    const user = userEvent.setup();
    vi.mocked(participant.getParticipantAddresses).mockResolvedValue([farmAddress]);
    vi.mocked(participant.updateParticipantAddress).mockResolvedValue({
      event_id: "event-address",
      object_id: farmAddress.id,
      replayed: false,
    });

    renderView();

    expect(await screen.findByRole("heading", { name: "My places" })).toBeInTheDocument();
    expect(await screen.findByText("12 Farm Road, Barn 2")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Edit place" }));
    const exactAddress = screen.getByRole("textbox", { name: "Exact address" });
    await user.clear(exactAddress);
    await user.type(exactAddress, "14 Farm Road, loading gate");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(participant.updateParticipantAddress).toHaveBeenCalledWith(
      farmAddress,
      expect.objectContaining({ address_text: "14 Farm Road, loading gate" }),
    );
  });

  it("localizes a value obligation using the related purchase title", async () => {
    vi.mocked(participant.getParticipantDashboard).mockResolvedValue({
      ...dashboard,
      purchases: [{
        id: "intent-1",
        status: "COMMITTED",
        description: "Computer repair",
        quantity: "1",
        unit_code: "HOUR",
        landed_cost: "10",
        created_at: "2026-07-24T10:00:00Z",
        committed_at: "2026-07-24T10:05:00Z",
      }],
      obligations: [dashboard.obligations[0]!],
    });

    renderView();

    expect(await screen.findByText("Exchange value for: Computer repair")).toBeInTheDocument();
  });

  it("lets the recipient accept a pending share compensation from the member home", async () => {
    const user = userEvent.setup();
    const pending = {
      id: "compensation-1",
      recipient_member_id: dashboard.profile.member_id,
      responsible_member_id: "member-2",
      amount: "15",
      denomination: "shares",
      status: "PENDING_ACCEPTANCE",
      authorized_at: "2026-07-28T10:00:00Z",
      version: 1,
    } as CompensationTransfer;
    vi.mocked(risk.getCompensations).mockResolvedValue([pending]);
    vi.mocked(risk.acceptCompensation).mockResolvedValue({
      event_id: "event-compensation",
      object_id: pending.id,
      replayed: false,
    });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);

    renderView();

    expect(await screen.findByRole("heading", { name: "My compensation" })).toBeInTheDocument();
    expect(screen.getByText("15 shares")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Accept compensation" }));

    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("15 shares"));
    expect(risk.acceptCompensation).toHaveBeenCalledWith(pending, expect.anything());
  });
});
