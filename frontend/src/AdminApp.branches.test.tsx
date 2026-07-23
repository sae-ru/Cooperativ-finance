import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AdminApp from "./AdminApp";
import * as admin from "./api/admin";
import { useSystemStatus } from "./features/system/use-system-status";

vi.mock("./api/admin", async () => {
  const actual = await vi.importActual<typeof import("./api/admin")>("./api/admin");
  return Object.fromEntries(
    Object.entries(actual).map(([key, value]) => [
      key,
      typeof value === "function" && key !== "AdminApiError" ? vi.fn() : value
    ]),
  );
});
vi.mock("./features/system/use-system-status", () => ({ useSystemStatus: vi.fn() }));

describe("AdminApp degraded branches", () => {
  beforeEach(() => {
    vi.mocked(admin.restoreSession).mockResolvedValue({
      access_token: "access",
      access_expires_at: "2026-07-20T11:00:00Z",
      refresh_expires_at: "2026-07-20T20:00:00Z",
      principal: {
        user_id: "10000000-0000-0000-0000-000000000001",
        login: "auditor",
        member_id: null,
        must_change_password: false,
        roles: [{
          assignment_id: "20000000-0000-0000-0000-000000000001",
          role: "AUDITOR",
          cooperative_id: null
        }]
      }
    });
    vi.mocked(admin.getOverview).mockResolvedValue({
      members: 0,
      active_members: 0,
      cooperatives: 0,
      users: 1,
      active_sessions: 1,
      pending_role_approvals: 0
    });
    vi.mocked(admin.logout).mockResolvedValue();
    vi.mocked(useSystemStatus).mockReturnValue({
      isPending: false,
      isError: false,
      error: null,
      data: {
        status: "DEGRADED",
        node: {
          id: "node-id",
          code: "node-test-01",
          display_name: "Тестовый узел",
          environment: "test",
          demo_data_loaded: false
        },
        release: { version: "test", schema_revision: "0002_identity_and_audit" },
        checks: [{ name: "database", status: "DOWN", code: "UNAVAILABLE" }],
        worker: { status: "STALE", last_seen_at: null },
        notices: [{
          code: "ATTENTION_REQUIRED",
          severity: "CRITICAL",
          message_key: "notices.attention_required",
          parameters: {},
          created_at: "2026-07-20T10:00:00Z"
        }]
      }
    } as ReturnType<typeof useSystemStatus>);
  });

  it("shows degraded state and closes the operator session", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={client}>
        <AdminApp />
      </QueryClientProvider>,
    );
    expect(await screen.findByText("Требует внимания", {}, { timeout: 5_000 })).toBeInTheDocument();
    expect(screen.getByText("ATTENTION_REQUIRED")).toBeInTheDocument();
    expect(screen.getByText("UNAVAILABLE")).toBeInTheDocument();
    await user.click(screen.getByTitle("Выйти"));
    expect(admin.logout).toHaveBeenCalled();
    expect(await screen.findByRole("heading", { name: "Вход оператора" })).toBeInTheDocument();
  });
});
