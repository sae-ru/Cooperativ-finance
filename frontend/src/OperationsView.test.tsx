import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import OperationsView from "./OperationsView";
import * as operations from "./api/operations";

vi.mock("./api/operations", () => ({ getOperationalSnapshot: vi.fn() }));

describe("OperationsView", () => {
  beforeEach(() => {
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
      active_crisis_mandates: 0,
      issued_crisis_forms: 1,
    });
  });

  it("renders the PII-free operational snapshot", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><OperationsView /></QueryClientProvider>);

    expect(await screen.findByRole("heading", { name: "Эксплуатация узла" })).toBeInTheDocument();
    expect(screen.getByText("230")).toBeInTheDocument();
    expect(screen.getByText("В карантине")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText((_, element) => (
      element?.classList.contains("release") === true
      && element.textContent?.includes("0016_peer_protocol") === true
    ))).toBeInTheDocument();
  });
});
