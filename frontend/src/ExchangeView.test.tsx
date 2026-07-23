import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ExchangeView from "./ExchangeView";
import type { Principal, RoleCode } from "./api/admin";
import * as admin from "./api/admin";
import * as exchange from "./api/exchange";
import * as inventory from "./api/inventory";

vi.mock("./api/admin", async () => {
  const actual = await vi.importActual<typeof import("./api/admin")>("./api/admin");
  return Object.fromEntries(
    Object.entries(actual).map(([key, value]) => [
      key,
      typeof value === "function" && key !== "AdminApiError" ? vi.fn() : value,
    ]),
  );
});
vi.mock("./api/exchange", async () => {
  const actual = await vi.importActual<typeof import("./api/exchange")>("./api/exchange");
  return Object.fromEntries(
    Object.entries(actual).map(([key, value]) => [
      key,
      typeof value === "function" ? vi.fn() : value,
    ]),
  );
});
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
const ownerId = "90000000-0000-0000-0000-000000000001";
const recipientId = "90000000-0000-0000-0000-000000000002";
const adminId = "90000000-0000-0000-0000-000000000003";
const unitId = "50000000-0000-0000-0000-000000000001";

const deal: exchange.Deal = {
  id: "40000000-0000-0000-0000-000000000001",
  cooperative_id: cooperativeId,
  title: "Поставка капусты",
  status: "PROPOSED",
  terms_version: 1,
  terms_hash: `sha256:${"a".repeat(64)}`,
  proposed_by_member_id: adminId,
  proposed_event_id: "60000000-0000-0000-0000-000000000001",
  confirmed_event_id: null,
  confirmed_at: null,
  created_at: "2026-07-20T10:00:00Z",
  updated_at: "2026-07-20T10:00:00Z",
  version: 1,
};
const obligation: exchange.Obligation = {
  id: "70000000-0000-0000-0000-000000000001",
  deal_id: deal.id,
  cooperative_id: cooperativeId,
  sequence_no: 1,
  terms_version: 1,
  debtor_member_id: ownerId,
  creditor_member_id: recipientId,
  subject_type: "PRODUCT",
  subject_id: null,
  description: "Капуста первого сорта",
  quality_criteria: "Свежая, без повреждений",
  fulfillment_place: "Основной склад",
  due_at: "2035-07-21T10:00:00Z",
  unit_id: unitId,
  quantity_total: "100.000",
  quantity_submitted: "0.000",
  quantity_fulfilled: "25.000",
  quantity_cleared: "0.000",
  partial_allowed: true,
  evidence_required: true,
  confirmation_method: "Акт приёмки",
  substitute_policy: "По согласию сторон",
  valuation_source: "Без денежной оценки",
  liquidity_class: "UNASSESSED",
  clearing_allowed: false,

  status: "DISPUTED",
  created_event_id: "60000000-0000-0000-0000-000000000002",
  last_event_id: "60000000-0000-0000-0000-000000000003",
  created_at: "2026-07-20T10:00:00Z",
  updated_at: "2026-07-20T12:00:00Z",
  version: 3,
};
const dispute: exchange.Dispute = {
  id: "80000000-0000-0000-0000-000000000001",
  obligation_id: obligation.id,
  fulfillment_id: null,
  reason_code: "QUALITY_OR_QUANTITY",
  statement: "Нужен совместный осмотр",
  status: "OPEN",
  previous_obligation_status: "PARTIALLY_FULFILLED",
  previous_fulfillment_status: null,
  opened_by_member_id: ownerId,
  event_id: "60000000-0000-0000-0000-000000000004",
  resolution_action: null,
  resolution_notes: null,
  resolved_by_member_id: null,
  resolution_event_id: null,
  created_at: "2026-07-20T12:00:00Z",
  resolved_at: null,
  version: 1,
};

