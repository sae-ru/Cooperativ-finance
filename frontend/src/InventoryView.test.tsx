import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import InventoryView from "./InventoryView";
import type { Principal } from "./api/admin";
import * as inventory from "./api/inventory";

vi.mock("./api/inventory", async () => {
  const actual = await vi.importActual<typeof import("./api/inventory")>("./api/inventory");
  return Object.fromEntries(
    Object.entries(actual).map(([key, value]) => [
      key,
      typeof value === "function" ? vi.fn() : value,
    ]),
  );
});

const cooperativeId = "30000000-0000-0000-0000-000000000001";
const userId = "10000000-0000-0000-0000-000000000001";
const product: inventory.Product = {
  id: "40000000-0000-0000-0000-000000000001",
  cooperative_id: cooperativeId,
  sku: "CABBAGE",
  name: "Капуста белокочанная",
  description: "Свежая капуста",
  default_unit_id: "50000000-0000-0000-0000-000000000001",
  quantity_tolerance: "0.100",
  requires_evidence: true,
  shelf_life_required: false,
  status: "ACTIVE",
  created_event_id: "60000000-0000-0000-0000-000000000001",
};
const unit: inventory.UnitOfMeasure = {
  id: product.default_unit_id,
  cooperative_id: cooperativeId,
  code: "KG",
  name: "Килограмм",
  symbol: "кг",
  dimension: "MASS",
  decimal_scale: 3,
  status: "ACTIVE",
  created_event_id: "60000000-0000-0000-0000-000000000002",
};
const warehouse: inventory.Warehouse = {
  id: "70000000-0000-0000-0000-000000000001",
  cooperative_id: cooperativeId,
  code: "WH-A",
  name: "Основной склад",
  address_text: "Площадка A",
  storage_conditions: "Сухое помещение",
  status: "ACTIVE",
  created_event_id: "60000000-0000-0000-0000-000000000003",
};
const custodian: inventory.InventoryCustodian = {
  assignment_id: "80000000-0000-0000-0000-000000000001",
  cooperative_id: cooperativeId,
  warehouse_id: warehouse.id,
  member_id: "90000000-0000-0000-0000-000000000001",
  user_id: userId,
  display_name: "Елена Соколова",
  role_code: "WAREHOUSE_CUSTODIAN",
};
const lot: inventory.InventoryLot = {
  id: "a0000000-0000-0000-0000-000000000001",
  cooperative_id: cooperativeId,
  lot_number: "CABBAGE-001",
  product_id: product.id,
  warehouse_id: warehouse.id,
  owner_member_id: "90000000-0000-0000-0000-000000000002",
  unit_id: unit.id,
  declared_quantity: "100.000",
  current_quantity: null,
  declared_quality: "Первый сорт",
  verified_quality: null,
  expires_at: null,
  storage_conditions: warehouse.storage_conditions,
  status: "PENDING_VERIFICATION",
  received_by_member_id: custodian.member_id,
  custodian_assignment_id: custodian.assignment_id,
  registered_event_id: "60000000-0000-0000-0000-000000000004",
  verified_event_id: null,
  created_at: "2026-07-20T10:00:00Z",
  updated_at: "2026-07-20T10:00:00Z",
  version: 1,
};

function principal(role: "WAREHOUSE_CUSTODIAN" | "INVENTORY_CONTROLLER"): Principal {
  return {
    user_id: role === "WAREHOUSE_CUSTODIAN" ? userId : "10000000-0000-0000-0000-000000000002",
    login: role === "WAREHOUSE_CUSTODIAN" ? "custodian" : "controller",
    member_id: role === "WAREHOUSE_CUSTODIAN" ? custodian.member_id : "90000000-0000-0000-0000-000000000003",
    must_change_password: false,
    roles: [{ assignment_id: "role-1", role, cooperative_id: cooperativeId }],
  };
}

function renderView(value: Principal) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <InventoryView principal={value} />
    </QueryClientProvider>,
  );
}

