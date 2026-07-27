import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AdminApiError, type Principal } from "./api/admin";
import {
  addShareContribution,
  assessLiabilityCase,
  decideRelatedLink,
  getExposureCommitments,
  getLiabilityCases,
  getRelatedLinks,
  getRiskPolicies,
  getShareAccounts,
  getShareContributions,
  openLiabilityCase,
  openShareAccount,
  proposeRelatedLink,
  proposeRiskPolicy,
  releaseExposure,
  type ExposureCommitment,
  type LiabilityCase,
  type RelatedLink,
  type RiskPolicy,
  type ShareAccount,
} from "./api/risk";
import RiskView from "./RiskView";

vi.mock("./api/admin", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/admin")>();
  return { ...actual, getCooperatives: vi.fn() };
});

vi.mock("./api/inventory", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/inventory")>();
  return {
    ...actual,
    getInventoryMembers: vi.fn(),
    uploadEvidence: vi.fn(),
  };
});

vi.mock("./api/risk", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/risk")>();
  return {
    ...actual,
    getRiskPolicies: vi.fn(),
    getShareAccounts: vi.fn(),
    getShareContributions: vi.fn(),
    getRelatedLinks: vi.fn(),
    getExposureCommitments: vi.fn(),
    getLiabilityCases: vi.fn(),
    previewExposure: vi.fn(),
    proposeRiskPolicy: vi.fn(),
    approveRiskPolicy: vi.fn(),
    openShareAccount: vi.fn(),
    addShareContribution: vi.fn(),
    proposeRelatedLink: vi.fn(),
    decideRelatedLink: vi.fn(),
    proposeExposure: vi.fn(),
    acceptExposure: vi.fn(),
    releaseExposure: vi.fn(),
    openLiabilityCase: vi.fn(),
    assessLiabilityCase: vi.fn(),
  };
});

const cooperativeId = "10000000-0000-4000-8000-000000000011";
const ownerId = "10000000-0000-4000-8000-000000000012";
const operatorId = "10000000-0000-4000-8000-000000000013";
const reviewerId = "10000000-0000-4000-8000-000000000014";
const evidenceId = "10000000-0000-4000-8000-000000000015";

const policy = {
  id: "20000000-0000-4000-8000-000000000011",
  cooperative_id: cooperativeId,
  policy_version: 2,
  denomination: "SHARE",
  max_member_exposure: "100.000000000000",
  max_related_exposure: "160.000000000000",
  max_guarantee_chain_depth: 3,
  terms_hash: `sha256:${"c".repeat(64)}`,
  terms_payload: {},
  status: "ACTIVE",
  proposed_by_member_id: operatorId,
  proposed_event_id: "20000000-0000-4000-8000-000000000012",
  approved_by_member_id: reviewerId,
  approved_event_id: "20000000-0000-4000-8000-000000000013",
  created_at: "2026-07-20T10:00:00Z",
  approved_at: "2026-07-20T10:10:00Z",
  version: 2,
} as RiskPolicy;

const account = {
  id: "30000000-0000-4000-8000-000000000011",
  cooperative_id: cooperativeId,
  member_id: ownerId,
  opening_policy_id: policy.id,
  contour: "GUARANTEE",
  denomination: "SHARE",
  balance: "100.000000000000",
  protected_amount: "20.000000000000",
  executed_not_settled: "0.000000000000",
  status: "ACTIVE",
  created_event_id: "30000000-0000-4000-8000-000000000012",
  last_event_id: "30000000-0000-4000-8000-000000000012",
  created_at: "2026-07-20T10:00:00Z",
  updated_at: "2026-07-20T10:00:00Z",
  version: 2,
} as ShareAccount;

const activeCommitment = {
  id: "40000000-0000-4000-8000-000000000011",
  cooperative_id: cooperativeId,
  policy_id: policy.id,
  account_id: account.id,
  owner_member_id: ownerId,
  commitment_type: "DIRECT_OBLIGATION",
  risk_type: "DELIVERY",
  risk_id: "40000000-0000-4000-8000-000000000012",
  debtor_member_id: ownerId,
  beneficiary_member_id: null,
  role_assignment_id: null,
  amount_reserved: "30.000000000000",
  max_loss: "25.000000000000",
  coverage_ratio: "0.833333",
  starts_at: "2026-07-20T10:00:00Z",
  expires_at: "2027-07-20T10:00:00Z",
  release_condition: "Verified completion.",
  trigger_conditions: "Documented default.",
  exclusions: "Protected shares.",
  terms_hash: `sha256:${"d".repeat(64)}`,
  terms_payload: {},
  status: "ACTIVE",
  proposed_by_member_id: operatorId,
  proposed_event_id: "40000000-0000-4000-8000-000000000013",
  accepted_by_user_id: "40000000-0000-4000-8000-000000000014",
  accepted_event_id: "40000000-0000-4000-8000-000000000015",
  released_event_id: null,
  release_reason: null,
  created_at: "2026-07-20T10:00:00Z",
  accepted_at: "2026-07-20T10:20:00Z",
  released_at: null,
  version: 2,
} as ExposureCommitment;

