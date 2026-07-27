import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Principal } from "./api/admin";
import * as admin from "./api/admin";
import type { CustodyContinuityCase } from "./api/custody-continuity";
import * as continuity from "./api/custody-continuity";
import * as inventory from "./api/inventory";
import CustodyContinuitySection from "./CustodyContinuitySection";
import i18n from "./i18n";

vi.mock("./api/custody-continuity", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api/custody-continuity")>()),
  getCustodyContinuityCases: vi.fn(),
  getCustodyContinuitySources: vi.fn(),
  getCustodyContinuityCandidates: vi.fn(),
  requestCustodyContinuity: vi.fn(),
  attestCustodyContinuityItem: vi.fn(),
  decideCustodyContinuity: vi.fn(),
  decideCustodyContinuityCandidate: vi.fn(),
}));

vi.mock("./api/admin", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api/admin")>()),
  getSecurityState: vi.fn(),
  verifyTotpStepUp: vi.fn(),
}));

vi.mock("./api/inventory", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api/inventory")>()),
  uploadEvidence: vi.fn(),
}));

const cooperativeId = "11000000-0000-0000-0000-000000000001";

function principal(
  role: "INVENTORY_CONTROLLER" | "SECURITY_ADMIN" | "WAREHOUSE_CUSTODIAN",
  memberId: string,
): Principal {
  return {
    user_id: `user-${role}`,
    login: role.toLowerCase(),
    member_id: memberId,
    must_change_password: false,
    roles: [
      {
        assignment_id: `role-${role}`,
        role,
        cooperative_id: role === "SECURITY_ADMIN" ? null : cooperativeId,
        source: "ASSIGNMENT",
      },
    ],
  };
}

function continuityCase(
  overrides: Partial<CustodyContinuityCase> = {},
): CustodyContinuityCase {
  return {
    id: "21000000-0000-0000-0000-000000000001",
    cooperative_id: cooperativeId,
    member_continuity_case_id: "22000000-0000-0000-0000-000000000001",
    source_member_id: "source-member",
    source_member_name: "Alex Source",
    warehouse_id: "23000000-0000-0000-0000-000000000001",
    warehouse_name: "North warehouse",
    source_assignment_id: "24000000-0000-0000-0000-000000000001",
    source_assignment_version: 3,
    target_member_id: "candidate-member",
    target_member_name: "Casey Candidate",
    target_role_assignment_id: "25000000-0000-0000-0000-000000000001",
    target_assignment_id: null,
    handover_place: "North warehouse desk",
    temporary_valid_until: "2026-07-29T12:00:00Z",
    evidence_refs: ["registry:notice-1"],
    blocked_reasons: [],
    status: "INVENTORY_PENDING",
    requested_by_user_id: "requester-user",
    decided_by_user_id: null,
    accepted_by_user_id: null,
    decision_reason_code: null,
    created_at: "2026-07-27T12:00:00Z",
    inventory_completed_at: null,
    decided_at: null,
    accepted_at: null,
    updated_at: "2026-07-27T12:00:00Z",
    version: 1,
    items: [
      {
        id: "26000000-0000-0000-0000-000000000001",
        lot_id: "27000000-0000-0000-0000-000000000001",
        lot_number: "LOT-001",
        product_name: "Cabbage",
        unit_symbol: "kg",
        lot_version: 2,
        expected_quantity: "25.000000000000",
        actual_quantity: null,
        status: "PENDING",
        condition_notes: null,
        evidence_ids: [],
        attested_by_user_id: null,
        attested_at: null,
        version: 1,
      },
    ],
    ...overrides,
  };
}

function renderSection(activePrincipal: Principal) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <CustodyContinuitySection principal={activePrincipal} />
    </QueryClientProvider>,
  );
}

