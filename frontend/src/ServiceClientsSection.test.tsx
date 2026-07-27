import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ServiceClientsSection from "./ServiceClientsSection";
import * as admin from "./api/admin";
import i18n from "./i18n";

vi.mock("./api/admin", async (importOriginal) => ({
  ...await importOriginal<typeof import("./api/admin")>(),
  decideServiceClientRequest: vi.fn(),
  getSecurityState: vi.fn(),
  getServiceClientRequests: vi.fn(),
  getServiceClients: vi.fn(),
  requestServiceClientChange: vi.fn(),
  revokeServiceClient: vi.fn(),
  suspendServiceClient: vi.fn(),
  verifyTotpStepUp: vi.fn(),
}));

const cooperative: admin.Cooperative = {
  id: "20000000-0000-0000-0000-000000000001",
  code: "demo-coop",
  name: "Demo cooperative",
  status: "ACTIVE",
  created_at: "2026-07-27T00:00:00Z",
  updated_at: "2026-07-27T00:00:00Z",
  version: 1,
};

const principal: admin.Principal = {
  user_id: "30000000-0000-0000-0000-000000000001",
  login: "security",
  member_id: "50000000-0000-0000-0000-000000000001",
  must_change_password: false,
  roles: [{
    assignment_id: "role-security",
    role: "SECURITY_ADMIN",
    cooperative_id: null,
    source: "ASSIGNMENT",
  }],
};

const serviceClient: admin.ServiceClient = {
  id: "10000000-0000-0000-0000-000000000001",
  client_code: "svc_demo_connector",
  owner_cooperative_id: cooperative.id,
  display_name: "Warehouse connector",
  technical_contact_name: "Alex Admin",
  technical_contact_email: "alex@example.test",
  scopes: ["catalog:read"],
  network_allowlist: ["192.0.2.10/32"],
  rate_limit_per_minute: 60,
  status: "ACTIVE",
  effective_status: "ACTIVE",
  expires_at: "2027-01-01T00:00:00Z",
  registered_by_user_id: "30000000-0000-0000-0000-000000000002",
  approved_by_user_id: principal.user_id,
  created_at: "2026-07-27T00:00:00Z",
  updated_at: "2026-07-27T00:00:00Z",
  suspended_at: null,
  revoked_at: null,
  version: 2,
};

function request(requestedBy = "30000000-0000-0000-0000-000000000002"): admin.ServiceClientRequest {
  return {
    id: "40000000-0000-0000-0000-000000000001",
    service_client_id: serviceClient.id,
    owner_cooperative_id: cooperative.id,
    operation: "ROTATE",
    proposed_config: null,
    expected_client_version: 2,
    reason_code: "ADMIN_SECRET_ROTATION",
    status: "PENDING",
    requested_by_user_id: requestedBy,
    decided_by_user_id: null,
    decision_reason_code: null,
    issued_credential_id: null,
    created_at: "2026-07-27T08:00:00Z",
    expires_at: "2099-07-28T08:00:00Z",
    decided_at: null,
    version: 1,
  };
}

function renderSection() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>
    <ServiceClientsSection principal={principal} cooperatives={[cooperative]} />
  </QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("scrollTo", vi.fn());
  void i18n.changeLanguage("ru");
  vi.mocked(admin.getServiceClients).mockResolvedValue([serviceClient]);
  vi.mocked(admin.getServiceClientRequests).mockResolvedValue([request()]);
  vi.mocked(admin.getSecurityState).mockResolvedValue({
    totp_enabled: true,
    totp_confirmed_at: "2026-07-27T00:00:00Z",
    enrollment_pending: false,
    enrollment_expires_at: null,
    step_up_active: false,
    step_up_method: null,
    step_up_expires_at: null,
    break_glass_grants: 0,
  });
  vi.mocked(admin.requestServiceClientChange).mockResolvedValue({
    event_id: "event-id",
    object_id: "request-id",
    replayed: false,
  });
  vi.mocked(admin.verifyTotpStepUp).mockResolvedValue({
    method: "TOTP",
    verified_at: "2026-07-27T08:00:00Z",
    expires_at: "2026-07-27T08:10:00Z",
  });
  vi.mocked(admin.decideServiceClientRequest).mockResolvedValue({
    event_id: "event-id",
    object_id: serviceClient.id,
    replayed: false,
    service_client_id: serviceClient.id,
    client_code: "svc_new_connector",
    credential_secret: "ccs_12345678123456781234567812345678_demo-secret",
    credential_expires_at: "2027-01-01T00:00:00Z",
  });
  vi.mocked(admin.suspendServiceClient).mockResolvedValue({ event_id: "e", object_id: serviceClient.id, replayed: false });
  vi.mocked(admin.revokeServiceClient).mockResolvedValue({ event_id: "e", object_id: serviceClient.id, replayed: false });
});

