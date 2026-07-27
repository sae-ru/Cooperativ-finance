import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Cooperative, Member, MemberMergeCase, Principal } from "./api/admin";
import * as admin from "./api/admin";
import MemberMergeSection from "./MemberMergeSection";
import i18n from "./i18n";

vi.mock("./api/admin", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api/admin")>()),
  getMemberMergeCases: vi.fn(),
  getSecurityState: vi.fn(),
  requestMemberMerge: vi.fn(),
  decideMemberMerge: vi.fn(),
  verifyTotpStepUp: vi.fn(),
}));

const cooperative: Cooperative = {
  id: "30000000-0000-0000-0000-000000000001",
  code: "farm-coop",
  name: "Фермерский кооператив",
  status: "ACTIVE",
  created_at: "2026-07-27T08:00:00Z",
  updated_at: "2026-07-27T08:00:00Z",
  version: 1,
};

const members: Member[] = [
  {
    id: "40000000-0000-0000-0000-000000000001",
    display_name: "Анна, дубль",
    registered_by_cooperative_id: cooperative.id,
    merged_into_member_id: null,
    status: "PENDING_VERIFICATION",
    created_at: "2026-07-27T08:00:00Z",
    updated_at: "2026-07-27T08:00:00Z",
    version: 1,
  },
  {
    id: "40000000-0000-0000-0000-000000000002",
    display_name: "Анна Петрова",
    registered_by_cooperative_id: cooperative.id,
    merged_into_member_id: null,
    status: "ACTIVE",
    created_at: "2026-07-27T08:00:00Z",
    updated_at: "2026-07-27T08:00:00Z",
    version: 2,
  },
];

const sourceMember = members[0]!;
const survivorMember = members[1]!;

function principal(role: "DATA_STEWARD" | "SECURITY_ADMIN", userId: string): Principal {
  return {
    user_id: userId,
    login: role.toLowerCase(),
    member_id: "40000000-0000-0000-0000-000000000099",
    must_change_password: false,
    roles: [
      {
        assignment_id: "50000000-0000-0000-0000-000000000001",
        role,
        cooperative_id: role === "SECURITY_ADMIN" ? null : cooperative.id,
        source: "ASSIGNMENT",
      },
    ],
  };
}

function mergeCase(overrides: Partial<MemberMergeCase> = {}): MemberMergeCase {
  return {
    id: "60000000-0000-0000-0000-000000000001",
    cooperative_id: cooperative.id,
    source_member_id: sourceMember.id,
    survivor_member_id: survivorMember.id,
    source_expected_version: 1,
    survivor_expected_version: 2,
    evidence_refs: ["case:duplicate-101"],
    reason_code: "CONFIRMED_DUPLICATE",
    blocker_summary: { codes: [], references: {} },
    status: "PENDING_REVIEW",
    requested_by_user_id: "70000000-0000-0000-0000-000000000001",
    decided_by_user_id: null,
    decision_reason_code: null,
    created_at: "2026-07-27T08:00:00Z",
    expires_at: "2026-07-28T08:00:00Z",
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
      <MemberMergeSection
        principal={activePrincipal}
        cooperatives={[cooperative]}
        members={members}
      />
    </QueryClientProvider>,
  );
}

describe("MemberMergeSection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.setItem("coop.language", "ru");
    void i18n.changeLanguage("ru");
    vi.mocked(admin.getMemberMergeCases).mockResolvedValue([]);
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
    vi.mocked(admin.requestMemberMerge).mockResolvedValue({
      event_id: "80000000-0000-0000-0000-000000000001",
      object_id: "60000000-0000-0000-0000-000000000001",
      status: "PENDING_REVIEW",
      replayed: false,
    });
    vi.mocked(admin.decideMemberMerge).mockResolvedValue({
      event_id: "80000000-0000-0000-0000-000000000002",
      object_id: "60000000-0000-0000-0000-000000000001",
      status: "APPROVED",
      replayed: false,
    });
  });

  it("lets a data steward create a merge case with a safe evidence reference", async () => {
    const user = userEvent.setup();
    renderSection(principal("DATA_STEWARD", "90000000-0000-0000-0000-000000000001"));

    await screen.findByRole("heading", { name: "Проверить две карточки участника" });
    await user.selectOptions(screen.getByLabelText(/Ошибочная карточка/u), sourceMember.id);
    await user.selectOptions(screen.getByLabelText(/Правильная карточка/u), survivorMember.id);
    await user.type(screen.getByLabelText(/Ссылка на доказательство/u), "case:duplicate-101");
    await user.click(screen.getByRole("button", { name: "Проверить и отправить" }));

    await waitFor(() =>
      expect(admin.requestMemberMerge).toHaveBeenCalledWith(
        {
          cooperative_id: cooperative.id,
          source_member_id: sourceMember.id,
          survivor_member_id: survivorMember.id,
          source_expected_version: 1,
          survivor_expected_version: 2,
          evidence_refs: ["case:duplicate-101"],
          reason_code: "CONFIRMED_DUPLICATE",
        },
        expect.anything(),
      ),
    );
  });

  it("explains blockers without exposing database constants", async () => {
    vi.mocked(admin.getMemberMergeCases).mockResolvedValue([
      mergeCase({
        status: "BLOCKED",
        blocker_summary: {
          codes: [
            "IDENTITY_ACCOUNT_CONFLICT",
            "IDENTITY_DEFAULT_PICKUP_CONFLICT",
            "IDENTITY_DEFAULT_DELIVERY_CONFLICT",
          ],
          references: { "risk.share_accounts.member_id": 2 },
        },
      }),
    ]);
    renderSection(principal("DATA_STEWARD", "90000000-0000-0000-0000-000000000001"));

    expect(await screen.findByText("Сначала устраните препятствия")).toBeInTheDocument();
    expect(screen.getByText(/У обеих карточек есть логины/u)).toBeInTheDocument();
    expect(screen.getByText(/адрес забора по умолчанию/u)).toBeInTheDocument();
    expect(screen.getByText(/адрес доставки по умолчанию/u)).toBeInTheDocument();
    expect(screen.getByText(/Паевые счета, ответственность или лимиты: 2/u)).toBeInTheDocument();
    expect(screen.queryByText("risk.share_accounts.member_id")).not.toBeInTheDocument();
  });

  it("requires an independent security confirmation before applying the merge", async () => {
    vi.mocked(admin.getMemberMergeCases).mockResolvedValue([mergeCase()]);
    renderSection(principal("SECURITY_ADMIN", "90000000-0000-0000-0000-000000000002"));

    fireEvent.click(await screen.findByRole("button", { name: "Объединить" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить решение" }));

    await waitFor(() =>
      expect(admin.decideMemberMerge).toHaveBeenCalledWith(
        expect.objectContaining({ id: "60000000-0000-0000-0000-000000000001" }),
        true,
        "INDEPENDENT_SECURITY_REVIEW",
      ),
    );
    expect(admin.verifyTotpStepUp).not.toHaveBeenCalled();
  });
});