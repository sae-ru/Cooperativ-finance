import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Principal } from "./api/admin";
import {
  approveLocalFederatedCycle,
  createFederatedClearingCycle,
  createFederatedClearingPolicy,
  createInterNodeObligation,
  getFederatedClearingCycles,
  getFederatedClearingPolicies,
  getFederatedCycleEvidence,
  getInterNodeObligations,
  recoverFederatedCycle,
  type FederatedClearingCycle,
  type FederatedCycleEvidence,
} from "./api/federatedClearing";
import FederatedClearingView from "./FederatedClearingView";

vi.mock("./features/system/use-system-status", () => ({
  useSystemStatus: () => ({
    data: { node: { code: "NODE-A" } },
    isPending: false,
    error: null,
  }),
}));

vi.mock("./api/federatedClearing", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/federatedClearing")>();
  return Object.fromEntries(
    Object.entries(actual).map(([key, value]) => [
      key,
      typeof value === "function" ? vi.fn() : value,
    ]),
  );
});

const hashA = `sha256:${"a".repeat(64)}`;
const hashB = `sha256:${"b".repeat(64)}`;
const hashC = `sha256:${"c".repeat(64)}`;

const cycle = {
  id: "30000000-0000-4000-8000-000000000001",
  cycle_code: "REGION-WEEK-01",
  coordinator_node_code: "node-a",
  policy_id: "20000000-0000-4000-8000-000000000001",
  period_start: "2035-01-01T00:00:00Z",
  period_end: "2035-01-08T00:00:00Z",
  status: "PROPOSED",
  participant_node_codes: ["node-a", "node-b"],
  affected_node_codes: ["node-a", "node-b"],
  input_hash: hashA,
  result_hash: hashB,
  certificate_hash: null,
  created_by_member_id: "member-1",
  created_event_id: "event-1",
  created_at: "2035-01-08T00:00:00Z",
  updated_at: "2035-01-08T00:01:00Z",
  prepared_at: "2035-01-08T00:00:30Z",
  certified_at: null,
  reconciled_at: null,
  version: 5,
} as FederatedClearingCycle;

function artifact(nodeCode: string, hash: string) {
  return { node_code: nodeCode, payload: { node_code: nodeCode }, hash };
}

const evidence: FederatedCycleEvidence = {
  cycle,
  snapshots: [artifact("node-a", hashA), artifact("node-b", hashB)],
  prepare_receipts: [artifact("node-a", hashB), artifact("node-b", hashC)],
  proposal: { payload: { result_hash: hashB }, hash: hashB },
  approvals: [artifact("node-b", hashC)],
  certificate: null,
  apply_receipts: [],
  proof: null,
};

const controller: Principal = {
  user_id: "user-controller",
  login: "controller",
  member_id: "member-controller",
  must_change_password: false,
  roles: [{
    assignment_id: "role-controller",
    role: "CLEARING_CONTROLLER",
    cooperative_id: "coop-1",
  }],
};

function renderView(principal: Principal) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <FederatedClearingView principal={principal} />
    </QueryClientProvider>,
  );
}

