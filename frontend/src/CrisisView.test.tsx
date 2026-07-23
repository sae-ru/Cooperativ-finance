import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Principal } from "./api/admin";
import {
  activateCrisisMandate, approveRationingRule, approveReserveTarget, cancelRationingPlan,
  closeCrisisMandate, confirmRationingPlan, getCrisisControllerWorkspace, getCrisisMandates,
  getCrisisOperatorWorkspace, getCrisisPaperForms, getCrisisReports, getCrisisReviews,
  getRationingAllocations, getRationingPlans, getRationingRules, getReserveSnapshots,
  getReserveTargets, issueCrisisPaperForm, issueRation, previewRationingPlan,
  proposeCrisisMandate, proposeRationingRule, proposeReserveTarget, recordCrisisPaperForm,
  recordReserveSnapshot, reviewCrisisMandate, type CrisisMandate, type CrisisPaperForm,
  type RationingAllocation, type RationingPlan, type RationingRule, type ReserveTarget,
} from "./api/crisis";
import { getInventoryMembers, uploadEvidence } from "./api/inventory";
import CrisisView from "./CrisisView";

vi.mock("./api/inventory", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/inventory")>();
  return { ...actual, getInventoryMembers: vi.fn(), uploadEvidence: vi.fn() };
});

vi.mock("./api/crisis", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/crisis")>();
  return Object.fromEntries(Object.entries(actual).map(([key, value]) => [key, typeof value === "function" ? vi.fn() : value]));
});

const cooperativeId = "10000000-0000-4000-8000-000000000001";
const memberId = "10000000-0000-4000-8000-000000000002";
const target = {
  id: "target-1", cooperative_id: cooperativeId, resource_code: "CABBAGE", resource_name: "Запас капусты",
  unit_code: "KG", target_quantity: "100", critical_minimum: "20", warning_coverage_days: "10",
  critical_coverage_days: "3", max_snapshot_age_hours: 24, policy_version: 1,
  terms_hash: `sha256:${"a".repeat(64)}`, status: "ACTIVE", proposed_by_member_id: memberId,
  approved_by_member_id: memberId, created_at: "2035-01-01T00:00:00Z", approved_at: "2035-01-01T01:00:00Z", version: 2,
} as ReserveTarget;
const draftTarget = { ...target, id: "target-2", resource_code: "MILK", resource_name: "Запас молока", status: "DRAFT", approved_by_member_id: null, approved_at: null, version: 1 };
const mandate = {
  id: "mandate-1", cooperative_id: cooperativeId, mandate_code: "DEMO-CRISIS-001", crisis_type: "PAYMENT_FAILURE",
  scope_payload: {}, capabilities: ["ENABLE_RATIONING", "ENABLE_PAPER_FORMS"], rationale: "Учение",
  exit_criteria: "Сверка", safe_state: "Штатный режим", policy_version: 1,
  starts_at: "2035-01-01T00:00:00Z", review_at: "2035-01-01T06:00:00Z", expires_at: "2035-01-02T00:00:00Z",
  maximum_end_at: "2035-01-03T00:00:00Z", terms_hash: `sha256:${"b".repeat(64)}`, status: "CLOSED", effective_status: "CLOSED",
  proposed_by_member_id: memberId, activated_by_member_id: memberId, closed_by_member_id: memberId,
  created_at: "2035-01-01T00:00:00Z", activated_at: "2035-01-01T00:05:00Z", closed_at: "2035-01-01T12:00:00Z", version: 4,
} as CrisisMandate;
const activeMandate = {
  ...mandate, id: "mandate-active", mandate_code: "CRISIS-ACTIVE", status: "ACTIVE",
  effective_status: "ACTIVE", activated_by_member_id: memberId, closed_by_member_id: null,
  activated_at: "2035-01-01T00:05:00Z", closed_at: null, version: 2,
} as CrisisMandate;
const draftMandate = {
  ...activeMandate, id: "mandate-draft", mandate_code: "CRISIS-DRAFT", status: "DRAFT",
  effective_status: "DRAFT", activated_by_member_id: null, activated_at: null, version: 1,
} as CrisisMandate;
const activeRule = {
  id: "rule-active", mandate_id: activeMandate.id, target_id: target.id, policy_version: 1,
  formula: "EQUAL_PER_MEMBER", eligibility_policy: { active_membership: true }, protected_minimum: "2",
  maximum_per_member: "5", period_hours: 24, terms_hash: `sha256:${"e".repeat(64)}`,
  status: "ACTIVE", proposed_by_member_id: memberId, approved_by_member_id: memberId,
  created_at: "2035-01-01T00:00:00Z", approved_at: "2035-01-01T00:05:00Z", version: 2,
} as RationingRule;
const draftRule = {
  ...activeRule, id: "rule-draft", status: "DRAFT", approved_by_member_id: null,
  approved_at: null, version: 1,
} as RationingRule;
const previewPlan = {
  id: "plan-preview", rule_id: activeRule.id, snapshot_id: "snapshot-1", available_input: "50",
  eligible_count: 1, total_allocated: "5", input_hash: `sha256:${"f".repeat(64)}`,
  allocations_hash: `sha256:${"1".repeat(64)}`, status: "PREVIEWED",
  expires_at: "2035-01-01T12:00:00Z", proposed_by_member_id: memberId,
  confirmed_by_member_id: null, created_at: "2035-01-01T01:00:00Z", confirmed_at: null, version: 1,
} as RationingPlan;
const reservedAllocation = {
  id: "allocation-1", plan_id: previewPlan.id, member_id: memberId, weight: 1, quantity: "5",
  status: "RESERVED", created_at: "2035-01-01T01:00:00Z", issued_at: null,
} as RationingAllocation;
const issuedForm = {
  id: "form-1", cooperative_id: cooperativeId, mandate_id: activeMandate.id,
  serial_number: "PAPER-001", checksum: "A1B2C3D4", form_type: "INCIDENT",
  assigned_to_member_id: memberId, status: "ISSUED", issued_at: "2035-01-01T01:00:00Z",
  expires_at: "2035-01-01T12:00:00Z", payload_hash: null, issued_by_member_id: memberId,
  recorded_by_member_id: null, recorded_at: null,
} as CrisisPaperForm;