const fulfillment: exchange.Fulfillment = {
  id: "81000000-0000-0000-0000-000000000001",
  obligation_id: obligation.id,
  logistics_order_id: null,
  quantity: "10.000",
  accepted_quantity: "0.000",
  quality_claim: "Первый сорт",
  location_text: "Основной склад",
  performed_at: "2026-07-20T12:30:00Z",
  status: "SUBMITTED",
  performed_by_member_id: ownerId,
  submitted_event_id: "60000000-0000-0000-0000-000000000006",
  accepted_event_id: null,
  created_at: "2026-07-20T12:30:00Z",
  updated_at: "2026-07-20T12:30:00Z",
  version: 1,
};
const logisticsOrder: exchange.LogisticsOrder = {
  id: "82000000-0000-0000-0000-000000000001",
  obligation_id: obligation.id,
  cooperative_id: cooperativeId,
  carrier_member_id: adminId,
  quantity: "10.000",
  unit_id: unitId,
  origin_text: "Поле",
  destination_text: "Основной склад",
  pickup_due_at: "2035-07-20T10:00:00Z",
  delivery_due_at: "2035-07-20T12:00:00Z",
  status: "OFFERED",
  carrier_user_id: null,
  offered_event_id: "60000000-0000-0000-0000-000000000007",
  accepted_event_id: null,
  pickup_event_id: null,
  delivered_event_id: null,
  accepted_at: null,
  picked_up_at: null,
  delivered_at: null,
  created_at: "2026-07-20T09:00:00Z",
  updated_at: "2026-07-20T09:00:00Z",
  version: 1,
};
const commandResult = {
  event_id: "60000000-0000-0000-0000-000000000099",
  object_id: obligation.id,
  replayed: false,
};
function principal(role: RoleCode, memberId: string): Principal {
  return {
    user_id: "10000000-0000-0000-0000-000000000001",
    login: role.toLowerCase(),
    member_id: memberId,
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
      <ExchangeView principal={value} />
    </QueryClientProvider>,
  );
}