describe("FederatedClearingView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getFederatedClearingPolicies).mockResolvedValue([{
      id: cycle.policy_id,
      policy_code: "REGIONAL-WEEKLY",
      policy_version: 1,
      valuation_unit: "DEMO",
      algorithm_id: "FEDERATED_NETTING",
      algorithm_version: "1.0.0",
      decimal_scale: 2,
      rounding_mode: "DOWN",
      minimum_operation: "0.01",
      max_iterations: 10000,
      max_cycle_length: 8,
      prepare_ttl_seconds: 900,
      policy_hash: hashA,
      status: "ACTIVE",
      created_by_member_id: "member-1",
      created_event_id: "event-policy",
      created_at: "2035-01-01T00:00:00Z",
      version: 1,
    }]);
    vi.mocked(getInterNodeObligations).mockResolvedValue([]);
    vi.mocked(getFederatedClearingCycles).mockResolvedValue([cycle]);
    vi.mocked(getFederatedCycleEvidence).mockResolvedValue(evidence);
    vi.mocked(approveLocalFederatedCycle).mockResolvedValue({
      cycle_id: cycle.id,
      object_id: "approval-1",
      event_id: "event-approval",
      status: "APPROVED",
      replayed: false,
      nodes: [],
    });
    vi.mocked(recoverFederatedCycle).mockResolvedValue({
      cycle_id: cycle.id,
      object_id: cycle.id,
      event_id: null,
      status: "RECONCILED",
      replayed: false,
      nodes: [],
    });
  });

  it("shows per-node evidence and allows only the local controller approval", async () => {
    const user = userEvent.setup();
    renderView(controller);

    expect(await screen.findByRole("heading", { name: "Межузловой клиринг" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "REGION-WEEK-01" })).toBeInTheDocument();
    expect(screen.getAllByText("node-b").length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: "Подтвердить расчет" }));

    await waitFor(() => expect(approveLocalFederatedCycle).toHaveBeenCalledWith(cycle.id));
    expect(screen.queryByRole("button", { name: "Выпустить сертификат" })).not.toBeInTheDocument();
  });

  it("marks certified finality and offers idempotent recovery to the finalizer", async () => {
    const finalCycle = {
      ...cycle,
      status: "COMMITTED_PENDING_APPLY",
      certificate_hash: hashC,
      certified_at: "2035-01-08T00:02:00Z",
    };
    vi.mocked(getFederatedClearingCycles).mockResolvedValue([finalCycle]);
    vi.mocked(getFederatedCycleEvidence).mockResolvedValue({
      ...evidence,
      cycle: finalCycle,
      certificate: { payload: { certificate_hash: hashC }, hash: hashC },
    });
    const user = userEvent.setup();
    renderView({
      ...controller,
      login: "finalizer",
      roles: [{
        assignment_id: "role-finalizer",
        role: "CLEARING_FINALIZER",
        cooperative_id: "coop-1",
      }],
    });

    expect(await screen.findByText("Экономическая финальность")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Довести применение" }));
    await waitFor(() => expect(recoverFederatedCycle).toHaveBeenCalledWith(cycle.id));
    expect(screen.queryByRole("button", { name: "Освободить" })).not.toBeInTheDocument();
  });
  it("creates policies, obligations, and cycles from the operator workspace", async () => {
    const user = userEvent.setup();
    renderView({
      ...controller,
      roles: [
        { assignment_id: "role-operator", role: "CLEARING_OPERATOR", cooperative_id: "coop-1" },
        { assignment_id: "role-finalizer", role: "CLEARING_FINALIZER", cooperative_id: "coop-1" },
      ],
    });

    await screen.findByRole("heading", { name: "REGION-WEEK-01" });

    await user.click(screen.getByTitle("Новая политика"));
    await user.type(screen.getByPlaceholderText("REGIONAL-WEEKLY"), "DAILY-NETTING");
    await user.click(screen.getByRole("button", { name: "Создать" }));
    await waitFor(() => expect(createFederatedClearingPolicy).toHaveBeenCalledWith(expect.objectContaining({
      policy_code: "DAILY-NETTING",
      policy_version: 1,
      rounding_mode: "DOWN",
    })));

    await user.click(screen.getByTitle("Новое обязательство"));
    await user.type(screen.getByLabelText("Узел-дебитор"), "NODE-A");
    await user.type(screen.getByLabelText("Узел-кредитор"), "NODE-B");
    await user.type(screen.getByLabelText("Сумма"), "125.50");
    await user.type(screen.getByLabelText("Источник"), "order-42");
    await user.type(screen.getByLabelText("Хеш события"), hashA);
    await user.click(screen.getByRole("button", { name: "Зарегистрировать" }));
    await waitFor(() => expect(createInterNodeObligation).toHaveBeenCalledWith(expect.objectContaining({
      debtor_node_code: "NODE-A",
      creditor_node_code: "NODE-B",
      amount: "125.5",
      source_event_hash: hashA,
    })));

    await user.click(screen.getByTitle("Новый цикл"));
    await user.type(screen.getByPlaceholderText("REGION-WEEK-01"), "DAILY-42");
    await user.selectOptions(screen.getByLabelText("Политика"), cycle.policy_id);
    await user.type(screen.getByPlaceholderText("node-01, node-02, node-03"), " NODE-A, node-b, , NODE-C ");
    await user.click(screen.getByRole("button", { name: "Открыть цикл" }));
    await waitFor(() => expect(createFederatedClearingCycle).toHaveBeenCalledWith(expect.objectContaining({
      cycle_code: "DAILY-42",
      policy_id: cycle.policy_id,
      participant_node_codes: ["node-a", "node-b", "node-c"],
    })));

    await user.click(screen.getByRole("button", { name: "Обязательства" }));
    expect(screen.getByRole("heading", { name: "Межузловые обязательства" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Политики" }));
    expect(screen.getByRole("heading", { name: "Политики межузлового расчета" })).toBeInTheDocument();
  });
});