const principal: Principal = {
  user_id: "user-1", login: "crisis-controller", member_id: memberId, must_change_password: false,
  roles: [
    { assignment_id: "role-1", role: "CRISIS_OPERATOR", cooperative_id: cooperativeId },
    { assignment_id: "role-2", role: "CRISIS_CONTROLLER", cooperative_id: cooperativeId },
  ],
};

function renderView() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><CrisisView principal={principal} /></QueryClientProvider>);
}

describe("CrisisView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(uploadEvidence).mockResolvedValue("evidence-1");
    vi.mocked(getReserveTargets).mockResolvedValue([target]);
    vi.mocked(getReserveSnapshots).mockResolvedValue([{ id: "snapshot-1", target_id: target.id, physical_verified_quantity: "50", committed_quantity: "0", available_quantity: "50", consumption_rate_per_day: "10", coverage_days: "5", expiring_quantity: "0", quality_status: "ACCEPTED", confidence: "0.95", reserve_level: "WARNING", observed_at: "2035-01-01T00:00:00Z", snapshot_hash: `sha256:${"c".repeat(64)}`, recorded_by_member_id: memberId, created_at: "2035-01-01T00:00:00Z" }]);
    vi.mocked(getCrisisMandates).mockResolvedValue([mandate]);
    vi.mocked(getCrisisReviews).mockResolvedValue([{ id: "review-1", mandate_id: mandate.id, decision_round: 1, decision: "CLOSE", facts_payload: {}, rationale: "Учение завершено", previous_review_at: mandate.review_at, previous_expires_at: mandate.expires_at, new_review_at: null, new_expires_at: null, reviewer_member_id: memberId, created_at: mandate.closed_at! }]);
    vi.mocked(getRationingRules).mockResolvedValue([]);
    vi.mocked(getRationingPlans).mockResolvedValue([]);
    vi.mocked(getRationingAllocations).mockResolvedValue([]);
    vi.mocked(getCrisisPaperForms).mockResolvedValue([]);
    vi.mocked(getCrisisReports).mockResolvedValue([{ id: "report-1", mandate_id: mandate.id, report_payload: { mandate_code: mandate.mandate_code, rationing_rule_count: 1, rationing_plan_count: 1, ration_issuance_count: 1, paper_form_count: 1 }, report_hash: `sha256:${"d".repeat(64)}`, generated_at: mandate.closed_at! }]);
    vi.mocked(getInventoryMembers).mockResolvedValue([{ member_id: memberId, cooperative_id: cooperativeId, display_name: "Анна", member_number: "D-1" }]);
    vi.mocked(getCrisisOperatorWorkspace).mockResolvedValue({ active_targets: [target], active_mandates: [], active_rules: [], confirmed_plans: [], issued_forms: [] });
    vi.mocked(getCrisisControllerWorkspace).mockResolvedValue({ draft_targets: [draftTarget], draft_mandates: [], due_reviews: [], draft_rules: [], previewed_plans: [], issued_forms: [] });
    vi.mocked(approveReserveTarget).mockResolvedValue({ event_id: "event-1", object_id: draftTarget.id, replayed: false });
  });

  it("shows verified reserve coverage and immutable crisis report", async () => {
    const user = userEvent.setup(); renderView();
    expect(await screen.findByRole("heading", { name: "Резервы и кризис" })).toBeInTheDocument();
    expect(await screen.findByText("Запас капусты")).toBeInTheDocument();
    expect(screen.getByText("5 дн.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Отчёты" }));
    expect(await screen.findByText("DEMO-CRISIS-001")).toBeInTheDocument();
    expect(screen.getAllByText("1", { selector: "dd" })).toHaveLength(4);
  });

  it("lets an independent controller approve a reserve target", async () => {
    const user = userEvent.setup(); renderView();
    await user.click(await screen.findByRole("button", { name: "Утвердить" }));
    expect(approveReserveTarget).toHaveBeenCalledWith(draftTarget);
  });

  it("submits a reserve policy and evidence-backed physical snapshot", async () => {
    vi.mocked(proposeReserveTarget).mockResolvedValue({ event_id: "e1", object_id: "t1", replayed: false });
    vi.mocked(recordReserveSnapshot).mockResolvedValue({ event_id: "e2", object_id: "s1", replayed: false });
    const user = userEvent.setup(); renderView();
    fireEvent.submit((await screen.findByRole("button", { name: "Создать" })).closest("form")!);
    await waitFor(() => expect(proposeReserveTarget).toHaveBeenCalled());
    await user.upload(screen.getByLabelText("Акт проверки"), new File(["count"], "count.txt", { type: "text/plain" }));
    fireEvent.submit(screen.getByRole("button", { name: "Зафиксировать" }).closest("form")!);
    await waitFor(() => expect(recordReserveSnapshot).toHaveBeenCalled());
  });

  it("operates proposal, activation, review and close controls", async () => {
    vi.mocked(getCrisisMandates).mockResolvedValue([draftMandate, activeMandate]);
    vi.mocked(proposeCrisisMandate).mockResolvedValue({ event_id: "e1", object_id: "m1", replayed: false });
    vi.mocked(activateCrisisMandate).mockResolvedValue({ event_id: "e2", object_id: draftMandate.id, replayed: false });
    vi.mocked(reviewCrisisMandate).mockResolvedValue({ event_id: "e3", object_id: activeMandate.id, replayed: false });
    vi.mocked(closeCrisisMandate).mockResolvedValue({ event_id: "e4", object_id: activeMandate.id, replayed: false });
    const user = userEvent.setup(); renderView();
    await user.click(await screen.findByRole("button", { name: "Мандаты" }));
    await user.upload(screen.getByLabelText("Доказательство"), new File(["incident"], "incident.txt", { type: "text/plain" }));
    fireEvent.submit(screen.getByRole("button", { name: "Предложить" }).closest("form")!);
    await waitFor(() => expect(proposeCrisisMandate).toHaveBeenCalled());
    await user.click(screen.getByRole("button", { name: "Активировать" }));
    await waitFor(() => expect(activateCrisisMandate).toHaveBeenCalled());
    await user.click(screen.getByRole("button", { name: "Review" }));
    await waitFor(() => expect(reviewCrisisMandate).toHaveBeenCalled());
    await user.click(screen.getByRole("button", { name: "Закрыть" }));
    await waitFor(() => expect(closeCrisisMandate).toHaveBeenCalledWith(activeMandate, expect.any(String)));
    expect(activateCrisisMandate).toHaveBeenCalledWith(draftMandate);
    expect(reviewCrisisMandate).toHaveBeenCalled();
  });

  it("previews, approves, confirms, cancels and issues a ration", async () => {
    vi.mocked(getCrisisMandates).mockResolvedValue([activeMandate]);
    vi.mocked(getRationingRules).mockResolvedValue([activeRule]);
    vi.mocked(getRationingPlans).mockResolvedValue([previewPlan]);
    vi.mocked(getRationingAllocations).mockResolvedValue([reservedAllocation]);
    vi.mocked(getCrisisControllerWorkspace).mockResolvedValue({ draft_targets: [], draft_mandates: [], due_reviews: [], draft_rules: [draftRule], previewed_plans: [previewPlan], issued_forms: [] });
    vi.mocked(proposeRationingRule).mockResolvedValue({ event_id: "e1", object_id: "r1", replayed: false });
    vi.mocked(previewRationingPlan).mockResolvedValue({ event_id: "e2", object_id: "p1", replayed: false });
    vi.mocked(approveRationingRule).mockResolvedValue({ event_id: "e3", object_id: draftRule.id, replayed: false });
    vi.mocked(confirmRationingPlan).mockResolvedValue({ event_id: "e4", object_id: previewPlan.id, replayed: false });
    vi.mocked(cancelRationingPlan).mockResolvedValue({ event_id: "e5", object_id: previewPlan.id, replayed: false });
    vi.mocked(issueRation).mockResolvedValue({ event_id: "e6", object_id: reservedAllocation.id, replayed: false });
    const user = userEvent.setup(); renderView();
    await user.click(await screen.findByRole("button", { name: "Нормирование" }));
    fireEvent.submit(screen.getByRole("button", { name: "Создать" }).closest("form")!);
    await waitFor(() => expect(proposeRationingRule).toHaveBeenCalled());
    fireEvent.submit(screen.getByRole("button", { name: "Рассчитать" }).closest("form")!);
    await waitFor(() => expect(previewRationingPlan).toHaveBeenCalled());
    await user.click(screen.getByRole("button", { name: "Утвердить" }));
    await user.click(screen.getByTitle("Подтвердить"));
    await user.click(screen.getByTitle("Отменить"));
    await user.upload(screen.getByLabelText("Акт выдачи"), new File(["issue"], "issue.txt", { type: "text/plain" }));
    await user.click(screen.getByTitle("Подтвердить выдачу"));
    await waitFor(() => expect(issueRation).toHaveBeenCalled());
    expect(proposeRationingRule).toHaveBeenCalled();
    expect(previewRationingPlan).toHaveBeenCalled();
    expect(approveRationingRule).toHaveBeenCalledWith(draftRule);
    expect(confirmRationingPlan).toHaveBeenCalledWith(previewPlan);
    expect(cancelRationingPlan).toHaveBeenCalled();
  });

  it("issues and records a numbered paper form", async () => {
    vi.mocked(getCrisisMandates).mockResolvedValue([activeMandate]);
    vi.mocked(getCrisisPaperForms).mockResolvedValue([issuedForm]);
    vi.mocked(issueCrisisPaperForm).mockResolvedValue({ event_id: "e1", object_id: issuedForm.id, replayed: false });
    vi.mocked(recordCrisisPaperForm).mockResolvedValue({ event_id: "e2", object_id: issuedForm.id, replayed: false });
    const user = userEvent.setup(); renderView();
    await user.click(await screen.findByRole("button", { name: "Бумага" }));
    fireEvent.submit(screen.getByRole("button", { name: "Выдать" }).closest("form")!);
    await waitFor(() => expect(issueCrisisPaperForm).toHaveBeenCalled());
    const note = screen.getByDisplayValue("Бумажный оригинал сверен и сохранён.");
    await user.clear(note);
    await user.type(note, "Оригинал принят архивом.");
    await user.click(screen.getByTitle("Ввести форму"));
    await waitFor(() => expect(recordCrisisPaperForm).toHaveBeenCalledWith(issuedForm.id, issuedForm.checksum, expect.any(Object)));
    expect(issueCrisisPaperForm).toHaveBeenCalled();
  });});
