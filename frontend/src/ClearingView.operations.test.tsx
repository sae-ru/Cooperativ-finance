import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Principal, RoleCode } from "./api/admin";
import * as clearing from "./api/clearing";
import type {
  ClearingCycle,
  ClearingDispute,
  ClearingEntry,
  ClearingPolicy,
} from "./api/clearing";
import ClearingView from "./ClearingView";

vi.mock("./api/admin", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/admin")>();
  return { ...actual, getCooperatives: vi.fn() };
});

vi.mock("./api/inventory", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/inventory")>();
  return { ...actual, getInventoryMembers: vi.fn(), getUnits: vi.fn(), uploadEvidence: vi.fn() };
});

vi.mock("./api/clearing", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/clearing")>();
  return Object.fromEntries(
    Object.entries(actual).map(([key, value]) => [
      key,
      typeof value === "function" ? vi.fn() : value,
    ]),
  );
});

const cooperativeId = "10000000-0000-4000-8000-000000000001";
const memberA = "10000000-0000-4000-8000-000000000002";
const memberB = "10000000-0000-4000-8000-000000000003";
const unitId = "10000000-0000-4000-8000-000000000004";
const hashA = `sha256:${"a".repeat(64)}`;
const hashB = `sha256:${"b".repeat(64)}`;
const hashC = `sha256:${"c".repeat(64)}`;

const policy = {
  id: "20000000-0000-4000-8000-000000000001",
  cooperative_id: cooperativeId,
  policy_version: 3,
  valuation_unit_id: unitId,
  algorithm_id: "LOCAL_NETTING",
  algorithm_version: "1.0.0",
  decimal_scale: 2,
  rounding_mode: "DOWN",
  minimum_operation: "0.01",
  max_iterations: 1000,
  max_cycle_length: 6,
  dispute_window_seconds: 3600,
  required_approvals: 1,
  liquidity_order: ["A", "B", "UNASSESSED"],
  terms_hash: hashA,
  status: "ACTIVE",
  proposed_by_member_id: memberA,
  approved_by_member_id: memberB,
  created_at: "2035-01-01T00:00:00Z",
  approved_at: "2035-01-01T00:01:00Z",
  version: 2,
} as ClearingPolicy;

function cycle(status: string): ClearingCycle {
  return {
    id: "30000000-0000-4000-8000-000000000001",
    cooperative_id: cooperativeId,
    policy_id: policy.id,
    cycle_code: "WEEK-2035-02",
    period_start: "2035-01-08T00:00:00Z",
    period_end: "2035-01-15T00:00:00Z",
    status,
    collected_count: 2,
    input_hash: ["DRAFT", "COLLECTING"].includes(status) ? null : hashA,
    parameters_hash: ["DRAFT", "COLLECTING", "INPUT_FROZEN"].includes(status) ? null : hashB,
    result_hash: ["DRAFT", "COLLECTING", "INPUT_FROZEN"].includes(status) ? null : hashC,
    dispute_until: status === "DISPUTE_WINDOW" ? "2035-01-15T01:00:00Z" : null,
    created_by_member_id: memberA,
    created_event_id: "event-created",
    previewed_at: ["DRAFT", "COLLECTING", "INPUT_FROZEN"].includes(status) ? null : "2035-01-15T00:01:00Z",
    finalized_at: ["FINALIZED", "RECONCILED"].includes(status) ? "2035-01-15T02:00:00Z" : null,
    reconciled_at: status === "RECONCILED" ? "2035-01-15T02:01:00Z" : null,
    created_at: "2035-01-15T00:00:00Z",
    updated_at: "2035-01-15T00:01:00Z",
    version: status === "DRAFT" ? 1 : status === "COLLECTING" ? 2 : status === "INPUT_FROZEN" ? 3 : status === "PREVIEWED" ? 4 : status === "DISPUTE_WINDOW" ? 5 : status === "DISPUTED" ? 6 : status === "READY_TO_FINALIZE" ? 6 : status === "FINALIZED" ? 7 : 8,
  };
}

const entries = [
  {
    id: "entry-1",
    obligation_id: "obligation-forward",
    debtor_member_id: memberA,
    creditor_member_id: memberB,
    amount_before: "12.00",
    cleared_amount: "9.00",
    amount_after: "3.00",
    inclusion_status: "INCLUDED",
    exclusion_reason: null,
    obligation_version: 1,
  },
  {
    id: "entry-2",
    obligation_id: "obligation-reverse",
    debtor_member_id: memberB,
    creditor_member_id: memberA,
    amount_before: "9.00",
    cleared_amount: "9.00",
    amount_after: "0.00",
    inclusion_status: "EXCLUDED",
    exclusion_reason: "LIMIT_APPLIED",
    obligation_version: 1,
  },
] as ClearingEntry[];

const openDispute = {
  id: "dispute-1",
  cycle_id: cycle("DISPUTED").id,
  entry_id: entries[0]!.id,
  reason_code: "AMOUNT_DISPUTED",
  statement: "Проверьте сумму зачета.",
  status: "OPEN",
  opened_by_member_id: memberA,
  created_at: "2035-01-15T00:20:00Z",
  version: 1,
} as ClearingDispute;