const relatedLink = {
  id: "50000000-0000-4000-8000-000000000011",
  cooperative_id: cooperativeId,
  member_a_id: operatorId,
  member_b_id: reviewerId,
  relation_type: "CONTROL",
  source_statement: "Documented common control.",
  status: "PROPOSED",
  proposed_by_member_id: operatorId,
  proposed_event_id: "50000000-0000-4000-8000-000000000012",
  decided_by_member_id: null,
  decision_event_id: null,
  created_at: "2026-07-20T10:00:00Z",
  decided_at: null,
  version: 1,
} as RelatedLink;

const liabilityCase = {
  id: "60000000-0000-4000-8000-000000000011",
  cooperative_id: cooperativeId,
  commitment_id: activeCommitment.id,
  incident_reference: "INCIDENT-OLD",
  responsible_member_id: ownerId,
  affected_amount: "12.000000000000",
  facts: "Delivery was not completed.",
  causal_graph: { cause: "default", effect: "loss" },
  status: "OPEN",
  opened_by_member_id: operatorId,
  opened_event_id: "60000000-0000-4000-8000-000000000012",
  fault_class: null,
  assessed_loss: null,
  coverage_summary: null,
  assessment_rationale: null,
  assessed_by_member_id: null,
  assessed_event_id: null,
  appeal_until: null,
  created_at: "2026-07-20T10:00:00Z",
  assessed_at: null,
  version: 1,
} as LiabilityCase;

const commandResult = { event_id: "event-result", object_id: "object-result", replayed: false };

function scopedPrincipal(role: "COOPERATIVE_ADMIN" | "RISK_ADMIN", memberId = operatorId): Principal {
  return {
    user_id: "70000000-0000-4000-8000-000000000011",
    login: "operator",
    member_id: memberId,
    must_change_password: false,
    roles: [{
      assignment_id: "70000000-0000-4000-8000-000000000012",
      role,
      cooperative_id: cooperativeId,
    }],
  };
}

function renderView(principal: Principal) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <RiskView principal={principal} />
    </QueryClientProvider>,
  );
}