describe("CustodyContinuitySection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.setItem("coop.language", "ru");
    void i18n.changeLanguage("ru");
    vi.mocked(continuity.getCustodyContinuityCases).mockResolvedValue([
      continuityCase(),
    ]);
    vi.mocked(continuity.getCustodyContinuitySources).mockResolvedValue([]);
    vi.mocked(continuity.getCustodyContinuityCandidates).mockResolvedValue([]);
    vi.mocked(admin.getSecurityState).mockResolvedValue({
      totp_enabled: true,
      totp_confirmed_at: "2026-07-27T08:00:00Z",
      enrollment_pending: false,
      enrollment_expires_at: null,
      step_up_active: true,
      step_up_method: "TOTP",
      step_up_expires_at: "2026-07-27T08:10:00Z",
      break_glass_grants: 0,
    });
    vi.mocked(inventory.uploadEvidence).mockResolvedValue("evidence-1");
    vi.mocked(continuity.attestCustodyContinuityItem).mockResolvedValue({
      event_id: "event-1",
      object_id: "case-1",
      status: "PENDING_APPROVAL",
      replayed: false,
    });
    vi.mocked(continuity.decideCustodyContinuity).mockResolvedValue({
      event_id: "event-2",
      object_id: "case-1",
      status: "PENDING_ACCEPTANCE",
      replayed: false,
    });
    vi.mocked(continuity.decideCustodyContinuityCandidate).mockResolvedValue({
      event_id: "event-3",
      object_id: "case-1",
      status: "ACCEPTED",
      replayed: false,
    });
  });

  it("keeps the previous custodian visible and lets an independent controller count", async () => {
    const user = userEvent.setup();
    renderSection(
      principal("INVENTORY_CONTROLLER", "controller-member"),
    );

    expect(
      await screen.findByText(
        "До личной приемки партии остаются записаны за прежним хранителем.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Alex Source")).toBeInTheDocument();
    expect(screen.getByText("Casey Candidate")).toBeInTheDocument();

    await user.clear(screen.getByLabelText("Насчитано фактически"));
    await user.type(screen.getByLabelText("Насчитано фактически"), "25");
    await user.type(
      screen.getByLabelText("Состояние товара и упаковки"),
      "Все цело",
    );
    const file = new File(["count"], "count.txt", { type: "text/plain" });
    await user.upload(
      screen.getByLabelText("Добавить фото или акт пересчета"),
      file,
    );
    await user.click(screen.getByRole("button", { name: "Сохранить пересчет" }));

    await waitFor(() =>
      expect(continuity.attestCustodyContinuityItem).toHaveBeenCalledWith(
        expect.objectContaining({ id: continuityCase().id }),
        expect.objectContaining({ lot_number: "LOT-001" }),
        {
          actual_quantity: "25",
          condition_notes: "Все цело",
          evidence_ids: ["evidence-1"],
        },
      ),
    );
  });

  it("renders the protected approval entirely in English", async () => {
    await i18n.changeLanguage("en");
    vi.mocked(continuity.getCustodyContinuityCases).mockResolvedValue([
      continuityCase({ status: "PENDING_APPROVAL", version: 2 }),
    ]);
    renderSection(principal("SECURITY_ADMIN", "security-member"));

    fireEvent.click(
      await screen.findByRole("button", { name: "Approve appointment" }),
    );
    expect(
      screen.getByRole("heading", { name: "Approve temporary custodian" }),
    ).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/[А-Яа-яЁё]/u);
    fireEvent.click(screen.getByRole("button", { name: "Confirm decision" }));

    await waitFor(() =>
      expect(continuity.decideCustodyContinuity).toHaveBeenCalledWith(
        expect.objectContaining({ status: "PENDING_APPROVAL" }),
        true,
        "INDEPENDENT_INVENTORY_REVIEW",
      ),
    );
  });

  it("requires the appointed person and a signed record for final acceptance", async () => {
    const user = userEvent.setup();
    vi.mocked(continuity.getCustodyContinuityCases).mockResolvedValue([
      continuityCase({
        status: "PENDING_ACCEPTANCE",
        target_assignment_id: "28000000-0000-0000-0000-000000000001",
        version: 3,
      }),
    ]);
    renderSection(
      principal("WAREHOUSE_CUSTODIAN", "candidate-member"),
    );

    const accept = await screen.findByRole("button", { name: "Принять лично" });
    expect(accept).toBeDisabled();
    await user.upload(
      screen.getByLabelText("Добавить подписанный акт приемки"),
      new File(["accepted"], "acceptance.txt", { type: "text/plain" }),
    );
    expect(accept).toBeEnabled();
    await user.click(accept);

    await waitFor(() =>
      expect(
        continuity.decideCustodyContinuityCandidate,
      ).toHaveBeenCalledWith(
        expect.objectContaining({ status: "PENDING_ACCEPTANCE" }),
        true,
        ["evidence-1"],
      ),
    );
  });
});
