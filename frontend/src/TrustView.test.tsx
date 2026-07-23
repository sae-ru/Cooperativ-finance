import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Principal } from "./api/admin";
import { getInventoryMembers } from "./api/inventory";
import {
  getArbitratorWorkspace,
  getAuditorWorkspace,
  getProtectiveMeasures,
  getRehabilitationPlans,
  getReliabilityProfile,
  getReputationEvents,
  getTrustAppeals,
  getTrustCases,
  getTrustConflicts,
  getTrustDecisions,
  getTrustPolicies,
  getTrustSanctions,
  type TrustCase,
} from "./api/trust";
import TrustView from "./TrustView";

vi.mock("./api/inventory", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/inventory")>();
  return { ...actual, getInventoryMembers: vi.fn(), uploadEvidence: vi.fn(), uploadEvidenceProof: vi.fn() };
});

vi.mock("./api/trust", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/trust")>();
  return Object.fromEntries(Object.entries(actual).map(([key, value]) => [
    key,
    typeof value === "function" ? vi.fn() : value,
  ]));
});

const cooperativeId = "10000000-0000-4000-8000-000000000001";
const annaId = "10000000-0000-4000-8000-000000000002";
const pavelId = "10000000-0000-4000-8000-000000000003";
const caseItem = {
  id: "20000000-0000-4000-8000-000000000001",
  cooperative_id: cooperativeId,
  case_reference: "DEMO-TRUST-APPEAL-001",
  subject_member_id: annaId,
  claimant_member_id: annaId,
  source_type: "OTHER",
  source_reference: "TIMESTAMP-001",
  summary: "Спорная отметка времени",
  facts: "В исходном решении часовой пояс определен неверно.",
  requested_outcome: "Исправить решение и сохранить историю.",
  response_text: "Обязательство исполнено вовремя.",
  opened_at: "2035-01-01T00:00:00Z",
  status: "CLOSED",
  version: 7,
} as TrustCase;

const principal: Principal = {
  user_id: "30000000-0000-4000-8000-000000000001",
  login: "auditor-arbitrator",
  member_id: pavelId,
  must_change_password: false,
  roles: [
    { assignment_id: "role-audit", role: "AUDITOR", cooperative_id: null },
    { assignment_id: "role-arbitrator", role: "ARBITRATOR", cooperative_id: null },
  ],
};

function renderView() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <TrustView principal={principal} />
    </QueryClientProvider>,
  );
}

