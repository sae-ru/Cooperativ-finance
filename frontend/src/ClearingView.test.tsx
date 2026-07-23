import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Principal } from "./api/admin";
import {
  approveClearingPreview,
  getClearingApprovals,
  getClearingCycles,
  getClearingDisputes,
  getClearingEntries,
  getClearingInput,
  getClearingPolicies,
  getClearingPositions,
  type ClearingCycle,
  type ClearingPolicy,
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
const operatorId = "10000000-0000-4000-8000-000000000002";
const controllerId = "10000000-0000-4000-8000-000000000003";
const hashA = `sha256:${"a".repeat(64)}`;
const hashB = `sha256:${"b".repeat(64)}`;

const policy = {
  id: "20000000-0000-4000-8000-000000000001",
  cooperative_id: cooperativeId,
  policy_version: 1,
  status: "ACTIVE",
} as ClearingPolicy;

const cycle = {
  id: "30000000-0000-4000-8000-000000000001",
  cooperative_id: cooperativeId,
  policy_id: policy.id,
  cycle_code: "WEEK-2035-01",
  period_start: "2035-01-01T00:00:00Z",
  period_end: "2035-01-08T00:00:00Z",
  status: "PREVIEWED",
  collected_count: 2,
  input_hash: hashA,
  parameters_hash: hashA,
  result_hash: hashB,
  dispute_until: null,
  created_by_member_id: operatorId,
  created_event_id: "event-created",
  previewed_at: "2035-01-08T00:01:00Z",
  finalized_at: null,
  reconciled_at: null,
  created_at: "2035-01-08T00:00:00Z",
  updated_at: "2035-01-08T00:01:00Z",
  version: 4,
} as ClearingCycle;

const principal: Principal = {
  user_id: "40000000-0000-4000-8000-000000000001",
  login: "controller",
  member_id: controllerId,
  must_change_password: false,
  roles: [{
    assignment_id: "50000000-0000-4000-8000-000000000001",
    role: "CLEARING_CONTROLLER",
    cooperative_id: cooperativeId,
  }],
};

function renderView() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ClearingView principal={principal} />
    </QueryClientProvider>,
  );
}

describe("ClearingView", () => {
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
      { member_id: operatorId, cooperative_id: cooperativeId, display_name: "Оператор", member_number: "M-1" },
      { member_id: controllerId, cooperative_id: cooperativeId, display_name: "Контролер", member_number: "M-2" },
    ]);
    vi.mocked(inventory.getUnits).mockResolvedValue([]);
    vi.mocked(getClearingPolicies).mockResolvedValue([policy]);
    vi.mocked(getClearingCycles).mockResolvedValue([cycle]);
    vi.mocked(getClearingInput).mockResolvedValue({
      id: "snapshot-1",
      cycle_id: cycle.id,
      input_version: 1,
      policy_version: 1,
      ordered_payload: {},
      input_hash: hashA,
      frozen_by_member_id: operatorId,
      frozen_event_id: "event-frozen",
      frozen_at: "2035-01-08T00:00:30Z",
    });
    vi.mocked(getClearingEntries).mockResolvedValue([]);
    vi.mocked(getClearingPositions).mockResolvedValue([]);
    vi.mocked(getClearingApprovals).mockResolvedValue([]);
    vi.mocked(getClearingDisputes).mockResolvedValue([]);
    vi.mocked(approveClearingPreview).mockResolvedValue({
      event_id: "event-approved",
      object_id: cycle.id,
      replayed: false,
    });
  });

  it("shows hashes and lets an independent controller approve the exact preview", async () => {
    const user = userEvent.setup();
    renderView();

    expect(await screen.findByRole("heading", { name: "Локальный клиринг" })).toBeInTheDocument();
    expect((await screen.findAllByText("WEEK-2035-01")).length).toBeGreaterThan(0);
    expect(screen.getAllByTitle(hashB).length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: "Подтвердить расчет" }));

    await waitFor(() => expect(approveClearingPreview).toHaveBeenCalledWith(cycle));
  });
});
