import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import i18n from "./i18n";
import type { Principal } from "./api/admin";
import {
  acceptCompensation,
  type CompensationTransfer,
} from "./api/risk";
import CompensationPanel from "./CompensationPanel";

vi.mock("./api/inventory", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/inventory")>();
  return { ...actual, uploadEvidence: vi.fn() };
});

vi.mock("./api/risk", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/risk")>();
  return {
    ...actual,
    acceptCompensation: vi.fn(),
    authorizeCompensation: vi.fn(),
    voidCompensation: vi.fn(),
  };
});

vi.mock("./api/trust", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/trust")>();
  return { ...actual, getTrustDecisions: vi.fn() };
});

const cooperativeId = "10000000-0000-4000-8000-000000000001";
const responsibleId = "10000000-0000-4000-8000-000000000002";
const recipientId = "10000000-0000-4000-8000-000000000003";

const transfer = {
  id: "20000000-0000-4000-8000-000000000001",
  cooperative_id: cooperativeId,
  liability_case_id: "20000000-0000-4000-8000-000000000002",
  trust_case_id: "20000000-0000-4000-8000-000000000003",
  trust_decision_id: "20000000-0000-4000-8000-000000000004",
  commitment_id: "20000000-0000-4000-8000-000000000005",
  source_account_id: "20000000-0000-4000-8000-000000000006",
  destination_account_id: "20000000-0000-4000-8000-000000000007",
  responsible_member_id: responsibleId,
  recipient_member_id: recipientId,
  amount: "15.000000000000",
  denomination: "DEMO_SHARE",
  rationale: "Final independent decision.",
  status: "PENDING_ACCEPTANCE",
  authorization_evidence_refs: [],
  authorized_by_user_id: "20000000-0000-4000-8000-000000000008",
  authorized_by_member_id: "20000000-0000-4000-8000-000000000009",
  authorized_role_assignment_id: "20000000-0000-4000-8000-000000000010",
  authorized_event_id: "20000000-0000-4000-8000-000000000011",
  source_account_version_before: 1,
  destination_account_version_at_authorization: 1,
  commitment_version_before: 2,
  accepted_by_user_id: null,
  accepted_by_member_id: null,
  accepted_role_assignment_id: null,
  accepted_event_id: null,
  voided_by_user_id: null,
  voided_by_member_id: null,
  voided_role_assignment_id: null,
  voided_event_id: null,
  void_reason: null,
  void_evidence_refs: null,
  source_balance_before: null,
  source_balance_after: null,
  destination_balance_before: null,
  destination_balance_after: null,
  authorized_at: "2026-07-28T10:00:00Z",
  accepted_at: null,
  voided_at: null,
  updated_at: "2026-07-28T10:00:00Z",
  version: 1,
} as CompensationTransfer;

function principal(memberId: string): Principal {
  return {
    user_id: "30000000-0000-4000-8000-000000000001",
    login: "farmer",
    member_id: memberId,
    must_change_password: false,
    roles: [],
  };
}

function renderPanel(value: Principal, onDone = vi.fn().mockResolvedValue(undefined)) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return {
    onDone,
    ...render(
      <QueryClientProvider client={client}>
        <CompensationPanel
          principal={value}
          liabilities={[]}
          commitments={[]}
          accounts={[]}
          transfers={[transfer]}
          trustCases={[]}
          members={[
            {
              member_id: responsibleId,
              cooperative_id: cooperativeId,
              display_name: "Анна",
              member_number: "D-0001",
            },
            {
              member_id: recipientId,
              cooperative_id: cooperativeId,
              display_name: "Иван",
              member_number: "D-0007",
            },
          ]}
          onDone={onDone}
        />
      </QueryClientProvider>,
    ),
  };
}

describe("CompensationPanel", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("ru");
    vi.mocked(acceptCompensation).mockResolvedValue({
      event_id: "event-1",
      object_id: transfer.id,
      replayed: false,
    });
  });

  it("lets only the exact recipient personally accept a pending transfer", async () => {
    const user = userEvent.setup();
    const { onDone } = renderPanel(principal(recipientId));

    await user.click(screen.getByRole("button", { name: "Принять компенсацию" }));

    expect(vi.mocked(acceptCompensation).mock.calls[0]?.[0]).toEqual(transfer);
    await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
  });

  it("does not expose recipient acceptance to another member", () => {
    renderPanel(principal(responsibleId));

    expect(screen.queryByRole("button", { name: "Принять компенсацию" })).not.toBeInTheDocument();
    expect(screen.getByText("Ожидается личное принятие")).toBeInTheDocument();
  });
});
