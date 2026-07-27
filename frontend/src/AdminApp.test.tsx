import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AdminApp from "./AdminApp";
import * as admin from "./api/admin";
import * as federation from "./api/federation";
import * as systemApi from "./api/system";
import type { SystemStatus } from "./api/system";
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
vi.mock("./api/federation", async (importOriginal) => ({
  ...await importOriginal<typeof import("./api/federation")>(),
  getFederationNodes: vi.fn(),
}));
vi.mock("./api/system", async (importOriginal) => ({
  ...await importOriginal<typeof import("./api/system")>(),
  fetchSystemStatus: vi.fn(),
}));
vi.mock("./features/system/use-system-status", () => ({ useSystemStatus: vi.fn() }));
vi.mock("./DiscoveryView", () => ({ default: () => <h1>Что вам нужно?</h1> }));
vi.mock("./MemberHomeView", () => ({ default: () => <h1>Мой кабинет</h1> }));

const systemStatus = {
  status: "OPERATIONAL",
  node: {
    id: "c5e7b672-f259-43d6-924a-05e5b3533c71",
    code: "node-demo-01",
    display_name: "Демонстрационный узел",
    environment: "dev",
    demo_data_loaded: true
  },
  release: { version: "0.1.0-dev", schema_revision: "0002_identity_and_audit" },
  checks: [{ name: "database", status: "UP", code: "OK" }],
  worker: { status: "RUNNING", last_seen_at: "2026-07-20T10:30:15Z" },
  notices: []
} as SystemStatus;

const securitySession: admin.AuthSession = {
  access_token: "access",
  access_expires_at: "2026-07-20T11:00:00Z",
  refresh_expires_at: "2026-07-20T20:00:00Z",
  principal: {
    user_id: "10000000-0000-0000-0000-000000000001",
    login: "security",
    member_id: null,
    must_change_password: false,
    roles: [{
      assignment_id: "20000000-0000-0000-0000-000000000001",
      role: "SECURITY_ADMIN",
      cooperative_id: null
    }]
  }
};

const registrarSession: admin.AuthSession = {
  ...securitySession,
  principal: {
    ...securitySession.principal,
    login: "registrar",
    roles: [{
      assignment_id: "20000000-0000-0000-0000-000000000002",
      role: "MEMBER_REGISTRAR",
      cooperative_id: "30000000-0000-0000-0000-000000000001"
    }]
  }
};

function renderApp() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } }
  });
  return render(
    <QueryClientProvider client={client}>
      <AdminApp />
    </QueryClientProvider>,
  );
}

