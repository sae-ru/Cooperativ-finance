import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
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
const unit: inventory.UnitOfMeasure = {
  id: "40000000-0000-0000-0000-000000000001",
  cooperative_id: cooperativeId,
  code: "KG",
  name: "Килограмм",
  symbol: "кг",
  dimension: "MASS",
  decimal_scale: 3,
  status: "ACTIVE",
  created_event_id: "event-unit",
};
const product: inventory.Product = {
  id: "50000000-0000-0000-0000-000000000001",
  cooperative_id: cooperativeId,
  sku: "MILK",
  name: "Молоко",
  description: "Молоко",
  default_unit_id: unit.id,
  quantity_tolerance: "0.050",
  requires_evidence: true,
  shelf_life_required: true,
  status: "ACTIVE",
  created_event_id: "event-product",
};
const warehouseA: inventory.Warehouse = {
  id: "60000000-0000-0000-0000-000000000001",
  cooperative_id: cooperativeId,
  code: "WH-A",
  name: "Склад A",
  address_text: "Площадка A",
  storage_conditions: "Холод",
  status: "ACTIVE",
  created_event_id: "event-warehouse-a",
};
const warehouseB: inventory.Warehouse = {
  ...warehouseA,
  id: "60000000-0000-0000-0000-000000000002",
  code: "WH-B",
  name: "Склад B",
  address_text: "Площадка B",
  created_event_id: "event-warehouse-b",
};
const custodianA: inventory.InventoryCustodian = {
  assignment_id: "70000000-0000-0000-0000-000000000001",
  cooperative_id: cooperativeId,
  warehouse_id: warehouseA.id,
  member_id: "80000000-0000-0000-0000-000000000001",
  user_id: userId,
  display_name: "Елена Соколова",
  role_code: "WAREHOUSE_CUSTODIAN",
};
const custodianB: inventory.InventoryCustodian = {
  ...custodianA,
  assignment_id: "70000000-0000-0000-0000-000000000002",
  warehouse_id: warehouseB.id,
  member_id: "80000000-0000-0000-0000-000000000002",
  user_id: "10000000-0000-0000-0000-000000000002",
  display_name: "Анна Петрова",
};
const lot: inventory.InventoryLot = {
  id: "90000000-0000-0000-0000-000000000001",
  cooperative_id: cooperativeId,
  lot_number: "MILK-001",
  product_id: product.id,
  warehouse_id: warehouseA.id,
  owner_member_id: custodianB.member_id,
  unit_id: unit.id,
  declared_quantity: "40.000",
  current_quantity: "40.000",
  declared_quality: "Пастеризованное",
  verified_quality: "Пастеризованное",
  expires_at: "2026-07-25T10:00:00Z",
  storage_conditions: "Холод",
  status: "VERIFIED",
  received_by_member_id: custodianA.member_id,
  custodian_assignment_id: custodianA.assignment_id,
  registered_event_id: "event-lot",
  verified_event_id: "event-verified",
  created_at: "2026-07-20T10:00:00Z",
  updated_at: "2026-07-20T10:10:00Z",
  version: 2,
};
const transfer: inventory.CustodyTransfer = {
  id: "a0000000-0000-0000-0000-000000000001",
  lot_id: lot.id,
  from_warehouse_id: warehouseB.id,
  to_warehouse_id: warehouseA.id,
  from_assignment_id: custodianB.assignment_id,
  to_assignment_id: custodianA.assignment_id,
  place: "Зона передачи",
  notes: "Опломбировано",
  status: "OFFERED",
  offered_by_user_id: custodianB.user_id,
  accepted_by_user_id: null,
  offered_event_id: "event-offer",
  accepted_event_id: null,
  offered_at: "2026-07-20T10:15:00Z",
  accepted_at: null,
};

function renderView(principal: Principal) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><InventoryView principal={principal} /></QueryClientProvider>);
}

function currentPrincipal(role: "WAREHOUSE_CUSTODIAN" | "DATA_STEWARD"): Principal {
  return {
    user_id: userId,
    login: role.toLowerCase(),
    member_id: custodianA.member_id,
    must_change_password: false,
    roles: [{ assignment_id: "role-1", role, cooperative_id: cooperativeId }],
  };
}

