import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import "./i18n";
import i18n from "./i18n";
import type { AntifraudSignal } from "./api/antifraud";
import type { Principal } from "./api/admin";
import AntifraudView from "./AntifraudView";

vi.mock("./api/admin", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/admin")>();
  return { ...actual, getCooperatives: vi.fn() };
});

vi.mock("./api/inventory", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/inventory")>();
  return { ...actual, uploadEvidence: vi.fn() };
});

vi.mock("./api/antifraud", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/antifraud")>();
  return {
    ...actual,
    getAntifraudOverview: vi.fn(),
    getAntifraudRules: vi.fn(),
    getAntifraudScans: vi.fn(),
    getAntifraudSignals: vi.fn(),
    runAntifraudScan: vi.fn(),
    beginAntifraudReview: vi.fn(),
    decideAntifraudSignal: vi.fn(),
  };
});

const cooperativeId = "10000000-0000-4000-8000-000000000001";
const auditorMemberId = "10000000-0000-4000-8000-000000000002";
const signal = {
  id: "20000000-0000-4000-8000-000000000001",
  cooperative_id: cooperativeId,
  scan_id: "20000000-0000-4000-8000-000000000002",
  rule_code: "OFFER_PRICE_OUTLIER",
  rule_version: 1,
  subject_type: "OFFER",
  subject_id: "20000000-0000-4000-8000-000000000003",
  severity: "HIGH",
  automation_action: "HOLD",
  status: "IN_REVIEW",
  reason_key: "antifraud.reasons.offer_price_outlier",
  observed_data: {
    unit_total: "9.550000000000",
    sample_size: 3,
    product_code: "MILK.UHT.3_2",
  },
  threshold_data: {
    median: "1.950000000000",
    upper_ratio: "2",
  },
  occurrence_count: 1,
  first_seen_at: "2026-07-26T10:00:00Z",
  last_seen_at: "2026-07-26T10:00:00Z",
  detected_by_member_id: "20000000-0000-4000-8000-000000000004",
  detected_event_id: "20000000-0000-4000-8000-000000000005",
  reviewer_member_id: auditorMemberId,
  review_started_event_id: "20000000-0000-4000-8000-000000000006",
  decision_event_id: null,
  decision_rationale: null,
  created_at: "2026-07-26T10:00:00Z",
  updated_at: "2026-07-26T10:10:00Z",
  reviewed_at: null,
  version: 2,
} as AntifraudSignal;

const principal: Principal = {
  user_id: "30000000-0000-4000-8000-000000000001",
  login: "auditor",
  member_id: auditorMemberId,
  must_change_password: false,
  roles: [{
    assignment_id: "30000000-0000-4000-8000-000000000002",
    role: "AUDITOR",
    cooperative_id: cooperativeId,
  }],
};

function renderView() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <AntifraudView principal={principal} />
    </QueryClientProvider>,
  );
}

describe("AntifraudView", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("ru");
    const admin = await import("./api/admin");
    const inventory = await import("./api/inventory");
    const api = await import("./api/antifraud");
    vi.mocked(admin.getCooperatives).mockResolvedValue([{
      id: cooperativeId,
      code: "DEMO",
      name: "Демонстрационный кооператив",
      status: "ACTIVE",
      created_at: "2026-07-26T00:00:00Z",
      updated_at: "2026-07-20T00:00:00Z",
      version: 1,
    }]);
    vi.mocked(api.getAntifraudRules).mockResolvedValue({
      algorithm_version: "2.0.0",
      manifest_hash: `sha256:${"a".repeat(64)}`,
      calibration_dataset_version: "synthetic-v2.0.0",
      calibration_scope: "SYNTHETIC_REGRESSION",
      requirement_count: 13,
      rule_count: 15,
      production_approved: false,
      rules: [{
        code: "PURCHASE_CANCELLATION_BURST",
        rule_version: 1,
        requirement_key: "antifraud.requirements.synthetic_demand",
        severity: "HIGH",
        action: "HOLD",
        data_sources: ["federation.purchase_intents"],
        calibration_dataset_version: "synthetic-v2.0.0",
        engineering_case_count: 2,
        pilot_false_positive_rate: null,
        production_approved: false,
      }],
    });
    vi.mocked(api.getAntifraudOverview).mockResolvedValue({
      cooperative_count: 1,
      signal_count: 1,
      active_hold_count: 1,
      by_status: { IN_REVIEW: 1 },
      by_severity: { HIGH: 1 },
      latest_scan_at: "2026-07-26T10:00:00Z",
    });
    vi.mocked(api.getAntifraudScans).mockResolvedValue([{
      id: signal.scan_id,
      cooperative_id: cooperativeId,
      algorithm_version: "2.0.0",
      rule_manifest_hash: `sha256:${"a".repeat(64)}`,
      calibration_dataset_version: "synthetic-v2.0.0",
      lookback_hours: 168,
      input_cutoff: "2026-07-26T10:00:00Z",
      finding_count: 1,
      result_summary: {},
      initiated_by_member_id: signal.detected_by_member_id,
      completed_event_id: signal.detected_event_id,
      created_at: "2026-07-26T10:00:00Z",
    }]);
    vi.mocked(api.getAntifraudSignals).mockResolvedValue([signal]);
    vi.mocked(inventory.uploadEvidence).mockResolvedValue(
      "40000000-0000-4000-8000-000000000001",
    );
    vi.mocked(api.decideAntifraudSignal).mockResolvedValue({
      event_id: "event-1",
      object_id: signal.id,
      replayed: false,
    });
  });

  it("explains the signal and submits an evidence-backed independent decision", async () => {
    const user = userEvent.setup();
    const inventory = await import("./api/inventory");
    const api = await import("./api/antifraud");
    renderView();

    expect(await screen.findByRole("heading", { name: "Проверка аномалий" }))
      .toBeInTheDocument();
    expect(screen.getByText("Сигнал не является обвинением")).toBeInTheDocument();
    expect(screen.getAllByText(
      "Цена сильно отличается от сопоставимых предложений",
    ).length).toBeGreaterThan(0);

    await user.click(screen.getAllByRole("button", { name: "Разобрать" })[0]!);
    await user.type(
      screen.getByLabelText("Обоснование решения"),
      "Цена подтверждена исправленным документом.",
    );
    await user.upload(
      screen.getByLabelText("Подтверждающий документ"),
      new File(["proof"], "price-proof.txt", { type: "text/plain" }),
    );
    await user.click(screen.getByRole("button", { name: "Снять ограничение" }));

    await waitFor(() => expect(inventory.uploadEvidence).toHaveBeenCalled());
    await waitFor(() => expect(api.decideAntifraudSignal).toHaveBeenCalledWith(
      signal,
      expect.objectContaining({
        decision: "CLEARED",
        evidence_ids: ["40000000-0000-4000-8000-000000000001"],
      }),
    ));
  });

  it("shows rule coverage without claiming pilot approval", async () => {
    await i18n.changeLanguage("en");
    renderView();

    expect(await screen.findByRole("heading", { name: "Risks checked by the system" }))
      .toBeInTheDocument();
    expect(screen.getByText("Risk classes covered: 13. Active rules: 15."))
      .toBeInTheDocument();
    expect(screen.getByText(/still requires pilot calibration/)).toBeInTheDocument();
  });
});
