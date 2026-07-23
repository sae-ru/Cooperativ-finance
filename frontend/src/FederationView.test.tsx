import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import FederationView from "./FederationView";
import type { Principal } from "./api/admin";
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

const node: federation.FederationNode = {
  id: "10000000-0000-0000-0000-000000000001",
  node_code: "peer-east-01",
  display_name: "Восточный узел",
  owner_organization_id: "20000000-0000-0000-0000-000000000001",
  territory: "Восточный район",
  purpose: "Обмен товарами первой необходимости",
  status: "LIMITED",
  trust_level: "STANDARD",
  capabilities: ["TEST_EXCHANGE"],
  supported_protocols: ["1.0"],
  supported_policies: { federation: 1 },
  last_sync_at: null,
  last_checkpoint_hash: null,
  created_at: "2026-07-21T10:00:00Z",
  updated_at: "2026-07-21T10:00:00Z",
  version: 4,
};

const application: federation.NodeApplication = {
  id: "30000000-0000-0000-0000-000000000001",
  node_id: node.id,
  status: "AUDIT_PENDING",
  requested_capabilities: ["TEST_EXCHANGE"],
  requested_limits: { maximum_package_value: "100" },
  requested_data_scopes: { products: ["essential"] },
  evidence_ids: ["evidence-1"],
  created_by_user_id: "40000000-0000-0000-0000-000000000001",
  identity_verified_by_user_id: "40000000-0000-0000-0000-000000000002",
  audit_decided_by_user_id: null,
  created_at: "2026-07-21T10:00:00Z",
  submitted_at: "2026-07-21T10:10:00Z",
  identity_verified_at: "2026-07-21T10:20:00Z",
  audit_decided_at: null,
  version: 6,
};

function principal(role: "NODE_AUDITOR" | "NODE_SECURITY_ADMIN"): Principal {
  return {
    user_id: role === "NODE_AUDITOR" ? "40000000-0000-0000-0000-000000000003" : "40000000-0000-0000-0000-000000000002",
    login: role.toLowerCase(),
    member_id: "50000000-0000-0000-0000-000000000001",
    must_change_password: false,
    roles: [{ assignment_id: `assignment-${role}`, role, cooperative_id: null }],
  };
}

function renderView(value: Principal) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <FederationView principal={value} />
    </QueryClientProvider>,
  );
}

describe("FederationView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(federation.getFederationNodes).mockResolvedValue([node]);
    vi.mocked(federation.getNodeApplications).mockResolvedValue([application]);
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
    vi.mocked(admin.getUsers).mockResolvedValue([]);
    vi.mocked(admin.getRoles).mockResolvedValue([]);
    vi.mocked(federation.decideNodeAudit).mockResolvedValue({ event_id: "event-1", object_id: application.id, replayed: false });
    vi.mocked(federation.openNodeIncident).mockResolvedValue({ event_id: "event-2", object_id: "incident-1", replayed: false });
  });

  it("gives the independent node auditor the audit decision without security custody commands", async () => {
    const user = userEvent.setup();
    renderView(principal("NODE_AUDITOR"));

    expect(await screen.findByText(/peer-east-01/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Подключение" }));
    await user.click(await screen.findByTitle("Одобрить аудит"));

    await waitFor(() => expect(federation.decideNodeAudit).toHaveBeenCalledWith(
      application,
      true,
      "Независимый аудит завершён.",
    ));
    expect(screen.queryByTitle("Выдать challenge")).not.toBeInTheDocument();
  });

  it("lets the node security administrator quarantine an incident with evidence", async () => {
    const user = userEvent.setup();
    vi.mocked(federation.getFederationNodes).mockResolvedValue([{ ...node, status: "ACTIVE" }]);
    vi.mocked(federation.getNodeContracts).mockResolvedValue([{
      node_id: node.id,
      status: "ACTIVE",
      contract_number: "TRUST-ACTIVE-01",
    } as unknown as federation.NodeTrustContract]);
    renderView(principal("NODE_SECURITY_ADMIN"));

    await user.click(await screen.findByRole("button", { name: "Безопасность" }));
    await user.selectOptions(screen.getAllByLabelText("Узел")[0]!, node.id);
    await user.selectOptions(screen.getByLabelText("Тяжесть"), "CRITICAL");
    await user.type(screen.getByLabelText("ID доказательства"), "evidence-incident-1");
    await user.type(screen.getByLabelText("Описание"), "Подпись контрольной точки не совпала.");
    await user.click(screen.getByRole("button", { name: "Изолировать узел" }));

    await waitFor(() => expect(federation.openNodeIncident).toHaveBeenCalledWith(node.id, {
      incident_type: "INTEGRITY_FAILURE",
      severity: "CRITICAL",
      earliest_compromise_at: null,
      description: "Подпись контрольной точки не совпала.",
      evidence_ids: ["evidence-incident-1"],
    }));
  });
});
