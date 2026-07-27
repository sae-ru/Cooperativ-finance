import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import OperationsView from "./OperationsView";
import * as operations from "./api/operations";
import i18n from "./i18n";

vi.mock("./api/operations", () => ({
  getOperationalSnapshot: vi.fn(),
  getHostReadiness: vi.fn(),
  getDiagnosticPlan: vi.fn(),
  downloadDiagnosticBundle: vi.fn(),
}));

describe("OperationsView", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("ru");
    vi.mocked(operations.getOperationalSnapshot).mockResolvedValue({
      generated_at: "2026-07-21T20:00:00Z",
      schema_revision: "0016_peer_protocol",
      signed_events: 230,
      outbox_pending: 0,
      outbox_quarantined: 2,
      active_sessions: 4,
      open_trust_cases: 0,
      submitted_appeals: 0,
      open_sync_conflicts: 1,
      open_node_incidents: 0,
      pending_key_rotations: 0,
      open_offline_epochs: 1,
      issued_federation_forms: 3,
      active_federated_prepares: 2,
      pending_federated_applies: 1,
      expired_federated_prepares: 0,
      active_crisis_mandates: 0,
      issued_crisis_forms: 1,
    });
    vi.mocked(operations.getHostReadiness).mockResolvedValue({
      generated_at: "2026-07-21T20:00:00Z",
      status: "ATTENTION",
      checks: [
        {
          name: "storage",
          status: "OK",
          code: "DISK_OK",
          observed_at: "2026-07-21T20:00:00Z",
          metrics: { free_percent: 54, free_bytes: 64_424_509_440 },
        },
        {
          name: "clock",
          status: "OK",
          code: "CLOCK_OK",
          observed_at: "2026-07-21T20:00:00Z",
          metrics: { database_drift_seconds: 1, host_clock_status: "SYNCED" },
        },
        {
          name: "backup",
          status: "WARNING",
          code: "BACKUP_DATA_ONLY",
          observed_at: "2026-07-21T19:00:00Z",
          metrics: { age_hours: 1, backup_kind: "DATA_ONLY" },
        },
        {
          name: "certificates",
          status: "OK",
          code: "CERTIFICATES_OK",
          observed_at: "2026-07-21T20:00:00Z",
          metrics: { active: 1, expired: 0, expiring: 0, nearest_expiry_days: 80 },
        },
        {
          name: "ups",
          status: "UNKNOWN",
          code: "UPS_NOT_CONFIGURED",
          observed_at: "2026-07-21T20:00:00Z",
          metrics: { ups_status: "NOT_CONFIGURED" },
        },
      ],
    });
    vi.mocked(operations.getDiagnosticPlan).mockResolvedValue({
      included: ["manifest.json", "operations.json", "host-readiness.json", "metrics.prom"],
      excluded: ["raw_logs", "personal_data"],
      encryption: "AES-256-GCM+scrypt",
    });
  });

  it("renders local readiness without exposing internal status codes", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><OperationsView /></QueryClientProvider>);

    expect(await screen.findByRole("heading", { name: "Эксплуатация узла" })).toBeInTheDocument();
    expect(screen.getByText("230")).toBeInTheDocument();
    expect(screen.getByText("В карантине")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Готовность сервера" })).toBeInTheDocument();
    expect(screen.getByText("Копия неполная: нет материалов восстановления или выпуска системы")).toBeInTheDocument();
    expect(screen.getByText("ИБП не настроен")).toBeInTheDocument();
    expect(screen.queryByText("BACKUP_DATA_ONLY")).not.toBeInTheDocument();
    expect(screen.queryByText("NOT_CONFIGURED")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Диагностический пакет" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Скачать пакет" })).toBeDisabled();
    expect(screen.getByText((_, element) => (
      element?.classList.contains("release") === true
      && element.textContent?.includes("0016_peer_protocol") === true
    ))).toBeInTheDocument();
  });
  it("renders the complete operations view in English without Russian labels", async () => {
    await i18n.changeLanguage("en");
    document.documentElement.lang = "en";
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { container } = render(<QueryClientProvider client={client}><OperationsView /></QueryClientProvider>);

    expect(await screen.findByRole("heading", { name: "Node operations" })).toBeInTheDocument();
    expect(screen.getByText("Prepared clearings")).toBeInTheDocument();
    expect(screen.getByText("Certificate issued but not applied by every node")).toBeInTheDocument();
    expect(screen.getByText("Preparations released without a certificate")).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/[А-Яа-яЁё]/u);
  });
});
