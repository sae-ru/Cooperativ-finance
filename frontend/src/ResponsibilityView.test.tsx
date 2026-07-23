import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ResponsibilityView from "./ResponsibilityView";
import * as admin from "./api/admin";
import * as responsibility from "./api/responsibility";

vi.mock("./api/admin", async () => {
  const actual = await vi.importActual<typeof import("./api/admin")>("./api/admin");
  return { ...actual, getCooperatives: vi.fn() };
});

vi.mock("./api/responsibility", async () => {
  const actual = await vi.importActual<typeof import("./api/responsibility")>(
    "./api/responsibility",
  );
  return Object.fromEntries(
    Object.entries(actual).map(([key, value]) => [
      key,
      typeof value === "function" ? vi.fn() : value,
    ]),
  );
});

const cooperativeId = "30000000-0000-0000-0000-000000000001";
const targetMemberId = "40000000-0000-0000-0000-000000000001";
const targetRoleId = "50000000-0000-0000-0000-000000000001";

function assignment(
  overrides: Partial<responsibility.ResponsibilityAssignment> = {},
): responsibility.ResponsibilityAssignment {
  return {
    id: "60000000-0000-0000-0000-000000000001",
    cooperative_id: cooperativeId,
    member_id: targetMemberId,
    role_assignment_id: targetRoleId,
    subject_type: "warehouse_zone",
    subject_id: "70000000-0000-0000-0000-000000000001",
    scope: "Приёмка и сохранность",
    max_exposure: "250.0000",
    exposure_unit: "SHARE_UNIT",
    valid_from: "2026-07-20T10:00:00Z",
    valid_until: null,
    status: "ACTIVE",
    created_by_user_id: "80000000-0000-0000-0000-000000000001",
    approved_by_user_id: "80000000-0000-0000-0000-000000000002",
    accepted_by_user_id: "80000000-0000-0000-0000-000000000003",
    created_event_id: "90000000-0000-0000-0000-000000000001",
    approved_event_id: "90000000-0000-0000-0000-000000000002",
    accepted_event_id: "90000000-0000-0000-0000-000000000003",
    created_at: "2026-07-20T10:00:00Z",
    approved_at: "2026-07-20T10:01:00Z",
    accepted_at: "2026-07-20T10:02:00Z",
    version: 3,
    ...overrides,
  };
}

function renderView(principal: admin.Principal) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ResponsibilityView principal={principal} />
    </QueryClientProvider>,
  );
}