describe("AdminApp", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    vi.clearAllMocks();
    vi.mocked(useSystemStatus).mockReturnValue({
      isPending: false,
      isError: false,
      data: systemStatus,
      error: null
    } as ReturnType<typeof useSystemStatus>);
    vi.mocked(federation.getFederationNodes).mockResolvedValue([]);
    vi.mocked(systemApi.fetchSystemStatus).mockResolvedValue(systemStatus);    vi.mocked(admin.getOverview).mockResolvedValue({
      members: 4,
      active_members: 1,
      cooperatives: 1,
      users: 3,
      active_sessions: 2,
      pending_role_approvals: 1
    });
    vi.mocked(admin.getCooperatives).mockResolvedValue([{
      id: "30000000-0000-0000-0000-000000000001",
      code: "node-demo-01",
      name: "Демо кооператив",
      status: "ACTIVE",
      created_at: "2026-07-20T10:00:00Z",
      updated_at: "2026-07-20T00:00:00Z",
      version: 1
    }]);
    vi.mocked(admin.getMembers).mockResolvedValue([{
      id: "40000000-0000-0000-0000-000000000001",
      display_name: "Анна Петрова",
      registered_by_cooperative_id: "30000000-0000-0000-0000-000000000001",
      status: "ACTIVE",
      created_at: "2026-07-20T10:00:00Z",
      updated_at: "2026-07-20T10:00:00Z",
      version: 1
    }]);
    vi.mocked(admin.getMemberships).mockResolvedValue([]);
    vi.mocked(admin.getUsers).mockResolvedValue([
      {
        id: securitySession.principal.user_id,
        login: "security",
        member_id: null,
        status: "ACTIVE",
        must_change_password: false,
        locked_until: null,
        last_login_at: "2026-07-20T10:00:00Z",
        created_at: "2026-07-20T09:00:00Z",
        version: 1
      },
      {
        id: "10000000-0000-0000-0000-000000000002",
        login: "candidate",
        member_id: null,
        status: "ACTIVE",
        must_change_password: true,
        locked_until: null,
        last_login_at: null,
        created_at: "2026-07-20T09:00:00Z",
        version: 1
      }
    ]);
    vi.mocked(admin.getRoles).mockResolvedValue([{
      id: "50000000-0000-0000-0000-000000000001",
      user_id: "10000000-0000-0000-0000-000000000002",
      role_code: "AUDITOR",
      cooperative_id: null,
      status: "PENDING_APPROVAL",
      granted_by_user_id: "10000000-0000-0000-0000-000000000003",
      approved_by_user_id: null,
      created_at: "2026-07-20T10:00:00Z",
      version: 1
    }]);
    vi.mocked(admin.getSessions).mockResolvedValue([{
      id: "60000000-0000-0000-0000-000000000001",
      user_id: securitySession.principal.user_id,
      status: "ACTIVE",
      access_expires_at: "2026-07-20T11:00:00Z",
      refresh_expires_at: "2026-07-20T20:00:00Z",
      created_at: "2026-07-20T10:00:00Z",
      last_seen_at: "2026-07-20T10:30:00Z",
      revoked_at: null
    }]);
    vi.mocked(admin.getAudit).mockResolvedValue([{
      id: "70000000-0000-0000-0000-000000000001",
      occurred_at: "2026-07-20T10:00:00Z",
      actor_user_id: securitySession.principal.user_id,
      action: "AUTH_LOGIN",
      object_type: "UserAccount",
      object_id: securitySession.principal.user_id,
      outcome: "SUCCESS",
      reason_code: null
    }]);
    vi.mocked(admin.logout).mockResolvedValue(undefined);
    for (const command of [
      admin.createCooperative,
      admin.transitionCooperative,
      admin.createMember,
      admin.transitionMember,
      admin.createMembership,
      admin.transitionMembership,
      admin.createUser,
      admin.transitionUser,
      admin.assignRole,
      admin.decideRole,
      admin.revokeSession
    ]) {
      vi.mocked(command).mockResolvedValue({ event_id: "1", object_id: "2" });
    }
  });

  it("logs in and opens the operational overview", async () => {
    vi.mocked(admin.restoreSession).mockResolvedValue(null);
    vi.mocked(admin.login).mockResolvedValue(securitySession);
    const user = userEvent.setup();
    renderApp();
    await user.type(await screen.findByLabelText("Учетная запись"), "security");
    await user.type(screen.getByLabelText("Пароль"), "production-password");
    await user.click(screen.getByRole("button", { name: "Войти" }));
    expect(await screen.findByRole("heading", { name: "Состояние узла" })).toBeInTheDocument();
    expect(screen.getByText("Демонстрационный узел · node-demo-01")).toBeInTheDocument();
  });

  it("requires a bootstrap password change", async () => {
    vi.mocked(admin.restoreSession).mockResolvedValue({
      ...securitySession,
      principal: { ...securitySession.principal, must_change_password: true }
    });
    vi.mocked(admin.changePassword).mockResolvedValue(securitySession);
    const user = userEvent.setup();
    renderApp();
    await user.type(await screen.findByLabelText("Текущий пароль"), "temporary-password");
    await user.type(screen.getByLabelText("Новый пароль"), "new-production-password");
    await user.click(screen.getByRole("button", { name: "Сменить пароль" }));
    expect(await screen.findByRole("heading", { name: "Состояние узла" })).toBeInTheDocument();
  });

  it("allows leaving the bootstrap password gate", async () => {
    vi.mocked(admin.restoreSession).mockResolvedValue({
      ...securitySession,
      principal: { ...securitySession.principal, must_change_password: true }
    });
    const user = userEvent.setup();
    renderApp();
    await user.click(await screen.findByRole("button", { name: "Выйти" }));
    await waitFor(() => expect(admin.logout).toHaveBeenCalledOnce());
    expect(await screen.findByRole("heading", { name: "Вход оператора" })).toBeInTheDocument();
  });
  it("restores the active workspace after the language reload", async () => {
    vi.mocked(admin.restoreSession).mockResolvedValue(securitySession);
    const user = userEvent.setup();
    const firstView = renderApp();
    await user.click(await screen.findByRole("button", { name: "Аудит" }));
    expect(await screen.findByRole("heading", { name: "Журнал аудита" })).toBeInTheDocument();
    await waitFor(() => {
      expect(window.sessionStorage.getItem(
        `coop.workspace.view.${securitySession.principal.user_id}`,
      )).toBe("audit");
    });

    firstView.unmount();
    renderApp();
    expect(await screen.findByRole("heading", { name: "Журнал аудита" })).toBeInTheDocument();
  });

  it("keeps the active section visible in mobile navigation", async () => {
    vi.mocked(admin.restoreSession).mockResolvedValue(securitySession);
    const originalMatchMedia = window.matchMedia;
    const originalScrollIntoView = Element.prototype.scrollIntoView;
    const scrollIntoView = vi.fn();
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn(() => ({ matches: true }) as MediaQueryList)
    });
    Object.defineProperty(Element.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView
    });

    try {
      const user = userEvent.setup();
      renderApp();
      const audit = await screen.findByRole("button", { name: "Аудит" });
      await user.click(audit);
      await waitFor(() => expect(audit).toHaveAttribute("aria-current", "page"));
      expect(scrollIntoView).toHaveBeenLastCalledWith({ block: "nearest", inline: "center" });
    } finally {
      Object.defineProperty(window, "matchMedia", { configurable: true, value: originalMatchMedia });
      Object.defineProperty(Element.prototype, "scrollIntoView", {
        configurable: true,
        value: originalScrollIntoView
      });
    }
  });
  it("manages accounts, dual-control decisions, sessions, and audit", async () => {
    vi.mocked(admin.restoreSession).mockResolvedValue(securitySession);
    const user = userEvent.setup();
    renderApp();
    await user.click(await screen.findByRole("button", { name: "Доступ" }));
    await user.type(await screen.findByLabelText("Логин"), "new-operator");
    await user.type(screen.getByLabelText("Временный пароль"), "temporary-password-value");
    await user.click(screen.getByRole("button", { name: "Создать вход" }));
    await waitFor(() => expect(admin.createUser).toHaveBeenCalledWith({
      login: "new-operator",
      temporary_password: "temporary-password-value",
      member_id: "40000000-0000-0000-0000-000000000001"
    }));
    expect(admin.assignRole).toHaveBeenCalledWith({
      user_id: "2",
      role: "EXCHANGE_PARTICIPANT",
      cooperative_id: "30000000-0000-0000-0000-000000000001"
    });
    await user.selectOptions(screen.getByRole("combobox", { name: "Учетная запись" }), "10000000-0000-0000-0000-000000000002");
    await user.click(screen.getByRole("button", { name: "Назначить" }));
    await user.click(screen.getByTitle("Одобрить"));
    await user.click(screen.getByTitle("Отозвать сессию"));
    await waitFor(() => expect(admin.assignRole).toHaveBeenCalled());
    expect(admin.decideRole).toHaveBeenCalled();
    expect(admin.revokeSession).toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Аудит" }));
    expect(await screen.findByText("AUTH_LOGIN")).toBeInTheDocument();
  });

  it("opens a basic member directly in the personal workspace", async () => {
    vi.mocked(admin.restoreSession).mockResolvedValue({
      ...securitySession,
      principal: {
        ...securitySession.principal,
        login: "farmer",
        member_id: "40000000-0000-0000-0000-000000000001",
        roles: [{
          assignment_id: "20000000-0000-0000-0000-000000000010",
          role: "EXCHANGE_PARTICIPANT",
          cooperative_id: "30000000-0000-0000-0000-000000000001"
        }]
      }
    });
    renderApp();
    expect(await screen.findByRole("heading", { name: "Мой кабинет" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Главная" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Рынок" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Сделки" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Обзор" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Доступ" })).not.toBeInTheDocument();
  });
  it("creates members, transitions status, and registers membership", async () => {
    vi.mocked(admin.getMembers).mockResolvedValue([{
      id: "40000000-0000-0000-0000-000000000001",
      display_name: "Анна Петрова",
      registered_by_cooperative_id: "30000000-0000-0000-0000-000000000001",
      status: "APPLICANT",
      created_at: "2026-07-20T10:00:00Z",
      updated_at: "2026-07-20T10:00:00Z",
      version: 1
    }]);
    vi.mocked(admin.restoreSession).mockResolvedValue(registrarSession);
    const user = userEvent.setup();
    renderApp();
    await user.click(await screen.findByRole("button", { name: "Администрирование" }));
    await user.type(await screen.findByLabelText("Имя участника"), "Новый участник");
    await user.click(screen.getByRole("button", { name: "Добавить участника" }));
    await user.selectOptions(screen.getByLabelText("Новый статус Анна Петрова"), "PENDING_VERIFICATION");
    await user.click(screen.getByRole("tab", { name: "Членства" }));
    await user.selectOptions(screen.getByLabelText("Участник"), "40000000-0000-0000-0000-000000000001");
    await user.type(screen.getByLabelText("Номер членства"), "D-1000");
    await user.click(screen.getByRole("button", { name: "Оформить членство" }));
    await waitFor(() => expect(admin.createMember).toHaveBeenCalledWith(expect.objectContaining({
      cooperative_id: "30000000-0000-0000-0000-000000000001",
      display_name: "Новый участник"
    })));
    expect(admin.transitionMember).toHaveBeenCalled();
    expect(admin.createMembership).toHaveBeenCalledWith({ cooperative_id: "30000000-0000-0000-0000-000000000001", member_id: "40000000-0000-0000-0000-000000000001", member_number: "D-1000" });
  });
});