describe("RiskView operational commands", () => {
  beforeEach(async () => {
    const admin = await import("./api/admin");
    const inventory = await import("./api/inventory");
    vi.mocked(admin.getCooperatives).mockResolvedValue([
      { id: cooperativeId, code: "DEMO", name: "Demo", status: "ACTIVE", created_at: "2026-07-20T00:00:00Z", updated_at: "2026-07-20T00:00:00Z", version: 1 },
    ]);
    vi.mocked(inventory.getInventoryMembers).mockResolvedValue([
      { member_id: ownerId, cooperative_id: cooperativeId, display_name: "Владелец", member_number: "M-1" },
      { member_id: operatorId, cooperative_id: cooperativeId, display_name: "Оператор", member_number: "M-2" },
      { member_id: reviewerId, cooperative_id: cooperativeId, display_name: "Проверяющий", member_number: "M-3" },
    ]);
    vi.mocked(inventory.uploadEvidence).mockResolvedValue(evidenceId);
    vi.mocked(getRiskPolicies).mockResolvedValue([policy]);
    vi.mocked(getShareAccounts).mockResolvedValue([account]);
    vi.mocked(getShareContributions).mockResolvedValue([{
      id: "contribution-1",
      account_id: account.id,
      amount: "100.000000000000",
      entry_type: "OPENING",
      source_reference: "REGISTER-1",
      recorded_by_user_id: "user-1",
      event_id: "event-opening",
      created_at: "2026-07-20T10:00:00Z",
    }]);
    vi.mocked(getExposureCommitments).mockResolvedValue([activeCommitment]);
    vi.mocked(getRelatedLinks).mockResolvedValue([]);
    vi.mocked(getLiabilityCases).mockResolvedValue([]);
    vi.mocked(proposeRiskPolicy).mockResolvedValue(commandResult);
    vi.mocked(openShareAccount).mockResolvedValue(commandResult);
    vi.mocked(addShareContribution).mockResolvedValue(commandResult);
    vi.mocked(proposeRelatedLink).mockResolvedValue(commandResult);
    vi.mocked(decideRelatedLink).mockResolvedValue(commandResult);
    vi.mocked(releaseExposure).mockResolvedValue(commandResult);
    vi.mocked(openLiabilityCase).mockResolvedValue(commandResult);
    vi.mocked(assessLiabilityCase).mockResolvedValue(commandResult);
  });

  it("proposes a policy with complete limits and evidence", async () => {
    const user = userEvent.setup();
    const inventory = await import("./api/inventory");
    renderView(scopedPrincipal("COOPERATIVE_ADMIN"));
    await screen.findByRole("heading", { name: "Риск и паи" });
    await user.click(screen.getByRole("button", { name: "Политики" }));
    const section = screen.getByRole("heading", { name: "Новая политика лимитов" }).closest("section");
    expect(section).not.toBeNull();
    const form = within(section!);
    await user.clear(form.getByLabelText("Лимит участника"));
    await user.type(form.getByLabelText("Лимит участника"), "500");
    await user.clear(form.getByLabelText("Лимит группы"));
    await user.type(form.getByLabelText("Лимит группы"), "900");
    await user.clear(form.getByLabelText("Глубина поручительств"));
    await user.type(form.getByLabelText("Глубина поручительств"), "4");
    await user.type(form.getByLabelText("Решение органа"), "BOARD-22");
    const file = new File(["policy"], "policy.txt", { type: "text/plain" });
    await user.upload(form.getByLabelText("Основание"), file);
    fireEvent.submit(section!.querySelector("form")!);

    await waitFor(() => expect(proposeRiskPolicy).toHaveBeenCalledWith(expect.objectContaining({
      cooperative_id: cooperativeId,
      max_member_exposure: "500",
      max_related_exposure: "900",
      max_guarantee_chain_depth: 4,
      approval_reference: "BOARD-22",
      evidence_ids: [evidenceId],
    })));
    expect(inventory.uploadEvidence).toHaveBeenCalledWith(
      cooperativeId,
      file,
      "RISK_POLICY_PROPOSAL",
    );
  });

  it("opens an account and records an append-only contribution", async () => {
    const user = userEvent.setup();
    renderView(scopedPrincipal("COOPERATIVE_ADMIN"));
    await screen.findByRole("heading", { name: "Риск и паи" });
    await user.click(screen.getByRole("button", { name: "Паи" }));
    const openSection = screen.getByRole("heading", { name: "Открыть счёт пая" }).closest("section");
    const openForm = within(openSection!);
    await user.selectOptions(openForm.getByLabelText("Участник"), ownerId);
    await user.selectOptions(openForm.getByLabelText("Контур"), "GUARANTEE");
    await user.type(openForm.getByLabelText("Начальный баланс"), "80");
    await user.clear(openForm.getByLabelText("Защищено"));
    await user.type(openForm.getByLabelText("Защищено"), "15");
    await user.type(openForm.getByLabelText("Источник"), "REGISTER-22");
    await user.upload(openForm.getByLabelText("Основание"), new File(["account"], "account.txt", { type: "text/plain" }));
    fireEvent.submit(openSection!.querySelector("form")!);
    await waitFor(() => expect(openShareAccount).toHaveBeenCalledWith(expect.objectContaining({
      policy_id: policy.id,
      member_id: ownerId,
      contour: "GUARANTEE",
      opening_balance: "80",
      protected_amount: "15",
    })));

    const contributionSection = screen.getByRole("heading", { name: /Взносы/ }).closest("section");
    const contributionForm = within(contributionSection!);
    await user.type(contributionForm.getByLabelText("Сумма"), "7");
    await user.type(contributionForm.getByLabelText("Источник"), "REGISTER-23");
    await user.upload(contributionForm.getByLabelText("Основание"), new File(["deposit"], "deposit.txt", { type: "text/plain" }));
    fireEvent.submit(contributionSection!.querySelector("form")!);
    await waitFor(() => expect(addShareContribution).toHaveBeenCalledWith(
      account,
      expect.objectContaining({ amount: "7", source_reference: "REGISTER-23" }),
    ));
  });

  it("proposes related parties and independently decides the queued link", async () => {
    const user = userEvent.setup();
    vi.mocked(getRelatedLinks).mockResolvedValue([relatedLink]);
    renderView(scopedPrincipal("RISK_ADMIN", ownerId));
    await screen.findByRole("heading", { name: "Риск и паи" });
    await user.click(screen.getByRole("button", { name: "Связи" }));
    const commandSection = screen.getByRole("heading", { name: "Связанные участники" }).closest("section");
    const command = within(commandSection!);
    await user.selectOptions(command.getByLabelText("Участник A"), ownerId);
    await user.selectOptions(command.getByLabelText("Участник B"), operatorId);
    await user.selectOptions(command.getByLabelText("Связь"), "HOUSEHOLD");
    await user.type(command.getByLabelText("Основание", { selector: 'input:not([type=file])' }), "Совместное домохозяйство");
    await user.upload(command.getByLabelText("Основание", { selector: "input[type=file]" }), new File(["link"], "link.txt", { type: "text/plain" }));
    fireEvent.submit(commandSection!.querySelector("form")!);
    await waitFor(() => expect(proposeRelatedLink).toHaveBeenCalledWith(expect.objectContaining({
      member_a_id: ownerId,
      member_b_id: operatorId,
      relation_type: "HOUSEHOLD",
    })));

    await user.type(screen.getByLabelText("Мотивировка решения"), "Связь подтверждена");
    await user.upload(screen.getByLabelText("Основание решения"), new File(["decision"], "decision.txt", { type: "text/plain" }));
    await user.click(screen.getByRole("button", { name: "Утвердить связь" }));
    await waitFor(() => expect(decideRelatedLink).toHaveBeenCalledWith(
      relatedLink,
      true,
      "Связь подтверждена",
      [evidenceId],
    ));
  });

  it("releases an active reserve with evidence and switches role contours safely", async () => {
    const user = userEvent.setup();
    const roleAccount = { ...account, id: "30000000-0000-4000-8000-000000000099", contour: "ROLE", member_id: operatorId } as ShareAccount;
    vi.mocked(getShareAccounts).mockResolvedValue([account, roleAccount]);
    renderView(scopedPrincipal("RISK_ADMIN"));
    await screen.findByRole("heading", { name: "Риск и паи" });
    await user.click(screen.getByRole("button", { name: "Риски" }));
    await user.selectOptions(screen.getByLabelText("Счёт"), roleAccount.id);
    expect(screen.getByLabelText("ID назначения роли")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Счёт"), account.id);

    await user.type(screen.getByLabelText("Основание освобождения"), "Обязательство исполнено");
    const file = new File(["release"], "release.txt", { type: "text/plain" });
    await user.upload(screen.getByLabelText("Файл освобождения"), file);
    await user.click(screen.getByRole("button", { name: "Освободить" }));
    await waitFor(() => expect(releaseExposure).toHaveBeenCalledWith(
      activeCommitment,
      "Обязательство исполнено",
      [evidenceId],
    ));
  });

  it("opens and independently assesses liability without executing shares", async () => {
    const user = userEvent.setup();
    vi.mocked(getLiabilityCases).mockResolvedValue([liabilityCase]);
    renderView(scopedPrincipal("RISK_ADMIN", reviewerId));
    await screen.findByRole("heading", { name: "Риск и паи" });
    await user.click(screen.getByRole("button", { name: "Ответственность" }));
    const openSection = screen.getByRole("heading", { name: "Открыть случай ответственности" }).closest("section");
    const openForm = within(openSection!);
    await user.type(openForm.getByLabelText("Код инцидента"), "INCIDENT-NEW");
    await user.type(openForm.getByLabelText("Затронутая сумма"), "9");
    await user.type(openForm.getByLabelText("Факты"), "Поставка подтвержденно сорвана.");
    fireEvent.change(openForm.getByLabelText("Причинная схема JSON"), {
      target: { value: '{"cause":"default","effect":"loss"}' },
    });
    await user.upload(openForm.getByLabelText("Основание"), new File(["case"], "case.txt", { type: "text/plain" }));
    fireEvent.submit(openSection!.querySelector("form")!);
    await waitFor(() => expect(openLiabilityCase).toHaveBeenCalledWith(expect.objectContaining({
      commitment_id: activeCommitment.id,
      incident_reference: "INCIDENT-NEW",
      affected_amount: "9",
      causal_graph: { cause: "default", effect: "loss" },
    })));

    await user.selectOptions(screen.getByLabelText("Класс вины"), "GROSS_NEGLIGENCE");
    await user.type(screen.getByLabelText("Оцененный ущерб"), "6");
    await user.type(screen.getByLabelText("Мотивировка оценки"), "Независимая проверка материалов");
    await user.upload(screen.getByLabelText("Основание оценки"), new File(["assessment"], "assessment.txt", { type: "text/plain" }));
    await user.click(screen.getByRole("button", { name: "Оценить" }));
    await waitFor(() => expect(assessLiabilityCase).toHaveBeenCalledWith(
      liabilityCase,
      expect.objectContaining({
        fault_class: "GROSS_NEGLIGENCE",
        assessed_loss: "6",
        rationale: "Независимая проверка материалов",
        evidence_ids: [evidenceId],
      }),
    ));
    expect(screen.getByText(/исполнение: NOT_EXECUTED/)).toBeInTheDocument();
  });

  it("renders a stable API error instead of partial risk data", async () => {
    vi.mocked(getRiskPolicies).mockRejectedValue(new AdminApiError("RISK_READ_DENIED", "request-7", 403));
    renderView(scopedPrincipal("RISK_ADMIN"));
    expect(await screen.findByText("You do not have permission to perform this action. Contact the cooperative administrator.")).toBeInTheDocument();
  });
});
