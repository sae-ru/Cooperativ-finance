import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import RightsView from "./RightsView";
import type { Principal, RoleCode } from "./api/admin";
import * as inventory from "./api/inventory";
import * as rights from "./api/rights";

vi.mock("./api/inventory", async () => {
  const actual = await vi.importActual<typeof import("./api/inventory")>("./api/inventory");
  return Object.fromEntries(Object.entries(actual).map(([key, value]) => [
    key,
    typeof value === "function" ? vi.fn() : value,
  ]));
});

vi.mock("./api/rights", async () => {
  const actual = await vi.importActual<typeof import("./api/rights")>("./api/rights");
  return Object.fromEntries(Object.entries(actual).map(([key, value]) => [
    key,
    typeof value === "function" ? vi.fn() : value,
  ]));
});

const cooperativeId = "30000000-0000-0000-0000-000000000001";
const unit: inventory.UnitOfMeasure = {
  id: "50000000-0000-0000-0000-000000000001",
  cooperative_id: cooperativeId,
  code: "KG",
  name: "Килограмм",
  symbol: "кг",
  dimension: "MASS",
  decimal_scale: 3,
  status: "ACTIVE",
  created_event_id: "60000000-0000-0000-0000-000000000001",
};
const product: inventory.Product = {
  id: "40000000-0000-0000-0000-000000000001",
  cooperative_id: cooperativeId,
  sku: "CABBAGE",
  name: "Капуста белокочанная",
  description: "Свежая капуста",
  default_unit_id: unit.id,
  quantity_tolerance: "0.100",
  requires_evidence: true,
  shelf_life_required: false,
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
const owner = {
  member_id: "90000000-0000-0000-0000-000000000001",
  cooperative_id: cooperativeId,
  display_name: "Анна Петрова",
  member_number: "D-001",
};
const recipient = {
  member_id: "90000000-0000-0000-0000-000000000002",
  cooperative_id: cooperativeId,
  display_name: "Елена Соколова",
  member_number: "D-002",
};
const lot: inventory.InventoryLot = {
  id: "a0000000-0000-0000-0000-000000000001",
  cooperative_id: cooperativeId,
  lot_number: "CABBAGE-001",
  product_id: product.id,
  warehouse_id: warehouse.id,
  owner_member_id: owner.member_id,
  unit_id: unit.id,
  declared_quantity: "120.000",
  current_quantity: "120.000",
  declared_quality: "Первый сорт",
  verified_quality: "Первый сорт",
  expires_at: null,
  storage_conditions: warehouse.storage_conditions,
  status: "VERIFIED",
  received_by_member_id: recipient.member_id,
  custodian_assignment_id: "80000000-0000-0000-0000-000000000001",
  registered_event_id: "60000000-0000-0000-0000-000000000004",
  verified_event_id: "60000000-0000-0000-0000-000000000005",
  created_at: "2026-07-20T10:00:00Z",
  updated_at: "2026-07-20T10:30:00Z",
  version: 2,
};
const balance: rights.LotBalance = {
  lot_id: lot.id,
  verified_quantity: "120.000",
  available_quantity: "107.500",
  reserved_quantity: "0.000",
  rights_issued_quantity: "12.500",
  redeemed_quantity: "0.000",
  quarantined_quantity: "0.000",
  backing_shortfall_quantity: "0.000",
  version: 4,
  updated_at: "2026-07-20T11:00:00Z",
};
const commodityRight: rights.CommodityRight = {
  id: "b0000000-0000-0000-0000-000000000001",
  cooperative_id: cooperativeId,
  lot_id: lot.id,
  owner_member_id: owner.member_id,
  original_owner_member_id: owner.member_id,
  quantity: "12.500",
  unit_id: unit.id,
  status: "ISSUED",
  redeem_warehouse_id: warehouse.id,
  valid_until: null,
  reservation_id: "c0000000-0000-0000-0000-000000000001",
  issued_by_member_id: recipient.member_id,
  issued_role_assignment_id: "d0000000-0000-0000-0000-000000000001",
  issued_event_id: "e0000000-0000-0000-0000-000000000001",
  frozen_previous_status: null,
  freeze_reason: null,
  frozen_event_id: null,
  redeemed_event_id: null,
  created_at: "2026-07-20T11:00:00Z",
  updated_at: "2026-07-20T11:00:00Z",
  version: 1,
};
const redemption: rights.RightRedemption = {
  id: "f0000000-0000-0000-0000-000000000001",
  right_id: commodityRight.id,
  lot_id: lot.id,
  owner_member_id: owner.member_id,
  warehouse_id: warehouse.id,
  custodian_assignment_id: lot.custodian_assignment_id,
  quantity: commodityRight.quantity,
  status: "REQUESTED",
  requested_by_user_id: "10000000-0000-0000-0000-000000000001",
  fulfilled_by_user_id: null,
  requested_event_id: "e0000000-0000-0000-0000-000000000002",
  completed_event_id: null,
  requested_at: "2026-07-20T11:30:00Z",
  completed_at: null,
};

function principal(role: RoleCode): Principal {
  return {
    user_id: "10000000-0000-0000-0000-000000000001",
    login: role.toLowerCase(),
    member_id: recipient.member_id,
    must_change_password: false,
    roles: [{ assignment_id: "role-1", role, cooperative_id: cooperativeId }],
  };
}

function renderView(role: RoleCode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <RightsView principal={principal(role)} />
    </QueryClientProvider>,
  );
}

