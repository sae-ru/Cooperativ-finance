import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AdminApp from "./AdminApp";
import * as admin from "./api/admin";
import { auditAccessibility } from "./test/accessibility";

vi.mock("./api/admin", async () => {
  const actual = await vi.importActual<typeof import("./api/admin")>("./api/admin");
  return { ...actual, restoreSession: vi.fn() };
});

function renderApplication() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AdminApp />
    </QueryClientProvider>,
  );
}

describe("authentication accessibility gates", () => {
  beforeEach(() => {
    vi.mocked(admin.restoreSession).mockResolvedValue(null);
  });

  it("keeps the login form keyboard and screen-reader addressable", async () => {
    const view = renderApplication();
    await screen.findByRole("heading", { name: "Вход оператора" });
    expect(auditAccessibility(view.container)).toEqual([]);
  });

  it("keeps mandatory password change addressable", async () => {
    vi.mocked(admin.restoreSession).mockResolvedValue({
      access_token: "access",
      access_expires_at: "2026-07-21T20:00:00Z",
      refresh_expires_at: "2026-07-22T08:00:00Z",
      principal: {
        user_id: "10000000-0000-0000-0000-000000000001",
        login: "registrar",
        member_id: null,
        must_change_password: true,
        roles: []
      }
    });
    const view = renderApplication();
    await screen.findByRole("heading", { name: "Смена временного пароля" });
    expect(auditAccessibility(view.container)).toEqual([]);
  });
});