describe("InventoryView operations", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(inventory.getUnits).mockResolvedValue([unit]);
    vi.mocked(inventory.getProducts).mockResolvedValue([product]);
    vi.mocked(inventory.getWarehouses).mockResolvedValue([warehouseA, warehouseB]);
    vi.mocked(inventory.getInventoryMembers).mockResolvedValue([]);
    vi.mocked(inventory.getInventoryCustodians).mockResolvedValue([custodianA, custodianB]);
    vi.mocked(inventory.getLots).mockResolvedValue([lot]);
    vi.mocked(inventory.getDiscrepancies).mockResolvedValue([]);
    vi.mocked(inventory.getCustodyTransfers).mockResolvedValue([transfer]);
    vi.mocked(inventory.uploadEvidence).mockResolvedValue("evidence-1");
    for (const command of [inventory.offerCustody, inventory.acceptCustody, inventory.recordDiscrepancy, inventory.createUnit, inventory.createProduct, inventory.createWarehouse]) {
      vi.mocked(command).mockResolvedValue({ event_id: "event-command", object_id: "object-command", replayed: false });
    }
  });

  it("offers and accepts custody and records a physical discrepancy", async () => {
    const user = userEvent.setup();
    renderView(currentPrincipal("WAREHOUSE_CUSTODIAN"));
    await user.click(await screen.findByRole("button", { name: "Хранение" }));

    const lotSelectors = screen.getAllByLabelText("Партия");
    await user.selectOptions(lotSelectors[0]!, lot.id);
    await user.selectOptions(screen.getByLabelText("Новый хранитель"), custodianB.assignment_id);
    await user.upload(screen.getByLabelText("Фото или акт"), new File(["offer"], "offer.txt", { type: "text/plain" }));
    await user.click(screen.getByRole("button", { name: "Предложить" }));
    await waitFor(() => expect(inventory.offerCustody).toHaveBeenCalled());

    await user.selectOptions(screen.getByLabelText("Предложение"), transfer.id);
    await user.upload(screen.getByLabelText("Акт получения"), new File(["accept"], "accept.txt", { type: "text/plain" }));
    await user.click(screen.getByRole("button", { name: "Принять" }));
    await waitFor(() => expect(inventory.acceptCustody).toHaveBeenCalledWith(transfer, lot, ["evidence-1"]));

    await user.selectOptions(lotSelectors[1]!, lot.id);
    await user.clear(screen.getByLabelText("Фактически"));
    await user.type(screen.getByLabelText("Фактически"), "39.500");
    await user.upload(screen.getByLabelText("Доказательство"), new File(["count"], "count.txt", { type: "text/plain" }));
    await user.click(screen.getByRole("button", { name: "Зафиксировать" }));
    await waitFor(() => expect(inventory.recordDiscrepancy).toHaveBeenCalled());
    expect(vi.mocked(inventory.recordDiscrepancy).mock.calls[0]?.[1]).toMatchObject({
      actual_quantity: "39.500",
      evidence_ids: ["evidence-1"],
    });
  });

  it("maintains units, products, and warehouses as a data steward", async () => {
    const user = userEvent.setup();
    renderView(currentPrincipal("DATA_STEWARD"));
    await user.click(await screen.findByRole("button", { name: "Справочники" }));

    const unitSection = screen.getByRole("heading", { name: "Единица измерения" }).closest("section") as HTMLElement;
    await user.type(within(unitSection).getByLabelText("Код"), "L");
    await user.type(within(unitSection).getByLabelText("Наименование"), "Литр");
    await user.type(within(unitSection).getByLabelText("Обозначение"), "л");
    await user.click(within(unitSection).getByRole("button", { name: "Создать" }));
    await waitFor(() => expect(inventory.createUnit).toHaveBeenCalled());

    const productSection = screen.getByRole("heading", { name: "Товар" }).closest("section") as HTMLElement;
    await user.type(within(productSection).getByLabelText("SKU"), "NAILS");
    await user.type(within(productSection).getByLabelText("Наименование"), "Гвозди");
    await user.selectOptions(within(productSection).getByLabelText("Единица"), unit.id);
    await user.clear(within(productSection).getByLabelText("Допуск"));
    await user.type(within(productSection).getByLabelText("Допуск"), "0.010");
    await user.click(within(productSection).getByRole("button", { name: "Создать" }));
    await waitFor(() => expect(inventory.createProduct).toHaveBeenCalled());

    const warehouseSection = screen.getByRole("heading", { name: "Склад" }).closest("section") as HTMLElement;
    await user.type(within(warehouseSection).getByLabelText("Код"), "WH-C");
    await user.type(within(warehouseSection).getByLabelText("Наименование"), "Склад C");
    await user.type(within(warehouseSection).getByLabelText("Адрес"), "Площадка C");
    await user.type(within(warehouseSection).getByLabelText("Условия хранения"), "Сухое помещение");
    await user.click(within(warehouseSection).getByRole("button", { name: "Создать" }));
    await waitFor(() => expect(inventory.createWarehouse).toHaveBeenCalled());
  });
});