describe("RightsView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(inventory.getUnits).mockResolvedValue([unit]);
    vi.mocked(inventory.getProducts).mockResolvedValue([product]);
    vi.mocked(inventory.getWarehouses).mockResolvedValue([warehouse]);
    vi.mocked(inventory.getInventoryMembers).mockResolvedValue([owner, recipient]);
    vi.mocked(inventory.getLots).mockResolvedValue([lot]);
    vi.mocked(inventory.uploadEvidence).mockResolvedValue("evidence-1");
    vi.mocked(rights.getLotBalances).mockResolvedValue([balance]);
    vi.mocked(rights.getCommodityRights).mockResolvedValue([commodityRight]);
    vi.mocked(rights.getRightRedemptions).mockResolvedValue([redemption]);
    vi.mocked(rights.issueCommodityRight).mockResolvedValue({ event_id: "event-3", object_id: "right-2", replayed: false });
    vi.mocked(rights.completeRightRedemption).mockResolvedValue({ event_id: "event-4", object_id: redemption.id, replayed: false });
    vi.mocked(rights.freezeCommodityRight).mockResolvedValue({ event_id: "event-5", object_id: commodityRight.id, replayed: false });
    vi.mocked(rights.transferCommodityRight).mockResolvedValue({ event_id: "event-6", object_id: commodityRight.id, replayed: false });
    vi.mocked(rights.requestRightRedemption).mockResolvedValue({ event_id: "event-7", object_id: redemption.id, replayed: false });
    vi.mocked(rights.unfreezeCommodityRight).mockResolvedValue({ event_id: "event-8", object_id: commodityRight.id, replayed: false });
    vi.mocked(rights.getRightProof).mockResolvedValue({
      proof_hash: "a".repeat(64),
      right: commodityRight,
      balance,
      lot_number: lot.lot_number,
      lot_status: lot.status,
      current_quantity: lot.current_quantity,
      original_owner_name: owner.display_name,
      current_owner_name: owner.display_name,
      reservation: {
        id: commodityRight.reservation_id,
        lot_id: lot.id,
        purpose_type: "COMMODITY_RIGHT",
        purpose_id: commodityRight.id,
        quantity: commodityRight.quantity,
        status: "CONVERTED",
        expires_at: null,
        created_event_id: "event-reservation",
        completed_event_id: commodityRight.issued_event_id,
        created_at: commodityRight.created_at,
      },
      transfers: [],
      redemption,
      signed_events: [{
        event_id: commodityRight.issued_event_id,
        event_type: "rights.commodity_right_issued",
        aggregate_type: "commodity_right",
        aggregate_id: commodityRight.id,
        aggregate_version: 1,
        local_sequence: 7,
        occurred_at: commodityRight.created_at,
        event_hash: "b".repeat(64),
        payload: {},
      }],
      generated_at: "2026-07-20T12:00:00Z",
    });
  });

  it("lets a rights operator inspect proof and issue against an exact balance version", async () => {
    const user = userEvent.setup();
    renderView("RIGHTS_OPERATOR");

    expect(await screen.findByText("CABBAGE-001")).toBeInTheDocument();
    await user.click(screen.getByTitle("Открыть proof"));
    expect(await screen.findByRole("heading", { name: "Товарное право" })).toBeInTheDocument();
    expect(screen.getByText("a".repeat(64))).toBeInTheDocument();
    await user.click(screen.getByTitle("Закрыть"));

    await user.click(screen.getByRole("button", { name: "Выпуск" }));
    await user.selectOptions(screen.getByLabelText("Партия"), lot.id);
    await user.selectOptions(screen.getByLabelText("Получатель"), recipient.member_id);
    await user.type(screen.getByLabelText("Количество"), "5.250");
    await user.click(screen.getByRole("button", { name: "Выпустить" }));

    await waitFor(() => expect(rights.issueCommodityRight).toHaveBeenCalled());
    expect(vi.mocked(rights.issueCommodityRight).mock.calls[0]?.[0]).toMatchObject({
      lot_id: lot.id,
      owner_member_id: recipient.member_id,
      quantity: "5.250",
      expected_balance_version: balance.version,
    });
    expect(screen.queryByRole("button", { name: "Контроль" })).not.toBeInTheDocument();
  });

  it("gives the warehouse custodian only the two-phase physical fulfillment action", async () => {
    const user = userEvent.setup();
    renderView("WAREHOUSE_CUSTODIAN");

    await user.click(await screen.findByRole("button", { name: "Выдача" }));
    await user.selectOptions(screen.getByLabelText("Запрос"), redemption.id);
    await user.upload(screen.getByLabelText("Акт выдачи"), new File(["act"], "act.txt", { type: "text/plain" }));
    const fulfillButton = screen.getByRole("button", { name: "Подтвердить выдачу" });
    const fulfillForm = fulfillButton.closest("form") as HTMLFormElement;
    expect(
      fulfillForm.checkValidity(),
      Array.from(fulfillForm.elements)
        .filter((item) => !(item as HTMLInputElement).checkValidity())
        .map((item) => item.outerHTML)
        .join("\n"),
    ).toBe(true);
    await user.click(fulfillButton);

    await waitFor(() => expect(rights.completeRightRedemption).toHaveBeenCalled());
    expect(inventory.uploadEvidence).toHaveBeenCalledWith(cooperativeId, expect.any(File), "RIGHT_REDEMPTION_ACT");
    expect(vi.mocked(rights.completeRightRedemption).mock.calls[0]?.[2]).toEqual(["evidence-1"]);
    expect(screen.queryByRole("button", { name: "Выпуск" })).not.toBeInTheDocument();
  });

  it("transfers a right with evidence and lets the operator request redemption", async () => {
    const user = userEvent.setup();
    renderView("RIGHTS_OPERATOR");

    await user.click(await screen.findByRole("button", { name: "Оборот" }));
    await user.selectOptions(screen.getByLabelText("Товарное право"), commodityRight.id);
    await user.selectOptions(screen.getByLabelText("Новый владелец"), recipient.member_id);
    await user.upload(
      screen.getByLabelText("Согласие владельца"),
      new File(["consent"], "consent.txt", { type: "text/plain" }),
    );
    await user.click(screen.getByRole("button", { name: "Передать" }));

    await waitFor(() => expect(rights.transferCommodityRight).toHaveBeenCalledWith(
      commodityRight,
      recipient.member_id,
      ["evidence-1"],
    ));
    expect(inventory.uploadEvidence).toHaveBeenCalledWith(
      cooperativeId,
      expect.any(File),
      "RIGHT_TRANSFER_AUTHORIZATION",
    );

    await user.selectOptions(screen.getByLabelText("Товарное право"), commodityRight.id);
    await user.click(screen.getByRole("button", { name: "Запросить выдачу" }));
    await waitFor(() => expect(rights.requestRightRedemption).toHaveBeenCalledWith(commodityRight));
  });

  it("shows an empty custodian queue without enabling physical fulfillment", async () => {
    const user = userEvent.setup();
    vi.mocked(rights.getRightRedemptions).mockResolvedValue([]);
    renderView("WAREHOUSE_CUSTODIAN");

    await user.click(await screen.findByRole("button", { name: "Выдача" }));
    expect(screen.getByText("Нет запросов на выдачу")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Подтвердить выдачу" })).toBeDisabled();
  });

  it("restores the previous state of a frozen right", async () => {
    const user = userEvent.setup();
    const frozenRight: rights.CommodityRight = {
      ...commodityRight,
      status: "FROZEN",
      frozen_previous_status: "ISSUED",
      freeze_reason: "PROTECTIVE_REVIEW",
      frozen_event_id: "event-freeze",
      version: 2,
    };
    vi.mocked(rights.getCommodityRights).mockResolvedValue([frozenRight]);
    renderView("AUDITOR");

    await user.click(await screen.findByRole("button", { name: "Контроль" }));
    await user.selectOptions(screen.getByLabelText("Товарное право"), frozenRight.id);
    await user.type(screen.getByLabelText("Решение / дело"), "AUDIT-2026-18");
    await user.click(screen.getByRole("button", { name: "Разморозить" }));

    await waitFor(() => expect(rights.unfreezeCommodityRight).toHaveBeenCalledWith(
      frozenRight,
      "AUDIT-2026-18",
    ));
  });

  it("lets an auditor freeze a right without granting circulation commands", async () => {
    const user = userEvent.setup();
    renderView("AUDITOR");

    await user.click(await screen.findByRole("button", { name: "Контроль" }));
    await user.selectOptions(screen.getByLabelText("Товарное право"), commodityRight.id);
    await user.type(screen.getByLabelText("Решение / дело"), "AUDIT-2026-17");
    await user.click(screen.getByRole("button", { name: "Заморозить" }));

    await waitFor(() => expect(rights.freezeCommodityRight).toHaveBeenCalledWith(
      commodityRight,
      "PROTECTIVE_REVIEW",
      "AUDIT-2026-17",
    ));
    expect(screen.queryByRole("button", { name: "Оборот" })).not.toBeInTheDocument();
  });
});