afterEach(() => {
  vi.restoreAllMocks();
  void i18n.changeLanguage("ru");
});

describe("ServiceClientsSection", () => {
  it("creates a least-privilege connection request", async () => {
    const user = userEvent.setup();
    renderSection();

    await screen.findByRole("heading", { name: "Подключить внешнюю программу" });
    await user.type(screen.getByLabelText("Название интеграции"), "Dairy catalog");
    await user.type(screen.getByLabelText("Ответственный специалист"), "Ivan Operator");
    await user.type(screen.getByLabelText("Электронная почта специалиста"), "ivan@example.test");
    await user.type(screen.getByLabelText(/Разрешённые IP-адреса/u), "192.0.2.10/32");
    await user.click(screen.getByRole("button", { name: "Отправить на проверку" }));

    await waitFor(() => expect(admin.requestServiceClientChange).toHaveBeenCalledWith(expect.objectContaining({
      owner_cooperative_id: cooperative.id,
      operation: "CREATE",
      reason_code: "ADMIN_INTEGRATION_REQUEST",
      config: expect.objectContaining({
        display_name: "Dairy catalog",
        technical_contact_name: "Ivan Operator",
        technical_contact_email: "ivan@example.test",
        scopes: ["catalog:read"],
        network_allowlist: ["192.0.2.10/32"],
        rate_limit_per_minute: 60,
      }),
    })));
    expect(await screen.findByText(/Заявка отправлена/u)).toBeInTheDocument();
  });

  it("requires TOTP, approves independently, and shows the secret once", async () => {
    const user = userEvent.setup();
    renderSection();

    await user.click(await screen.findByRole("button", { name: "Одобрить" }));
    await user.type(screen.getByLabelText("Шестизначный код из приложения"), "123456");
    await user.click(screen.getByRole("button", { name: "Подтвердить действие" }));

    await waitFor(() => expect(admin.verifyTotpStepUp).toHaveBeenCalledWith("123456"));
    expect(admin.decideServiceClientRequest).toHaveBeenCalledWith(
      expect.objectContaining({ id: request().id }),
      true,
      "INDEPENDENT_REVIEW",
    );
    expect(await screen.findByRole("heading", { name: "Сохраните данные подключения" })).toBeInTheDocument();
    expect(screen.getByText("svc_new_connector")).toBeInTheDocument();
    expect(screen.getByText(/ccs_12345678/u)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Я сохранил данные" }));
    expect(screen.queryByText(/ccs_12345678/u)).not.toBeInTheDocument();
  });

  it("edits, rotates, and suspends an active integration", async () => {
    const user = userEvent.setup();
    renderSection();

    await user.click(await screen.findByRole("button", { name: "Изменить настройки" }));
    expect(screen.getByRole("heading", { name: "Изменить интеграцию" })).toBeInTheDocument();
    expect(screen.getByLabelText("Название интеграции")).toHaveValue("Warehouse connector");
    await user.click(screen.getByRole("button", { name: "Отменить" }));
    expect(screen.getByRole("heading", { name: "Подключить внешнюю программу" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Выпустить новый секрет" }));
    await waitFor(() => expect(admin.requestServiceClientChange).toHaveBeenCalledWith(expect.objectContaining({
      service_client_id: serviceClient.id,
      owner_cooperative_id: cooperative.id,
      operation: "ROTATE",
      expected_client_version: serviceClient.version,
      reason_code: "ADMIN_SECRET_ROTATION",
    })));

    await user.click(screen.getByRole("button", { name: "Немедленно приостановить" }));
    expect(screen.getByRole("heading", { name: "Приостановить интеграцию?" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("Шестизначный код из приложения"), "654321");
    await user.click(screen.getByRole("button", { name: "Подтвердить действие" }));

    await waitFor(() => expect(admin.suspendServiceClient).toHaveBeenCalledWith(
      serviceClient,
      "SECURITY_SUSPENDED",
    ));
  });
  it("does not offer self-approval and renders English from the locale file", async () => {
    vi.mocked(admin.getServiceClientRequests).mockResolvedValue([request(principal.user_id)]);
    await i18n.changeLanguage("en");
    renderSection();

    expect(await screen.findByRole("heading", { name: "Connect an external application" })).toBeInTheDocument();
    expect(screen.getByText("Another reviewer is required")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.getAllByText("Search products and services")).toHaveLength(2);
  });
});