describe("ResponsibilityView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(admin.getCooperatives).mockResolvedValue([
      {
        id: cooperativeId,
        code: "demo",
        name: "Демо кооператив",
        status: "ACTIVE",
        created_at: "2026-07-20T09:00:00Z",
        version: 1,
      },
    ]);
    vi.mocked(responsibility.getResponsibilityCandidates).mockResolvedValue([
      {
        role_assignment_id: targetRoleId,
        user_id: "80000000-0000-0000-0000-000000000003",
        member_id: targetMemberId,
        display_name: "Елена Соколова",
        role_code: "DATA_STEWARD",
      },
    ]);
    vi.mocked(responsibility.previewResponsibility).mockResolvedValue({
      canonicalization_profile: "RFC8785-JCS-1",
      canonical_json: '{"command":"responsibility.propose_assignment"}',
      summary_hash: `sha256:${"a".repeat(64)}`,
    });
    vi.mocked(responsibility.proposeResponsibility).mockResolvedValue({
      event_id: "90000000-0000-0000-0000-000000000004",
      object_id: "60000000-0000-0000-0000-000000000004",
      replayed: false,
    });
    vi.mocked(responsibility.decideResponsibility).mockResolvedValue({
      event_id: "90000000-0000-0000-0000-000000000005",
      object_id: "60000000-0000-0000-0000-000000000001",
      replayed: false,
    });
    vi.mocked(responsibility.acceptResponsibility).mockResolvedValue({
      event_id: "90000000-0000-0000-0000-000000000006",
      object_id: "60000000-0000-0000-0000-000000000001",
      replayed: false,
    });
    vi.mocked(responsibility.getJournalIntegrity).mockResolvedValue({
      ok: true,
      node_id: "a0000000-0000-0000-0000-000000000001",
      checked_events: 3,
      last_sequence: 3,
      last_event_hash: `sha256:${"b".repeat(64)}`,
      failures: [],
    });
    vi.mocked(responsibility.getOutboxStatus).mockResolvedValue({
      pending: 0,
      processing: 0,
      published: 3,
      quarantined: 0,
      oldest_pending_at: null,
    });
    vi.mocked(responsibility.getSignedEvents).mockResolvedValue([
      {
        event_id: "90000000-0000-0000-0000-000000000003",
        event_type: "responsibility.assignment_accepted",
        node_id: "a0000000-0000-0000-0000-000000000001",
        local_sequence: 3,
        aggregate_type: "responsibility_assignment",
        aggregate_id: "60000000-0000-0000-0000-000000000001",
        aggregate_version: 3,
        occurred_at: "2026-07-20T10:02:00Z",
        recorded_at: "2026-07-20T10:02:00Z",
        previous_event_hash: `sha256:${"c".repeat(64)}`,
        payload_hash: `sha256:${"d".repeat(64)}`,
        event_hash: `sha256:${"b".repeat(64)}`,
        canonicalization_profile: "RFC8785-JCS-1",
        canonical_json: '{"event_type":"responsibility.assignment_accepted"}',
        envelope: {},
        signatures: [
          {
            key_id: "b0000000-0000-0000-0000-000000000001",
            key_fingerprint: `sha256:${"e".repeat(64)}`,
            algorithm: "Ed25519",
            scope: "NODE",
            signature_base64: "c2lnbmF0dXJl",
            signed_at: "2026-07-20T10:02:00Z",
          },
        ],
      },
    ]);
  });

  it("binds a proposal to the preview hash and accepts personal responsibility", async () => {
    const currentMember = "40000000-0000-0000-0000-000000000009";
    vi.mocked(responsibility.getResponsibilityAssignments).mockResolvedValue([
      assignment(),
      assignment({
        id: "60000000-0000-0000-0000-000000000009",
        member_id: currentMember,
        status: "PENDING_ACCEPTANCE",
        approved_by_user_id: "80000000-0000-0000-0000-000000000002",
        accepted_by_user_id: null,
        accepted_event_id: null,
        accepted_at: null,
        version: 2,
      }),
    ]);
    const user = userEvent.setup();
    renderView({
      user_id: "80000000-0000-0000-0000-000000000009",
      login: "cooperative-admin",
      member_id: currentMember,
      must_change_password: false,
      roles: [
        {
          assignment_id: "50000000-0000-0000-0000-000000000009",
          role: "COOPERATIVE_ADMIN",
          cooperative_id: cooperativeId,
        },
      ],
    });

    await screen.findByText("Елена Соколова");
    await user.selectOptions(screen.getByLabelText("Ответственный"), targetRoleId);
    await user.type(screen.getByLabelText("Границы ответственности"), "Учёт и сохранность");
    await user.click(screen.getByRole("button", { name: "Сформировать" }));
    await screen.findByText(`sha256:${"a".repeat(64)}`);
    await user.click(screen.getByRole("button", { name: "Создать" }));

    await waitFor(() => expect(responsibility.proposeResponsibility).toHaveBeenCalled());
    expect(vi.mocked(responsibility.proposeResponsibility).mock.calls[0]?.[0]).toMatchObject({
      member_id: targetMemberId,
      expected_summary_hash: `sha256:${"a".repeat(64)}`,
    });
    await user.click(screen.getByRole("button", { name: "Принять" }));
    await waitFor(() => expect(responsibility.acceptResponsibility).toHaveBeenCalled());
    expect(screen.queryByRole("tab", { name: "Журнал" })).not.toBeInTheDocument();
  });

  it("performs independent decisions and inspects node evidence", async () => {
    vi.mocked(responsibility.getResponsibilityAssignments).mockResolvedValue([
      assignment({ status: "PENDING_APPROVAL", approved_by_user_id: null, approved_event_id: null, approved_at: null, version: 1 }),
    ]);
    const user = userEvent.setup();
    renderView({
      user_id: "80000000-0000-0000-0000-000000000020",
      login: "auditor",
      member_id: "40000000-0000-0000-0000-000000000020",
      must_change_password: false,
      roles: [
        {
          assignment_id: "50000000-0000-0000-0000-000000000020",
          role: "AUDITOR",
          cooperative_id: null,
        },
      ],
    });

    await user.click(await screen.findByRole("button", { name: "Одобрить назначение" }));
    await waitFor(() => expect(responsibility.decideResponsibility).toHaveBeenCalledWith(
      "60000000-0000-0000-0000-000000000001",
      true,
    ));
    await user.click(screen.getByRole("button", { name: "Отклонить назначение" }));
    await waitFor(() => expect(responsibility.decideResponsibility).toHaveBeenLastCalledWith(
      "60000000-0000-0000-0000-000000000001",
      false,
    ));
    await user.click(screen.getByRole("tab", { name: "Журнал" }));

    expect(await screen.findByText("Головка цепочки")).toBeInTheDocument();
    expect(screen.getByText("#3")).toBeInTheDocument();
    await user.click(screen.getByText(/Ed25519/));
    expect(screen.getAllByText(/responsibility.assignment_accepted/)).toHaveLength(2);
  });

  it("shows an API failure", async () => {
    vi.mocked(responsibility.getResponsibilityAssignments).mockRejectedValue(
      new admin.AdminApiError("AUTHORIZATION_DENIED", "request-1", 403),
    );
    renderView({
      user_id: "80000000-0000-0000-0000-000000000030",
      login: "steward",
      member_id: targetMemberId,
      must_change_password: false,
      roles: [{ assignment_id: targetRoleId, role: "DATA_STEWARD", cooperative_id: cooperativeId }],
    });
    expect(await screen.findByText(/AUTHORIZATION_DENIED · request-1/)).toBeInTheDocument();
  });
});