function actor(role: RoleCode | null, memberId: string | null = memberA): Principal {
  return {
    user_id: "40000000-0000-4000-8000-000000000001",
    login: role?.toLowerCase() ?? "participant",
    member_id: memberId,
    must_change_password: false,
    roles: role ? [{ assignment_id: "role-1", role, cooperative_id: role === "AUDITOR" ? null : cooperativeId }] : [],
  };
}

function renderView(principal: Principal) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ClearingView principal={principal} />
    </QueryClientProvider>,
  );
}

describe("ClearingView operations", () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    const admin = await import("./api/admin");
    const inventory = await import("./api/inventory");
    vi.mocked(admin.getCooperatives).mockResolvedValue([{
      id: cooperativeId,
      code: "DEMO",
      name: "Демо",
      status: "ACTIVE",
      created_at: "2035-01-01T00:00:00Z",
      version: 1,
    }]);
    vi.mocked(inventory.getInventoryMembers).mockResolvedValue([
      { member_id: memberA, cooperative_id: cooperativeId, display_name: "Анна", member_number: "M-1" },
      { member_id: memberB, cooperative_id: cooperativeId, display_name: "Павел", member_number: "M-2" },
    ]);
    vi.mocked(inventory.getUnits).mockResolvedValue([{
      id: unitId,
      cooperative_id: cooperativeId,
      code: "SHARE",
      name: "Расчетный пай",
      symbol: "PS",
      dimension: "VALUATION",
      decimal_scale: 2,
      status: "ACTIVE",
      created_event_id: "unit-event",
    }]);
    vi.mocked(inventory.uploadEvidence).mockResolvedValue("evidence-1");
    vi.mocked(clearing.getClearingPolicies).mockResolvedValue([policy]);
    vi.mocked(clearing.getClearingCycles).mockResolvedValue([cycle("DRAFT")]);
    vi.mocked(clearing.getClearingInput).mockResolvedValue({
      id: "snapshot-1",
      cycle_id: cycle("DRAFT").id,
      input_version: 1,
      policy_version: 3,
      ordered_payload: {},
      input_hash: hashA,
      frozen_by_member_id: memberA,
      frozen_event_id: "frozen-event",
      frozen_at: "2035-01-15T00:00:30Z",
    });
    vi.mocked(clearing.getClearingEntries).mockResolvedValue(entries);
    vi.mocked(clearing.getClearingPositions).mockResolvedValue([
      { id: "position-1", member_id: memberA, incoming_before: "9", outgoing_before: "12", incoming_cleared: "9", outgoing_cleared: "9", incoming_after: "0", outgoing_after: "3", net_before: "-3", net_after: "-3" },
      { id: "position-2", member_id: memberB, incoming_before: "12", outgoing_before: "9", incoming_cleared: "9", outgoing_cleared: "9", incoming_after: "3", outgoing_after: "0", net_before: "3", net_after: "3" },
    ] as Awaited<ReturnType<typeof clearing.getClearingPositions>>);
    vi.mocked(clearing.getClearingApprovals).mockResolvedValue([{
      id: "approval-1",
      member_id: memberB,
      approved_at: "2035-01-15T00:10:00Z",
      result_hash: hashC,
    }] as Awaited<ReturnType<typeof clearing.getClearingApprovals>>);
    vi.mocked(clearing.getClearingDisputes).mockResolvedValue([]);
    vi.mocked(clearing.getClearingProof).mockResolvedValue({
      id: "proof-1",
      cycle_id: cycle("RECONCILED").id,
      proof_payload: { proof_hash: hashA },
      proof_hash: hashA,
      finalized_event_id: "final-event",
      node_event_hash: hashB,
      created_at: "2035-01-15T02:00:00Z",
    });
    vi.mocked(clearing.getClearingStatements).mockResolvedValue([{
      id: "statement-1",
      cycle_id: cycle("RECONCILED").id,
      member_id: memberA,
      unit_id: unitId,
      statement_hash: hashC,
      statement_payload: { net_after: "-3" },
      created_event_id: "statement-event",
      created_at: "2035-01-15T02:00:00Z",
    }] as Awaited<ReturnType<typeof clearing.getClearingStatements>>);
    vi.mocked(clearing.getClearingAccountingExport).mockResolvedValue({
      id: "accounting-1",
      cycle_id: cycle("RECONCILED").id,
      package_hash: hashB,
      export_payload: { entries: 2 },
      created_event_id: "accounting-event",
      created_at: "2035-01-15T02:01:00Z",
    } as Awaited<ReturnType<typeof clearing.getClearingAccountingExport>>);
    vi.mocked(clearing.verifyClearingProof).mockResolvedValue({
      valid: true,
      input_hash: hashA,
      parameters_hash: hashB,
      result_hash: hashC,
      proof_hash: hashA,
    });
    for (const command of [
      clearing.proposeClearingPolicy,
      clearing.createClearingCycle,
      clearing.collectClearingCycle,
      clearing.freezeClearingInput,
      clearing.previewClearingCycle,
      clearing.markClearingReady,
      clearing.finalizeClearingCycle,
      clearing.reconcileClearingCycle,
      clearing.openClearingDispute,
      clearing.decideClearingDispute,
    ]) {
      vi.mocked(command).mockResolvedValue({ event_id: "event-result", object_id: "object-result", replayed: false });
    }
  });

  it("lets an operator propose policy, create a cycle, and start collection", async () => {
    const user = userEvent.setup();
    renderView(actor("CLEARING_OPERATOR"));

    await user.click(await screen.findByRole("button", { name: "Предложить политику" }));
    await waitFor(() => expect(clearing.proposeClearingPolicy).toHaveBeenCalled());
    await user.click(screen.getByRole("button", { name: "Создать цикл" }));
    await waitFor(() => expect(clearing.createClearingCycle).toHaveBeenCalled());
    await user.click(screen.getByRole("button", { name: "Начать сбор" }));
    await waitFor(() => expect(clearing.collectClearingCycle).toHaveBeenCalled());
  });

  it("shows participant entries and opens an evidence-backed dispute", async () => {
    const user = userEvent.setup();
    const inventory = await import("./api/inventory");
    const disputedCycle = cycle("DISPUTE_WINDOW");
    vi.mocked(clearing.getClearingCycles).mockResolvedValue([disputedCycle]);
    renderView(actor(null));

    await user.click(await screen.findByRole("button", { name: "Обязательства" }));
    expect(await screen.findByText("LIMIT_APPLIED")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Позиции" }));
    expect(await screen.findByText("Анна")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Контроль" }));
    await user.selectOptions(screen.getByLabelText("Обязательство"), entries[0]!.id);
    await user.type(screen.getByLabelText("Заявление"), "Требуется независимая проверка суммы.");
    await user.upload(
      screen.getByLabelText("Доказательство"),
      new File(["evidence"], "dispute.txt", { type: "text/plain" }),
    );
    const disputeForm = screen.getByLabelText("Обязательство").closest("form");
    expect(disputeForm).not.toBeNull();
    fireEvent.submit(disputeForm!);
    await waitFor(() => expect(inventory.uploadEvidence).toHaveBeenCalled());
    await waitFor(() => expect(clearing.openClearingDispute).toHaveBeenCalledWith(
      disputedCycle,
      expect.objectContaining({ evidence_ids: ["evidence-1"] }),
    ));
  });

  it("lets an independent controller decide an open dispute", async () => {
    const user = userEvent.setup();
    const disputedCycle = cycle("DISPUTED");
    vi.mocked(clearing.getClearingCycles).mockResolvedValue([disputedCycle]);
    vi.mocked(clearing.getClearingDisputes).mockResolvedValue([openDispute]);
    renderView(actor("CLEARING_CONTROLLER", memberB));

    await user.click(await screen.findByRole("button", { name: "Контроль" }));
    await user.type(screen.getByLabelText(`Решение ${openDispute.id}`), "Расчет подтвержден.");
    await user.click(screen.getByTitle("Отклонить возражение"));
    await waitFor(() => expect(clearing.decideClearingDispute).toHaveBeenCalledWith(
      disputedCycle,
      openDispute,
      "REJECT",
      "Расчет подтвержден.",
    ));
  });

  it.each([
    ["DISPUTE_WINDOW", "Закрыть окно", "markClearingReady"],
    ["READY_TO_FINALIZE", "Финализировать", "finalizeClearingCycle"],
    ["FINALIZED", "Сверить", "reconcileClearingCycle"],
  ] as const)("runs finalizer stage %s", async (status, label, functionName) => {
    const user = userEvent.setup();
    const current = cycle(status);
    vi.mocked(clearing.getClearingCycles).mockResolvedValue([current]);
    renderView(actor("CLEARING_FINALIZER", memberB));

    await user.click(await screen.findByRole("button", { name: label }));
    await waitFor(() => expect(clearing[functionName]).toHaveBeenCalledWith(current));
  });

  it("verifies a reconciled proof and shows statements with accounting export", async () => {
    const user = userEvent.setup();
    vi.mocked(clearing.getClearingCycles).mockResolvedValue([cycle("RECONCILED")]);
    renderView(actor("AUDITOR", memberA));

    await user.click(await screen.findByRole("button", { name: "Доказательство" }));
    await user.click(await screen.findByRole("button", { name: "Проверить" }));
    expect(await screen.findByText("Доказательство действительно")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Выписки" }));
    expect(await screen.findByText("Учетный пакет")).toBeInTheDocument();
    expect(screen.getAllByTitle(hashB).length).toBeGreaterThan(0);
  });

  it("reports base registry loading errors", async () => {
    vi.mocked(clearing.getClearingCycles).mockRejectedValue(new Error("offline"));
    renderView(actor("AUDITOR", memberA));
    expect(await screen.findByRole("alert")).toHaveTextContent("Операция не выполнена");
  });
});
