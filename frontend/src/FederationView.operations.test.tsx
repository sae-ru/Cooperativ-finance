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

const node = {
  id: "node-1",
  node_code: "peer-west-01",
  display_name: "Западный узел",
  territory: "Западный район",
  status: "LIMITED",
  trust_level: "STANDARD",
  last_sync_at: null,
  last_checkpoint_hash: "sha256:checkpoint",
  version: 5,
} as federation.FederationNode;

const contract = {
  id: "contract-1",
  node_id: node.id,
  contract_number: "TRUST-01",
  application_id: "application-1",
  capabilities: ["TEST_EXCHANGE"],
  event_types: ["federation.test_event"],
  federation_limits: { maximum_package_value: "100" },
  max_offline_hours: 24,
  required_protocols: ["1.0"],
  required_policies: { federation: 1 },
  terms_hash: "sha256:contract",
  status: "DRAFT",
  trust_level: "STANDARD",
  valid_from: "2026-07-21T10:00:00Z",
  valid_until: "2027-07-21T10:00:00Z",
  created_at: "2026-07-21T10:00:00Z",
  liability_terms: { ordinary_member_shares_excluded: true },
  version: 2,
} as federation.NodeTrustContract;

const limit = {
  id: "limit-1",
  node_id: node.id,
  capability: "TEST_EXCHANGE",
  max_unsettled_obligations: "100",
  unit: "UNIT",
  terms_hash: "sha256:limit",
  status: "DRAFT",
  version: 3,
} as federation.NodeLimit;

const epoch = {
  id: "epoch-1",
  external_node_id: node.id,
  protocol_version: "1.0",
  status: "OPEN",
  starts_at: "2026-07-21T10:00:00Z",
  expires_at: "2026-07-21T22:00:00Z",
  allowed_event_types: ["federation.test_event"],
  policy_hash: "sha256:policy",
  version: 4,
} as federation.OfflineEpoch;

const syncPackage = {
  id: "package-1",
  peer_node_id: node.id,
  epoch_id: epoch.id,
  direction: "INBOUND",
  status: "SIMULATED",
  source_node_code: node.node_code,
  target_node_code: "local-01",
  protocol_version: "1.0",
  sequence_first: 1,
  sequence_last: 2,
  event_count: 2,
  blob_count: 0,
  archive_size: 1024,
  archive_hash: "sha256:archive",
  manifest_hash: "sha256:manifest",
  simulation_summary: { ready_events: 1, conflicts: 1 },
  rejection_code: null,
  created_at: "2026-07-21T10:00:00Z",
  expires_at: "2026-07-22T10:00:00Z",
  verified_at: "2026-07-21T10:05:00Z",
  simulated_at: "2026-07-21T10:06:00Z",
  applied_at: null,
  version: 5,
} as federation.SyncPackage;

const conflict = {
  id: "conflict-1",
  package_id: syncPackage.id,
  conflict_class: "AGGREGATE_VERSION_COLLISION",
  affected_object_type: "federation.test",
  affected_object_id: "aggregate-1",
  local_event_hash: "sha256:local",
  remote_event_hash: "sha256:remote",
  status: "OPEN",
  decision: null,
  rationale: null,
  version: 2,
} as federation.SyncConflict;

const incident = {
  id: "incident-1",
  node_id: node.id,
  incident_type: "INTEGRITY_FAILURE",
  severity: "HIGH",
  status: "OPEN",
  description: "Контрольная точка требует независимой сверки.",
  created_at: "2026-07-21T11:00:00Z",
  version: 2,
} as federation.NodeIncident;

const auditor: Principal = {
  user_id: "auditor-1",
  login: "node-auditor",
  member_id: "member-auditor-1",
  must_change_password: false,
  roles: [{ assignment_id: "assignment-auditor-1", role: "NODE_AUDITOR", cooperative_id: null }],
};

function renderView() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <FederationView principal={auditor} />
    </QueryClientProvider>,
  );
}

