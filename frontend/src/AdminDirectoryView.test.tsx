import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AdminDirectoryView from "./AdminDirectoryView";
import * as admin from "./api/admin";
import * as federation from "./api/federation";
import * as system from "./api/system";

vi.mock("./api/admin", async (importOriginal) => ({
  ...await importOriginal<typeof import("./api/admin")>(),
  createCooperative: vi.fn(),
  createMember: vi.fn(),
  createMembership: vi.fn(),
  createUser: vi.fn(),
  getCooperatives: vi.fn(),
  getMembers: vi.fn(),
  getMemberships: vi.fn(),
  getUsers: vi.fn(),
  transitionCooperative: vi.fn(),
  transitionMember: vi.fn(),
  transitionMembership: vi.fn(),
  transitionUser: vi.fn(),
}));
vi.mock("./api/federation", async (importOriginal) => ({
  ...await importOriginal<typeof import("./api/federation")>(),
  getFederationNodes: vi.fn(),
}));
vi.mock("./api/system", async (importOriginal) => ({
  ...await importOriginal<typeof import("./api/system")>(),
  fetchSystemStatus: vi.fn(),
}));

const cooperativeId = "30000000-0000-0000-0000-000000000001";
const memberId = "40000000-0000-0000-0000-000000000001";
const principal: admin.Principal = {
  user_id: "10000000-0000-0000-0000-000000000001",
  login: "security",
  member_id: null,
  must_change_password: false,
  roles: [
    { assignment_id: "role-security", role: "SECURITY_ADMIN", cooperative_id: null },
    { assignment_id: "role-registrar", role: "MEMBER_REGISTRAR", cooperative_id: cooperativeId },
    { assignment_id: "role-node", role: "NODE_REGISTRAR", cooperative_id: null },
  ],
};

function renderView(onManageNodes = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><AdminDirectoryView principal={principal} onManageNodes={onManageNodes} /></QueryClientProvider>);
  return onManageNodes;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(admin.getCooperatives).mockResolvedValue([{
    id: cooperativeId,
    code: "demo-coop",
    name: "Демо кооператив",
    status: "ACTIVE",
    created_at: "2026-07-27T08:00:00Z",
    updated_at: "2026-07-27T08:00:00Z",
    version: 1,
  }]);
  vi.mocked(admin.getMembers).mockResolvedValue([{
    id: memberId,
    display_name: "Анна Петрова",
    registered_by_cooperative_id: cooperativeId,
    status: "ACTIVE",
    created_at: "2026-07-27T08:00:00Z",
    updated_at: "2026-07-27T08:00:00Z",
    version: 2,
  }]);
  vi.mocked(admin.getMemberships).mockResolvedValue([{
    id: "50000000-0000-0000-0000-000000000001",
    cooperative_id: cooperativeId,
    member_id: memberId,
    member_number: "D-100",
    status: "ACTIVE",
    joined_at: "2026-07-27T08:00:00Z",
    ended_at: null,
    created_at: "2026-07-27T08:00:00Z",
    updated_at: "2026-07-27T08:00:00Z",
    version: 1,
  }]);
  vi.mocked(admin.getUsers).mockResolvedValue([{
    id: "10000000-0000-0000-0000-000000000002",
    login: "farmer",
    member_id: memberId,
    status: "ACTIVE",
    must_change_password: false,
    locked_until: null,
    last_login_at: "2026-07-27T09:00:00Z",
    created_at: "2026-07-27T08:00:00Z",
    version: 3,
  }]);
  vi.mocked(system.fetchSystemStatus).mockResolvedValue({
    status: "OPERATIONAL",
    node: {
      id: "20000000-0000-0000-0000-000000000001",
      code: "node-local-01",
      display_name: "Локальный узел",
      environment: "pilot",
      demo_data_loaded: false,
    },
    release: { version: "0.1.0", schema_revision: "0029_identity_registry_scope" },
    checks: [],
    worker: { status: "RUNNING", last_seen_at: "2026-07-27T10:00:00Z" },
    notices: [],
  });
  vi.mocked(federation.getFederationNodes).mockResolvedValue([{
    id: "60000000-0000-0000-0000-000000000001",
    node_code: "node-remote-01",
    display_name: "Соседний узел",
    owner_organization_id: "70000000-0000-0000-0000-000000000001",
    territory: "Северный район",
    purpose: "EXCHANGE",
    status: "ACTIVE",
    trust_level: "STANDARD",
    capabilities: ["TEST_EXCHANGE"],
    supported_protocols: ["1.0"],
    supported_policies: { federation: 1 },
    last_sync_at: null,
    last_checkpoint_hash: null,
    created_at: "2026-07-27T08:00:00Z",
    updated_at: "2026-07-27T08:00:00Z",
    version: 1,
  }]);
  for (const command of [
    admin.createCooperative,
    admin.createMember,
    admin.createMembership,
    admin.createUser,
    admin.transitionCooperative,
    admin.transitionMember,
    admin.transitionMembership,
    admin.transitionUser,
  ]) {
    vi.mocked(command).mockResolvedValue({ event_id: "event-id", object_id: "object-id" });
  }
});

describe("AdminDirectoryView", () => {
  it("keeps organizations, members, memberships, accounts, and nodes separate", async () => {
    const user = userEvent.setup();
    const manageNodes = renderView();

    expect(await screen.findByRole("heading", { name: "Участники" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Узлы" })).toHaveAttribute("title", "Узлы");
    expect(screen.getByText("Анна Петрова")).toBeInTheDocument();
    expect(screen.getAllByText("Демо кооператив").length).toBeGreaterThan(0);

    await user.click(screen.getByRole("tab", { name: "Организации" }));
    await user.type(screen.getByLabelText("Код организации"), "north-coop");
    await user.type(screen.getByLabelText("Название организации"), "Северный кооператив");
    await user.click(screen.getByRole("button", { name: "Создать организацию" }));
    await waitFor(() => expect(admin.createCooperative).toHaveBeenCalledWith({ code: "north-coop", name: "Северный кооператив" }));

    await user.click(screen.getByRole("tab", { name: "Членства" }));
    expect(await screen.findByText("D-100")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Новый статус членства D-100"), "SUSPENDED");
    await waitFor(() => expect(admin.transitionMembership).toHaveBeenCalledWith(expect.objectContaining({ id: "50000000-0000-0000-0000-000000000001" }), "SUSPENDED"));

    await user.click(screen.getByRole("tab", { name: "Учетные записи" }));
    expect(await screen.findByText("farmer")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Отключить вход" }));
    await waitFor(() => expect(admin.transitionUser).toHaveBeenCalledWith(expect.objectContaining({ login: "farmer" }), "DISABLED"));

    await user.click(screen.getByRole("tab", { name: "Узлы" }));
    expect(await screen.findByText("Локальный узел")).toBeInTheDocument();
    expect(screen.getByText("Работает")).toBeInTheDocument();
    expect(screen.getByText("Пилот")).toBeInTheDocument();
    expect(screen.getByText("Соседний узел")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Управление узлами" }));
    expect(manageNodes).toHaveBeenCalledOnce();
  });
});