describe("ExchangeView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(admin.getCooperatives).mockResolvedValue([{
      id: cooperativeId,
      code: "DEMO",
      name: "Демо-кооператив",
      status: "ACTIVE",
      created_at: "2026-07-20T09:00:00Z",
      version: 1,
    }]);
    vi.mocked(inventory.getInventoryMembers).mockResolvedValue([
      {
        member_id: ownerId,
        cooperative_id: cooperativeId,
        display_name: "Анна Петрова",
        member_number: "D-001",
      },
      {
        member_id: recipientId,
        cooperative_id: cooperativeId,
        display_name: "Елена Соколова",
        member_number: "D-002",
      },
      {
        member_id: adminId,
        cooperative_id: cooperativeId,
        display_name: "Иван Орлов",
        member_number: "D-003",
      },
    ]);
    vi.mocked(inventory.getUnits).mockResolvedValue([{
      id: unitId,
      cooperative_id: cooperativeId,
      code: "KG",
      name: "Килограмм",
      symbol: "кг",
      dimension: "MASS",
      decimal_scale: 3,
      status: "ACTIVE",
      created_event_id: "60000000-0000-0000-0000-000000000005",
    }]);
    vi.mocked(inventory.uploadEvidence).mockResolvedValue("evidence-1");
    vi.mocked(exchange.getDeals).mockResolvedValue([deal]);
    vi.mocked(exchange.getDeal).mockResolvedValue({
      deal,
      terms: {},
      parties: [
        {
          id: "party-1",
          deal_id: deal.id,
          terms_version: 1,
          terms_hash: deal.terms_hash,
          member_id: ownerId,
          created_event_id: deal.proposed_event_id,
          created_at: deal.created_at,
        },
        {
          id: "party-2",
          deal_id: deal.id,
          terms_version: 1,
          terms_hash: deal.terms_hash,
          member_id: recipientId,
          created_event_id: deal.proposed_event_id,
          created_at: deal.created_at,
        },
      ],
      confirmations: [],
      obligations: [],
    });
    vi.mocked(exchange.getObligations).mockResolvedValue([obligation]);
    vi.mocked(exchange.getLogisticsOrders).mockResolvedValue([]);
    vi.mocked(exchange.getDisputes).mockResolvedValue([dispute]);
    vi.mocked(exchange.getFulfillments).mockResolvedValue([]);
    vi.mocked(exchange.confirmDeal).mockResolvedValue({
      event_id: "event-confirm",
      object_id: deal.id,
      replayed: false,
    });
    vi.mocked(exchange.resolveDispute).mockResolvedValue({
      event_id: "event-resolution",
      object_id: dispute.id,
      replayed: false,
    });
    vi.mocked(exchange.proposeDeal).mockResolvedValue(commandResult);
    vi.mocked(exchange.reviseDeal).mockResolvedValue(commandResult);
    vi.mocked(exchange.submitFulfillment).mockResolvedValue(commandResult);
    vi.mocked(exchange.acceptFulfillment).mockResolvedValue(commandResult);
    vi.mocked(exchange.createLogisticsOrder).mockResolvedValue(commandResult);
    vi.mocked(exchange.transitionLogisticsOrder).mockResolvedValue(commandResult);
    vi.mocked(exchange.openDispute).mockResolvedValue(commandResult);
    vi.mocked(exchange.markOverdue).mockResolvedValue(commandResult);
  });

  it("lets an exact deal party inspect terms, confirm, and see aligned obligation columns", async () => {
    const user = userEvent.setup();
    renderView(principal("DATA_STEWARD", ownerId));

    expect(await screen.findByText(deal.title)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Открыть" }));
    await user.click(await screen.findByRole("button", { name: "Подтвердить" }));
    await waitFor(() => expect(exchange.confirmDeal).toHaveBeenCalled());
    expect(vi.mocked(exchange.confirmDeal).mock.calls[0]?.[0]).toEqual(deal);

    await user.click(screen.getByRole("button", { name: "Исполнение" }));
    expect(await screen.findByRole("columnheader", { name: "Должник" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Получатель" })).toBeInTheDocument();
    expect(screen.getByText("Анна Петрова")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Новая сделка" })).not.toBeInTheDocument();
  });

  it("gives an independent scoped administrator a documented resolution action", async () => {
    const user = userEvent.setup();
    renderView(principal("COOPERATIVE_ADMIN", adminId));

    await user.click(await screen.findByRole("button", { name: "Споры" }));
    expect(screen.getByRole("heading", { name: "Решение по спору" })).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Спор"), dispute.id);
    await user.type(screen.getByLabelText("Мотивировка"), "Совместный осмотр завершён");
    await user.upload(
      screen.getByLabelText("Документ решения"),
      new File(["decision"], "decision.txt", { type: "text/plain" }),
    );
    const resolveButton = screen.getByRole("button", { name: "Зафиксировать решение" });
    const resolveForm = resolveButton.closest("form") as HTMLFormElement;
    const invalidControls = Array.from(resolveForm.elements)
      .filter((element) => element instanceof HTMLInputElement || element instanceof HTMLSelectElement)
      .filter((element) => !element.checkValidity())
      .map((element) => ({
        label: element.getAttribute("aria-label"),
        type: element.getAttribute("type"),
        value: (element as HTMLInputElement | HTMLSelectElement).value,
      }));
    expect(invalidControls).toEqual([]);
    await user.click(resolveButton);

    await waitFor(() => expect(inventory.uploadEvidence).toHaveBeenCalled());
    await waitFor(() => expect(exchange.resolveDispute).toHaveBeenCalledWith(dispute, {
      resolution_action: "CONTINUE_PERFORMANCE",
      resolution_notes: "Совместный осмотр завершён",
      evidence_ids: ["evidence-1"],
    }));
    expect(inventory.uploadEvidence).toHaveBeenCalledWith(
      cooperativeId,
      expect.any(File),
      "DISPUTE_RESOLUTION",
    );
  });

  it("creates a versioned deal from the complete administrator editor", async () => {
    const user = userEvent.setup();
    renderView(principal("COOPERATIVE_ADMIN", adminId));

    await user.click(await screen.findByRole("button", { name: "Новая сделка" }));
    await user.type(screen.getByLabelText("Название сделки"), "Поставка овощей");
    await user.selectOptions(screen.getByLabelText("Должник"), ownerId);
    await user.selectOptions(screen.getByLabelText("Получатель"), recipientId);
    await user.selectOptions(screen.getByLabelText("Единица"), unitId);
    await user.type(screen.getByLabelText("Количество"), "50.000");
    await user.type(screen.getByLabelText("Предмет"), "Пятьдесят килограммов овощей");
    await user.type(screen.getByLabelText("Критерии качества"), "Свежие, без повреждений");
    await user.type(screen.getByLabelText("Место исполнения"), "Основной склад");
    await user.click(screen.getByRole("button", { name: "Предложить" }));

    await waitFor(() => expect(exchange.proposeDeal).toHaveBeenCalled());
    expect(vi.mocked(exchange.proposeDeal).mock.calls[0]?.[0]).toMatchObject({
      cooperative_id: cooperativeId,
      title: "Поставка овощей",
      obligations: [
        expect.objectContaining({
          debtor_member_id: ownerId,
          creditor_member_id: recipientId,
          unit_id: unitId,
          quantity: "50.000",
        }),
      ],
    });
  });

  it("submits fulfillment as the debtor and independently accepts it as the creditor", async () => {
    const activeObligation = { ...obligation, status: "PARTIALLY_FULFILLED" };
    vi.mocked(exchange.getObligations).mockResolvedValue([activeObligation]);
    const user = userEvent.setup();
    const debtorView = renderView(principal("DATA_STEWARD", ownerId));

    await user.click(await screen.findByRole("button", { name: "Исполнение" }));
    await user.type(screen.getByLabelText("Количество"), "10.000");
    await user.type(screen.getByLabelText("Качество"), "Первый сорт");
    await user.type(screen.getByLabelText("Место"), "Основной склад");
    await user.upload(
      screen.getByLabelText("Акт исполнения"),
      new File(["act"], "fulfillment.txt", { type: "text/plain" }),
    );
    fireEvent.submit(
      screen.getByRole("button", { name: "Предъявить" }).closest("form") as HTMLFormElement,
    );

    await waitFor(() => expect(exchange.submitFulfillment).toHaveBeenCalled());
    expect(vi.mocked(exchange.submitFulfillment).mock.calls[0]?.[0]).toEqual(activeObligation);
    expect(vi.mocked(exchange.submitFulfillment).mock.calls[0]?.[1]).toMatchObject({
      quantity: "10.000",
      quality_claim: "Первый сорт",
      evidence_ids: ["evidence-1"],
    });
    debtorView.unmount();

    vi.mocked(exchange.getFulfillments).mockResolvedValue([fulfillment]);
    renderView(principal("DATA_STEWARD", recipientId));
    await user.click(await screen.findByRole("button", { name: "Исполнение" }));
    await user.selectOptions(screen.getByLabelText("Предъявление"), fulfillment.id);
    await user.type(screen.getByLabelText("Принято"), "8.000");
    await user.type(screen.getByLabelText("Оценка качества"), "Два килограмма повреждены");
    await user.type(screen.getByLabelText("Примечание"), "Остаток возвращён");
    await user.upload(
      screen.getByLabelText("Акт приёмки"),
      new File(["acceptance"], "acceptance.txt", { type: "text/plain" }),
    );
    fireEvent.submit(
      screen.getByRole("button", { name: "Зафиксировать" }).closest("form") as HTMLFormElement,
    );

    await waitFor(() => expect(exchange.acceptFulfillment).toHaveBeenCalled());
    expect(vi.mocked(exchange.acceptFulfillment).mock.calls[0]?.[0]).toEqual(activeObligation);
    expect(vi.mocked(exchange.acceptFulfillment).mock.calls[0]?.[1]).toEqual(fulfillment);
    expect(vi.mocked(exchange.acceptFulfillment).mock.calls[0]?.[2]).toMatchObject({
      accepted_quantity: "8.000",
      evidence_ids: ["evidence-1"],
    });
  });

  it("offers logistics and lets only the named carrier accept the order", async () => {
    const activeObligation = { ...obligation, status: "ACTIVE" };
    vi.mocked(exchange.getObligations).mockResolvedValue([activeObligation]);
    vi.mocked(exchange.getDisputes).mockResolvedValue([]);
    const user = userEvent.setup();
    const adminView = renderView(principal("COOPERATIVE_ADMIN", adminId));

    await user.click(await screen.findByRole("button", { name: "Логистика" }));
    await user.selectOptions(screen.getByLabelText("Обязательство"), obligation.id);
    await user.selectOptions(screen.getByLabelText("Перевозчик"), adminId);
    await user.type(screen.getByLabelText("Количество"), "10.000");
    await user.type(screen.getByLabelText("Откуда"), "Поле");
    await user.type(screen.getByLabelText("Куда"), "Основной склад");
    fireEvent.change(screen.getByLabelText("Забрать до"), {
      target: { value: "2035-07-20T10:00" },
    });
    fireEvent.change(screen.getByLabelText("Доставить до"), {
      target: { value: "2035-07-20T12:00" },
    });
    await user.click(screen.getByRole("button", { name: "Предложить" }));

    await waitFor(() => expect(exchange.createLogisticsOrder).toHaveBeenCalled());
    expect(vi.mocked(exchange.createLogisticsOrder).mock.calls[0]?.[0]).toEqual(activeObligation);
    adminView.unmount();

    vi.mocked(exchange.getLogisticsOrders).mockResolvedValue([logisticsOrder]);
    renderView(principal("LOGISTICS_OPERATOR", adminId));
    await user.click(await screen.findByRole("button", { name: "Логистика" }));
    await user.click(screen.getByRole("button", { name: "Принять" }));

    await waitFor(() => expect(exchange.transitionLogisticsOrder).toHaveBeenCalled());
    expect(vi.mocked(exchange.transitionLogisticsOrder).mock.calls[0]?.slice(0, 3)).toEqual([
      logisticsOrder,
      "accept",
      [],
    ]);
  });

  it("opens a documented party dispute and runs an explicit overdue scan", async () => {
    const activeObligation = {
      ...obligation,
      status: "ACTIVE",
      due_at: "2025-01-01T00:00:00Z",
    };
    vi.mocked(exchange.getObligations).mockResolvedValue([activeObligation]);
    vi.mocked(exchange.getDisputes).mockResolvedValue([]);
    const user = userEvent.setup();
    const partyView = renderView(principal("DATA_STEWARD", ownerId));

    await user.click(await screen.findByRole("button", { name: "Споры" }));
    await user.selectOptions(screen.getByLabelText("Обязательство"), obligation.id);
    await user.type(screen.getByLabelText("Заявление"), "Количество требует сверки");
    await user.upload(
      screen.getByLabelText("Доказательство"),
      new File(["statement"], "statement.txt", { type: "text/plain" }),
    );
    fireEvent.submit(
      screen.getByRole("button", { name: "Открыть спор" }).closest("form") as HTMLFormElement,
    );

    await waitFor(() => expect(exchange.openDispute).toHaveBeenCalled());
    expect(vi.mocked(exchange.openDispute).mock.calls[0]?.[0]).toEqual(activeObligation);
    partyView.unmount();

    renderView(principal("RISK_ADMIN", adminId));
    await user.click(await screen.findByRole("button", { name: "Споры" }));
    expect(screen.getByText("1 кандидатов")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Зафиксировать просрочку" }));
    await waitFor(() => expect(exchange.markOverdue).toHaveBeenCalled());
    expect(vi.mocked(exchange.markOverdue).mock.calls[0]?.[0]).toBe(cooperativeId);
  });

  it("does not expose execution commands while an obligation is disputed", async () => {
    const user = userEvent.setup();
    renderView(principal("DATA_STEWARD", ownerId));

    await user.click(await screen.findByRole("button", { name: "Исполнение" }));
    expect(screen.queryByRole("heading", { name: "Предъявить исполнение" }))
      .not.toBeInTheDocument();
  });

  it("loads the current terms into a new immutable version", async () => {
    const user = userEvent.setup();
    vi.mocked(exchange.getDeal).mockResolvedValue({
      deal,
      terms: {
        obligations: [{
          debtor_member_id: ownerId,
          creditor_member_id: recipientId,
          subject_type: "PRODUCT",
          subject_id: null,
          description: "Капуста первого сорта",
          quality_criteria: "Свежая, без повреждений",
          fulfillment_place: "Основной склад",
          due_at: "2035-07-21T10:00:00Z",
          unit_id: unitId,
          quantity: "100.000",
          partial_allowed: true,
          evidence_required: true,
          confirmation_method: "Акт приёмки",
          substitute_policy: "По согласию сторон",
          valuation_source: "Без денежной оценки",
          liquidity_class: "UNASSESSED",
          clearing_allowed: false,
        }],
      },
      parties: [],
      confirmations: [],
      obligations: [],
    });
    renderView(principal("COOPERATIVE_ADMIN", adminId));

    await user.click(await screen.findByRole("button", { name: "Открыть" }));
    await user.click(await screen.findByRole("button", { name: "Новая версия" }));
    expect(screen.getByDisplayValue("Капуста первого сорта")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Создать версию" }));

    await waitFor(() => expect(exchange.reviseDeal).toHaveBeenCalled());
    expect(vi.mocked(exchange.reviseDeal).mock.calls[0]?.[0]).toEqual(deal);
    expect(vi.mocked(exchange.reviseDeal).mock.calls[0]?.[1]).toMatchObject({
      title: deal.title,
      obligations: [
        expect.objectContaining({
          debtor_member_id: ownerId,
          creditor_member_id: recipientId,
          quantity: "100.000",
        }),
      ],
    });
  });

  it("records pickup and delivery evidence for the same named logistics user", async () => {
    const activeObligation = { ...obligation, status: "ACTIVE" };
    vi.mocked(exchange.getObligations).mockResolvedValue([activeObligation]);
    const user = userEvent.setup();

    vi.mocked(exchange.getLogisticsOrders).mockResolvedValue([{
      ...logisticsOrder,
      status: "ACCEPTED",
      carrier_user_id: "10000000-0000-0000-0000-000000000001",
      accepted_event_id: "event-accepted",
      accepted_at: "2026-07-20T09:30:00Z",
      version: 2,
    }]);
    const pickupView = renderView(principal("LOGISTICS_OPERATOR", adminId));
    await user.click(await screen.findByRole("button", { name: "Логистика" }));
    await user.upload(
      screen.getByLabelText(`Акт ${logisticsOrder.id.slice(0, 8)}`),
      new File(["pickup"], "pickup.txt", { type: "text/plain" }),
    );
    await user.click(screen.getByRole("button", { name: "Забрать" }));
    await waitFor(() => expect(exchange.transitionLogisticsOrder).toHaveBeenCalled());
    expect(vi.mocked(exchange.transitionLogisticsOrder).mock.calls[0]?.[1]).toBe("pickup");
    expect(vi.mocked(exchange.transitionLogisticsOrder).mock.calls[0]?.[2]).toEqual([
      "evidence-1",
    ]);
    pickupView.unmount();

    vi.mocked(exchange.transitionLogisticsOrder).mockClear();
    vi.mocked(exchange.getLogisticsOrders).mockResolvedValue([{
      ...logisticsOrder,
      status: "IN_TRANSIT",
      carrier_user_id: "10000000-0000-0000-0000-000000000001",
      accepted_event_id: "event-accepted",
      pickup_event_id: "event-pickup",
      accepted_at: "2026-07-20T09:30:00Z",
      picked_up_at: "2026-07-20T10:00:00Z",
      version: 3,
    }]);
    renderView(principal("LOGISTICS_OPERATOR", adminId));
    await user.click(await screen.findByRole("button", { name: "Логистика" }));
    await user.upload(
      screen.getByLabelText(`Акт ${logisticsOrder.id.slice(0, 8)}`),
      new File(["delivery"], "delivery.txt", { type: "text/plain" }),
    );
    await user.click(screen.getByRole("button", { name: "Доставить" }));
    await waitFor(() => expect(exchange.transitionLogisticsOrder).toHaveBeenCalled());
    expect(vi.mocked(exchange.transitionLogisticsOrder).mock.calls[0]?.[1]).toBe("deliver");
  });

  it("renders request-aware and generic registry failures", async () => {
    vi.mocked(exchange.getDeals).mockRejectedValue(
      new admin.AdminApiError("EXCHANGE_DOWN", "request-1", 503),
    );
    const failedView = renderView(principal("DATA_STEWARD", ownerId));
    expect(await screen.findByText("EXCHANGE_DOWN · request-1")).toBeInTheDocument();
    failedView.unmount();

    vi.mocked(exchange.getDeals).mockRejectedValue(new Error("network"));
    renderView(principal("DATA_STEWARD", ownerId));
    expect(await screen.findByText("Операция не выполнена")).toBeInTheDocument();
  });
});