describe("InventoryView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(inventory.getUnits).mockResolvedValue([unit]);
    vi.mocked(inventory.getProducts).mockResolvedValue([product]);
    vi.mocked(inventory.getWarehouses).mockResolvedValue([warehouse]);
    vi.mocked(inventory.getInventoryMembers).mockResolvedValue([{
      member_id: lot.owner_member_id,
      cooperative_id: cooperativeId,
      display_name: "Анна Петрова",
      member_number: "D-001",
    }]);
    vi.mocked(inventory.getInventoryCustodians).mockResolvedValue([custodian]);
    vi.mocked(inventory.getLots).mockResolvedValue([lot]);
    vi.mocked(inventory.getDiscrepancies).mockResolvedValue([]);
    vi.mocked(inventory.getCustodyTransfers).mockResolvedValue([]);
    vi.mocked(inventory.uploadEvidence).mockResolvedValue("evidence-1");
    vi.mocked(inventory.registerLot).mockResolvedValue({ event_id: "event-1", object_id: "lot-2", replayed: false });
    vi.mocked(inventory.attestLot).mockResolvedValue({ event_id: "event-2", object_id: lot.id, replayed: false });
    vi.mocked(inventory.getReceiptAct).mockResolvedValue({
      lot,
      product,
      unit,
      warehouse,
      owner_name: "Анна Петрова",
      receiver_name: "Елена Соколова",
      attester_name: null,
      custodian_name: "Елена Соколова",
      attestation: null,
      evidence: [],
      signed_events: [{ event_id: lot.registered_event_id, event_type: "inventory.lot_registered", aggregate_version: 1, occurred_at: lot.created_at, event_hash: "a".repeat(64), payload: {} }],
      generated_at: "2026-07-20T11:00:00Z",
    });
  });

  it("lets the responsible custodian register a lot and open its receipt act", async () => {
    const user = userEvent.setup();
    renderView(principal("WAREHOUSE_CUSTODIAN"));

    expect(await screen.findByText("CABBAGE-001")).toBeInTheDocument();
    await user.click(screen.getByTitle("Открыть акт"));
    expect(await screen.findByRole("heading", { name: "Акт приемки партии" })).toBeInTheDocument();
    await user.click(screen.getByTitle("Закрыть"));
    await user.click(screen.getByRole("button", { name: "Приемка" }));
    await user.type(screen.getByLabelText("Номер партии"), "CABBAGE-002");
    await user.selectOptions(screen.getByLabelText("Склад"), warehouse.id);
    await user.selectOptions(screen.getByLabelText("Владелец"), lot.owner_member_id);
    await user.type(screen.getByLabelText("Количество"), "25.125");
    await user.upload(screen.getByLabelText("Доказательство"), new File(["act"], "act.txt", { type: "text/plain" }));
    const receiveButton = screen.getByRole("button", { name: "Принять" });
    const receiveForm = receiveButton.closest("form") as HTMLFormElement;
    expect(
      receiveForm.checkValidity(),
      Array.from(receiveForm.elements).filter((item) => !(item as HTMLInputElement).checkValidity()).map((item) => item.outerHTML).join("\n"),
    ).toBe(true);
    await user.click(receiveButton);

    await waitFor(() => expect(inventory.registerLot).toHaveBeenCalled());
    expect(vi.mocked(inventory.registerLot).mock.calls[0]?.[0]).toMatchObject({
      lot_number: "CABBAGE-002",
      declared_quantity: "25.125",
      custodian_assignment_id: custodian.assignment_id,
      evidence_ids: ["evidence-1"],
    });
    expect(screen.queryByRole("button", { name: "Контроль" })).not.toBeInTheDocument();
  });

  it("gives an independent controller the attestation work queue", async () => {
    const user = userEvent.setup();
    renderView(principal("INVENTORY_CONTROLLER"));

    await user.click(await screen.findByRole("button", { name: "Контроль" }));
    await user.selectOptions(screen.getByLabelText("Партия"), lot.id);
    await user.type(screen.getByLabelText("Температура"), "4 градуса");
    await user.upload(screen.getByLabelText("Доказательство"), new File(["measure"], "measure.txt", { type: "text/plain" }));
    const controlButton = screen.getByRole("button", { name: "Подтвердить" });
    const controlForm = controlButton.closest("form") as HTMLFormElement;
    expect(
      controlForm.checkValidity(),
      Array.from(controlForm.elements).filter((item) => !(item as HTMLInputElement).checkValidity()).map((item) => item.outerHTML).join("\n"),
    ).toBe(true);
    await user.click(controlButton);

    await waitFor(() => expect(inventory.attestLot).toHaveBeenCalled());
    expect(vi.mocked(inventory.attestLot).mock.calls[0]?.[0]).toEqual(lot);
    expect(vi.mocked(inventory.attestLot).mock.calls[0]?.[1]).toMatchObject({
      measured_quantity: "100.000",
      measurements: { temperature: "4 градуса", packaging: "Целая" },
      evidence_ids: ["evidence-1"],
    });
    expect(screen.queryByRole("button", { name: "Приемка" })).not.toBeInTheDocument();
  });

  it("formats indivisible inventory units without a fractional part", async () => {
    vi.mocked(inventory.getUnits).mockResolvedValue([{ ...unit, decimal_scale: 0 }]);
    vi.mocked(inventory.getLots).mockResolvedValue([{
      ...lot,
      declared_quantity: "100.000000000000",
      current_quantity: "99.000000000000",
    }]);

    renderView(principal("WAREHOUSE_CUSTODIAN"));

    expect(await screen.findByText("99")).toBeInTheDocument();
    expect(screen.getByText("заявлено 100")).toBeInTheDocument();
  });
});
