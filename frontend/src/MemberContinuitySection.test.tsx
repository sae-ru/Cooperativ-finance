import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  Cooperative,
  Member,
  MemberContinuityCase,
  Principal,
} from "./api/admin";
import * as admin from "./api/admin";
import i18n from "./i18n";
import MemberContinuitySection from "./MemberContinuitySection";

vi.mock("./api/admin", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api/admin")>()),
  getMemberContinuityCases: vi.fn(),
  getSecurityState: vi.fn(),
  requestMemberContinuity: vi.fn(),
  decideMemberContinuity: vi.fn(),
  verifyTotpStepUp: vi.fn(),
}));

const cooperative: Cooperative = {
  id: "31000000-0000-0000-0000-000000000001",
  code: "farm-coop",
  name: "Фермерский кооператив",
  status: "ACTIVE",
  created_at: "2026-07-27T08:00:00Z",
  updated_at: "2026-07-27T08:00:00Z",
  version: 1,
};

const member: Member = {
  id: "41000000-0000-0000-0000-000000000001",
  display_name: "Мария Иванова",
  registered_by_cooperative_id: cooperative.id,
  merged_into_member_id: null,
  status: "ACTIVE",
  created_at: "2026-07-27T08:00:00Z",
  updated_at: "2026-07-27T08:00:00Z",
  version: 3,
};

function principal(role: "MEMBER_REGISTRAR" | "SECURITY_ADMIN", userId: string): Principal {
  return {
    user_id: userId,
    login: role.toLowerCase(),
    member_id: "41000000-0000-0000-0000-000000000099",
    must_change_password: false,
    roles: [
      {
        assignment_id: "51000000-0000-0000-0000-000000000001",
        role,
        cooperative_id: role === "SECURITY_ADMIN" ? null : cooperative.id,
        source: "ASSIGNMENT",
      },
    ],
  };
}

function continuityCase(
  overrides: Partial<MemberContinuityCase> = {},
): MemberContinuityCase {
  return {
    id: "61000000-0000-0000-0000-000000000001",
    cooperative_id: cooperative.id,
    member_id: member.id,
    case_type: "VOLUNTARY_EXIT",
    previous_member_status: "ACTIVE",
    contained_member_version: 4,
    reference_summary: {
      groups: {
        identity_registry: 2,
        responsibility_shares: 3,
        signed_history: 1,
      },
      total_references: 6,
    },
    review_blockers: [],
    evidence_refs: ["case:exit-101"],
    reason_code: "MEMBER_REQUEST_RECEIVED",
    status: "PENDING_REVIEW",
    requested_by_user_id: "71000000-0000-0000-0000-000000000001",
    decided_by_user_id: null,
    decision_reason_code: null,
    disabled_user_count: 1,
    suspended_membership_count: 1,
    created_at: "2026-07-27T08:00:00Z",
    decided_at: null,
    updated_at: "2026-07-27T08:00:00Z",
    version: 1,
    ...overrides,
  };
}

function renderSection(activePrincipal: Principal) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemberContinuitySection
        principal={activePrincipal}
        cooperatives={[cooperative]}
        members={[member]}
      />
    </QueryClientProvider>,
  );
}

describe("MemberContinuitySection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.setItem("coop.language", "ru");
    void i18n.changeLanguage("ru");
    vi.mocked(admin.getMemberContinuityCases).mockResolvedValue([]);
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
    vi.mocked(admin.requestMemberContinuity).mockResolvedValue({
      event_id: "81000000-0000-0000-0000-000000000001",
      object_id: "61000000-0000-0000-0000-000000000001",
      status: "PENDING_REVIEW",
      replayed: false,
    });
    vi.mocked(admin.decideMemberContinuity).mockResolvedValue({
      event_id: "81000000-0000-0000-0000-000000000002",
      object_id: "61000000-0000-0000-0000-000000000001",
      status: "CONFIRMED",
      replayed: false,
    });
  });

  it("explains immediate containment and creates a death or incapacity case", async () => {
    const user = userEvent.setup();
    renderSection(
      principal("MEMBER_REGISTRAR", "91000000-0000-0000-0000-000000000001"),
    );

    await screen.findByRole("heading", {
      name: "Остановить доступ и начать проверку",
    });
    expect(screen.getByText("Доступ остановится сразу")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Участник"), member.id);
    await user.click(screen.getByText("Смерть или недееспособность"));
    await user.type(
      screen.getByLabelText(/Ссылка на подтверждение/u),
      "registry:notice-101",
    );
    await user.click(
      screen.getByRole("button", { name: "Остановить доступ и отправить" }),
    );

    await waitFor(() =>
      expect(admin.requestMemberContinuity).toHaveBeenCalledWith(
        {
          cooperative_id: cooperative.id,
          member_id: member.id,
          case_type: "DEATH_OR_INCAPACITY",
          expected_member_version: 3,
          evidence_refs: ["registry:notice-101"],
          reason_code: "OFFICIAL_NOTICE_RECEIVED",
        },
        expect.anything(),
      ),
    );
  });

  it("shows grouped references without exposing storage names", async () => {
    vi.mocked(admin.getMemberContinuityCases).mockResolvedValue([continuityCase()]);
    renderSection(
      principal("SECURITY_ADMIN", "91000000-0000-0000-0000-000000000002"),
    );

    expect(await screen.findByText("Ответственность и паи: 3")).toBeInTheDocument();
    expect(screen.getByText("Подписанная история: 1")).toBeInTheDocument();
    expect(screen.getByText("Отключено логинов: 1")).toBeInTheDocument();
    expect(screen.queryByText(/risk\.share_accounts/u)).not.toBeInTheDocument();
  });

  it("requires an independent protected decision", async () => {
    vi.mocked(admin.getMemberContinuityCases).mockResolvedValue([continuityCase()]);
    renderSection(
      principal("SECURITY_ADMIN", "91000000-0000-0000-0000-000000000002"),
    );

    fireEvent.click(
      await screen.findByRole("button", { name: "Подтвердить обстоятельство" }),
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить решение" }));

    await waitFor(() =>
      expect(admin.decideMemberContinuity).toHaveBeenCalledWith(
        expect.objectContaining({ id: "61000000-0000-0000-0000-000000000001" }),
        true,
        "INDEPENDENT_CONFIRMATION",
      ),
    );
    expect(admin.verifyTotpStepUp).not.toHaveBeenCalled();
  });
});