describe("TrustView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getInventoryMembers).mockResolvedValue([
      { member_id: annaId, cooperative_id: cooperativeId, display_name: "Анна Петрова", member_number: "D-1" },
    ]);
    vi.mocked(getTrustPolicies).mockResolvedValue([]);
    vi.mocked(getTrustCases).mockResolvedValue([caseItem]);
    vi.mocked(getTrustAppeals).mockResolvedValue([]);
    vi.mocked(getTrustSanctions).mockResolvedValue([]);
    vi.mocked(getReputationEvents).mockResolvedValue([{
      id: "event-correction",
      cooperative_id: cooperativeId,
      case_id: caseItem.id,
      decision_id: "decision-2",
      subject_member_id: annaId,
      context: "OBLIGATION",
      classification: "CORRECTION",
      severity: 0,
      confidence: "1.0000",
      observation_start: "2035-01-02T00:00:00Z",
      observation_end: "2035-01-02T00:00:00Z",
      appeal_state: "OVERTURNED",
      status: "ACTIVE",
      visibility: "COOPERATIVE",
      corrects_event_id: "event-breach",
      created_at: "2035-01-02T00:00:00Z",
    }]);
    vi.mocked(getRehabilitationPlans).mockResolvedValue([]);
    vi.mocked(getTrustDecisions).mockResolvedValue([]);
    vi.mocked(getTrustConflicts).mockResolvedValue([]);
    vi.mocked(getProtectiveMeasures).mockResolvedValue([]);
    vi.mocked(getArbitratorWorkspace).mockResolvedValue({ ready_cases: [], submitted_appeals: [], active_measures: [] });
    vi.mocked(getAuditorWorkspace).mockResolvedValue({ cases_needing_review: [], active_measures: [], disputed_reputation_events: [], active_rehabilitation_plans: [] });
    vi.mocked(getReliabilityProfile).mockResolvedValue({
      subject_member_id: pavelId,
      active_measures: 0,
      active_sanctions: 0,
      rehabilitation_active: 0,
      generated_at: "2035-01-02T00:00:00Z",
      contexts: [{
        context: "OBLIGATION",
        confirmed_fulfillments: 0,
        confirmed_breaches: 0,
        self_reported_errors: 0,
        rehabilitation_events: 0,
        disputed_events: 1,
        voided_events: 0,
        corrections: 1,
        sample_count: 0,
        confidence_min: null,
        confidence_max: null,
        last_observation: "2035-01-02T00:00:00Z",
        source_event_ids: ["event-breach", "event-correction"],
      }],
    });
  });

  it("shows the case workspace and a contextual profile without a scalar score", async () => {
    const user = userEvent.setup();
    renderView();

    expect(await screen.findByRole("heading", { name: "Споры и доверие" })).toBeInTheDocument();
    expect(await screen.findByText("DEMO-TRUST-APPEAL-001")).toBeInTheDocument();
    await waitFor(() => expect(getReliabilityProfile).toHaveBeenCalledWith(annaId));
    await user.click(screen.getByRole("button", { name: "Репутация" }));

    expect(await screen.findByRole("heading", { name: "Контекстная матрица" })).toBeInTheDocument();
    expect(screen.queryByText("Поручительство", { exact: true })).not.toBeInTheDocument();
    expect(screen.queryByText(/общий балл/i)).not.toBeInTheDocument();
    expect(screen.getAllByText("1").length).toBeGreaterThan(0);
  });

  it("runs the auditor and arbitrator queues across consequences and rehabilitation", async () => {
    const trust = await import("./api/trust");
    const user = userEvent.setup();
    const activeCase = { ...caseItem, status: "READY_FOR_DECISION", version: 4 };
    const measure = {
      id: "measure-1", case_id: activeCase.id, subject_member_id: annaId,
      measure_type: "BLOCK_NEW_GUARANTEES", scope: { blocked_actions: ["GUARANTEE_CREATE"] },
      rationale: "Independent review is pending.", status: "ACTIVE",
      starts_at: "2035-01-01T00:00:00Z", expires_at: "2035-01-08T00:00:00Z",
      review_at: "2035-01-02T00:00:00Z", lift_reason: null, version: 1,
    } as Awaited<ReturnType<typeof getProtectiveMeasures>>[number];
    const appeal = {
      id: "appeal-1", case_id: activeCase.id, original_decision_id: "decision-1",
      sanction_id: "sanction-1", appellant_member_id: annaId,
      grounds: "The timestamp source was interpreted incorrectly.", status: "SUBMITTED",
      outcome: null, submitted_at: "2035-01-03T00:00:00Z", decided_at: null,
    } as Awaited<ReturnType<typeof getTrustAppeals>>[number];
    const sanction = {
      id: "sanction-1", case_id: activeCase.id, decision_id: "decision-1",
      subject_member_id: annaId, measure_type: "WARNING", severity: "LOW", scope: {},
      rationale: "Original decision consequence.", status: "PENDING_APPEAL",
      starts_at: "2035-01-03T00:00:00Z", expires_at: null, review_at: null,
      appeal_until: "2035-01-17T00:00:00Z", revocation_reason: null, version: 1,
    } as Awaited<ReturnType<typeof getTrustSanctions>>[number];
    const plan = {
      id: "plan-1", case_id: activeCase.id, decision_id: "decision-1",
      subject_member_id: annaId, title: "Проверка процедуры", completion_criteria: {},
      status: "ACTIVE", starts_at: "2035-01-03T00:00:00Z", due_at: "2035-02-03T00:00:00Z",
      closure_reason: null, created_at: "2035-01-03T00:00:00Z", closed_at: null, version: 1,
    } as Awaited<ReturnType<typeof getRehabilitationPlans>>[number];
    vi.mocked(getTrustCases).mockResolvedValue([activeCase]);
    vi.mocked(getTrustAppeals).mockResolvedValue([appeal]);
    vi.mocked(getTrustSanctions).mockResolvedValue([sanction]);
    vi.mocked(getProtectiveMeasures).mockResolvedValue([measure]);
    vi.mocked(getRehabilitationPlans).mockResolvedValue([plan]);
    vi.mocked(getTrustDecisions).mockResolvedValue([{
      id: "decision-1", case_id: activeCase.id, stage: "ORIGINAL",
      outcome: "SUBSTANTIATED", decision_round: 1,
    } as never]);
    vi.mocked(trust.getRehabilitationSteps).mockResolvedValue([{
      id: "step-1", plan_id: plan.id, sequence: 1,
      description: "Подтвердить корректирующее действие", completion_criterion: "Документ принят",
      status: "PENDING", evidence_refs: [], completed_at: null,
    }]);
    vi.mocked(trust.declareTrustConflict).mockResolvedValue({ event_id: "event-1", object_id: "conflict-1", replayed: false });
    vi.mocked(trust.liftProtectiveMeasure).mockResolvedValue({ event_id: "event-2", object_id: measure.id, replayed: false });
    vi.mocked(trust.finalizeTrustSanction).mockResolvedValue({ event_id: "event-3", object_id: sanction.id, replayed: false });
    vi.mocked(trust.closeRehabilitationPlan).mockResolvedValue({ event_id: "event-4", object_id: plan.id, replayed: false });

    renderView();
    expect(await screen.findByText("Готово к решению")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Заявить об отсутствии конфликта" }));
    await user.click(screen.getByRole("button", { name: "Апелляции" }));
    expect(await screen.findByText(appeal.grounds)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Подтвердить независимость" }));
    await user.click(screen.getByRole("button", { name: "Меры" }));
    await user.click(await screen.findByRole("button", { name: "Снять" }));
    await user.click(screen.getByRole("button", { name: "Финализировать" }));
    await user.click(screen.getByRole("button", { name: "Реабилитация" }));
    expect(await screen.findByText("Подтвердить корректирующее действие")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Закрыть план" }));

    expect(trust.declareTrustConflict).toHaveBeenCalledTimes(2);
    expect(trust.liftProtectiveMeasure).toHaveBeenCalledWith(measure, expect.any(String));
    expect(trust.finalizeTrustSanction).toHaveBeenCalledWith(sanction);
    expect(trust.closeRehabilitationPlan).toHaveBeenCalledWith(plan, "OBLIGATION", expect.any(String));
  });});