describe("FederationView operational review", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(federation.getFederationNodes).mockResolvedValue([node]);
    vi.mocked(federation.getNodeApplications).mockResolvedValue([]);
    vi.mocked(federation.getNodeResponsibilities).mockResolvedValue([]);
    vi.mocked(federation.getNodeChallenges).mockResolvedValue([]);
    vi.mocked(federation.getNodeContracts).mockResolvedValue([contract]);
    vi.mocked(federation.getNodeLimits).mockResolvedValue([limit]);
    vi.mocked(federation.getNodeBonds).mockResolvedValue([{
      id: "bond-1",
      node_id: node.id,
      reference: "BOND-01",
      amount: "120",
      protected_amount: "20",
      maximum_loss: "100",
      unit: "UNIT",
      status: "ACTIVE",
    } as federation.NodeBond]);
    vi.mocked(federation.getNodeExposures).mockResolvedValue([{
      id: "exposure-1",
      node_id: node.id,
      capability: "TEST_EXCHANGE",
      current_amount: "10",
      reserved_amount: "5",
      unit: "UNIT",
    } as federation.NodeExposure]);
    vi.mocked(federation.getOfflineEpochs).mockResolvedValue([epoch]);
    vi.mocked(federation.getSyncPackages).mockResolvedValue([syncPackage]);
    vi.mocked(federation.getSyncConflicts).mockResolvedValue([conflict]);
    vi.mocked(federation.getSyncReceipts).mockResolvedValue([{
      id: "receipt-1",
      package_id: syncPackage.id,
      receipt_payload: {},
      receipt_hash: "sha256:receipt",
      signature_base64: "signature",
      created_at: "2026-07-21T12:00:00Z",
    }]);
    vi.mocked(federation.getNodeIncidents).mockResolvedValue([incident]);
    vi.mocked(federation.getFederationPaperForms).mockResolvedValue([]);
    vi.mocked(federation.getNodeKeyRotations).mockResolvedValue([]);
    vi.mocked(admin.getUsers).mockResolvedValue([]);
    vi.mocked(admin.getRoles).mockResolvedValue([]);

    const result = { event_id: "event-1", object_id: "object-1", replayed: false };
    vi.mocked(federation.activateFederationNode).mockResolvedValue(result);
    vi.mocked(federation.approveNodeContract).mockResolvedValue(result);
    vi.mocked(federation.approveNodeLimit).mockResolvedValue(result);
    vi.mocked(federation.closeOfflineEpoch).mockResolvedValue(result);
    vi.mocked(federation.applySyncPackage).mockResolvedValue(result);
    vi.mocked(federation.resolveSyncConflict).mockResolvedValue(result);
    vi.mocked(federation.resolveNodeIncident).mockResolvedValue(result);
  });

  it("runs the independent approval and reconciliation queues", async () => {
    const user = userEvent.setup();
    renderView();

    await user.click(await screen.findByTitle("Активировать"));
    await waitFor(() => expect(federation.activateFederationNode).toHaveBeenCalledWith(node));

    await user.click(screen.getByRole("button", { name: "Лимиты" }));
    await user.click((await screen.findByText("Одобрить")).closest("button") as HTMLButtonElement);
    await waitFor(() => expect(federation.approveNodeContract).toHaveBeenCalledWith(contract));
    await user.click(screen.getByTitle("Одобрить"));
    await waitFor(() => expect(federation.approveNodeLimit).toHaveBeenCalledWith(limit));

    await user.click(screen.getByRole("button", { name: "Offline" }));
    await user.click(await screen.findByRole("button", { name: "Закрыть и сверить" }));
    await waitFor(() => expect(federation.closeOfflineEpoch).toHaveBeenCalledWith(epoch));

    await user.click(screen.getByRole("button", { name: "Пакеты" }));
    await user.click(await screen.findByTitle("Применить"));
    await waitFor(() => expect(federation.applySyncPackage).toHaveBeenCalledWith(syncPackage));
    await user.click(screen.getByRole("button", { name: "Оставить локальную" }));
    await waitFor(() => expect(federation.resolveSyncConflict).toHaveBeenCalledWith(
      conflict,
      "KEEP_LOCAL",
      "Сохраняется ранее подтверждённая ветвь.",
    ));

    await user.click(screen.getByRole("button", { name: "Безопасность" }));
    await user.click(await screen.findByRole("button", { name: "Закрыть" }));
    await waitFor(() => expect(federation.resolveNodeIncident).toHaveBeenCalledWith(
      incident,
      "Корректирующие действия и целостность проверены.",
    ));
  });
});
