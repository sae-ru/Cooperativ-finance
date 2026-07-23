import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import FederationView from "./FederationView";
import type { Principal, RoleAssignment, RoleCode, UserAccount } from "./api/admin";
import * as admin from "./api/admin";
import * as federation from "./api/federation";

vi.mock("./api/admin", async () => {
  const actual = await vi.importActual<typeof import("./api/admin")>("./api/admin");
  return { ...actual, getUsers: vi.fn(), getRoles: vi.fn() };
});

vi.mock("./api/federation", async () => {
  const actual = await vi.importActual<typeof import("./api/federation")>("./api/federation");
  return Object.fromEntries(
    Object.entries(actual).map(([key, value]) => [key, typeof value === "function" ? vi.fn() : value]),
  );
});

const principal: Principal = {
  user_id: "registrar-1",
  login: "node-registrar",
  member_id: "member-registrar",
  must_change_password: false,
  roles: [{ assignment_id: "registrar-assignment", role: "NODE_REGISTRAR", cooperative_id: null }],
};

function account(index: number): UserAccount {
  return {
    id: `user-${index}`,
    login: `responsible-${index}`,
    member_id: `member-${index}`,
    status: "ACTIVE",
    must_change_password: false,
    locked_until: null,
    last_login_at: null,
    created_at: "2026-07-21T10:00:00Z",
    version: 1,
  };
}

function assignment(index: number, role: RoleCode): RoleAssignment {
  return {
    id: `assignment-${index}`,
    user_id: `user-${index}`,
    role_code: role,
    cooperative_id: null,
    status: "ACTIVE",
    granted_by_user_id: principal.user_id,
    approved_by_user_id: "security-1",
    created_at: "2026-07-21T10:00:00Z",
    version: 1,
  };
}

function renderView() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <FederationView principal={principal} />
    </QueryClientProvider>,
  );
}

describe("FederationView node registration", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(federation.getFederationNodes).mockResolvedValue([]);
    vi.mocked(federation.getNodeApplications).mockResolvedValue([]);
    vi.mocked(federation.getNodeResponsibilities).mockResolvedValue([]);
    vi.mocked(federation.getNodeChallenges).mockResolvedValue([]);
    vi.mocked(federation.getNodeContracts).mockResolvedValue([]);
    vi.mocked(federation.getNodeLimits).mockResolvedValue([]);
    vi.mocked(federation.getNodeBonds).mockResolvedValue([]);
    vi.mocked(federation.getNodeExposures).mockResolvedValue([]);
    vi.mocked(federation.getOfflineEpochs).mockResolvedValue([]);
    vi.mocked(federation.getSyncPackages).mockResolvedValue([]);
    vi.mocked(federation.getSyncConflicts).mockResolvedValue([]);
    vi.mocked(federation.getSyncReceipts).mockResolvedValue([]);
    vi.mocked(federation.getNodeIncidents).mockResolvedValue([]);
    vi.mocked(federation.getFederationPaperForms).mockResolvedValue([]);
    vi.mocked(federation.getNodeKeyRotations).mockResolvedValue([]);
    vi.mocked(admin.getUsers).mockResolvedValue([1, 2, 3, 4, 5].map(account));
    vi.mocked(admin.getRoles).mockResolvedValue([
      assignment(1, "NODE_BUSINESS_OPERATOR"),
      assignment(2, "NODE_TECHNICAL_CUSTODIAN"),
      assignment(3, "NODE_SECURITY_ADMIN"),
      assignment(4, "NODE_BUSINESS_OPERATOR"),
      assignment(5, "NODE_AUDITOR"),
    ]);
    vi.mocked(federation.createNodeApplication).mockResolvedValue({
      event_id: "event-1",
      object_id: "application-1",
      replayed: false,
    });
  });

  it("submits a node only with five named responsibility assignments", async () => {
    const user = userEvent.setup();
    renderView();

    await user.click(await screen.findByRole("button", { name: "Подключение" }));
    await user.type(screen.getByLabelText("Код узла"), "REGION-02");
    await user.type(screen.getByLabelText("Название"), "Резервный узел");
    await user.type(screen.getByLabelText("Владелец"), "Кооператив Резерв");
    await user.type(screen.getByLabelText("Регистрационный код"), "ORG-002");
    await user.type(screen.getByLabelText("Юрисдикция"), "LOCAL");
    await user.type(screen.getByLabelText("Территория"), "Северный район");
    await user.type(screen.getByLabelText("Контакт владельца"), "security@example.test");
    await user.type(screen.getByLabelText("Endpoint"), "https://peer.example.test");
    await user.type(screen.getByLabelText("Релиз"), "1.0.0");
    await user.type(screen.getByLabelText("Назначение"), "Резервный обмен критическими товарами.");
    await user.type(screen.getByLabelText("Ed25519 public key, base64"), "cHVibGljLWtleQ==");
    await user.type(screen.getByLabelText("ID доказательства"), "evidence-node-1");
    await user.selectOptions(screen.getByLabelText("Подписант владельца"), "assignment-1");
    await user.selectOptions(screen.getByLabelText("Технический хранитель"), "assignment-2");
    await user.selectOptions(screen.getByLabelText("Администратор безопасности"), "assignment-3");
    await user.selectOptions(screen.getByLabelText("Оператор деятельности"), "assignment-4");
    await user.selectOptions(screen.getByLabelText("Аудитор узла"), "assignment-5");
    await user.click(screen.getByRole("button", { name: "Зарегистрировать" }));

    await waitFor(() => expect(federation.createNodeApplication).toHaveBeenCalled());
    const payload = vi.mocked(federation.createNodeApplication).mock.calls[0]?.[0] as {
      node_code: string;
      network_endpoints: Array<{ uri: string }>;
      responsible_parties: Array<{ member_id: string; role_assignment_id: string; role_code: string }>;
    };
    expect(payload.node_code).toBe("REGION-02");
    expect(payload.network_endpoints).toEqual([{ transport: "HTTPS", uri: "https://peer.example.test" }]);
    expect(payload.responsible_parties).toHaveLength(5);
    expect(new Set(payload.responsible_parties.map((item) => item.member_id)).size).toBe(5);
    expect(payload.responsible_parties.map((item) => item.role_code)).toEqual([
      "OWNER_SIGNATORY",
      "TECHNICAL_CUSTODIAN",
      "SECURITY_ADMINISTRATOR",
      "BUSINESS_OPERATOR",
      "NODE_AUDITOR",
    ]);
  });
});
