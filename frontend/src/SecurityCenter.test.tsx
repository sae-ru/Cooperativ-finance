import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SecurityCenter from "./SecurityCenter";
import * as admin from "./api/admin";

vi.mock("qrcode", () => ({
  default: { toDataURL: vi.fn().mockResolvedValue("data:image/png;base64,qr") },
}));
vi.mock("./api/admin", async () => {
  const actual = await vi.importActual<typeof import("./api/admin")>("./api/admin");
  return Object.fromEntries(
    Object.entries(actual).map(([key, value]) => [
      key,
      typeof value === "function" && key !== "AdminApiError" ? vi.fn() : value,
    ]),
  );
});

const participant: admin.Principal = {
  user_id: "10000000-0000-0000-0000-000000000001",
  login: "farmer",
  member_id: "20000000-0000-0000-0000-000000000001",
  must_change_password: false,
  roles: [{
    assignment_id: "30000000-0000-0000-0000-000000000001",
    role: "EXCHANGE_PARTICIPANT",
    cooperative_id: "40000000-0000-0000-0000-000000000001",
  }],
};
const securityPrincipal: admin.Principal = {
  ...participant,
  login: "security",
  roles: [{
    assignment_id: "30000000-0000-0000-0000-000000000002",
    role: "SECURITY_ADMIN",
    cooperative_id: null,
  }],
};

function renderSecurity(principal: admin.Principal) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>
      <SecurityCenter principal={principal} />
    </QueryClientProvider>,
  );
}

describe("SecurityCenter", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(admin.getSecurityState).mockResolvedValue({
      totp_enabled: false,
      totp_confirmed_at: null,
      enrollment_pending: false,
      enrollment_expires_at: null,
      step_up_active: false,
      step_up_method: null,
      step_up_expires_at: null,
      break_glass_grants: 0,
    });
    vi.mocked(admin.beginTotpEnrollment).mockResolvedValue({
      factor_id: "50000000-0000-0000-0000-000000000001",
      secret: "JBSWY3DPEHPK3PXP",
      provisioning_uri: "otpauth://totp/Cooperative%20Clearing:farmer?secret=test",
      expires_at: "2026-07-27T12:15:00Z",
    });
    vi.mocked(admin.confirmTotpEnrollment).mockResolvedValue({
      method: "TOTP",
      verified_at: "2026-07-27T12:00:00Z",
      expires_at: "2026-07-27T12:10:00Z",
    });
    vi.mocked(admin.getUsers).mockResolvedValue([]);
    vi.mocked(admin.getCooperatives).mockResolvedValue([]);
    vi.mocked(admin.getAccountRecoveries).mockResolvedValue([]);
    vi.mocked(admin.getBreakGlassGrants).mockResolvedValue([]);
  });

  it("guides an ordinary user through TOTP enrollment", async () => {
    const user = userEvent.setup();
    renderSecurity(participant);

    await screen.findByRole("heading", { name: "Защита моей учётной записи" });
    await user.type(screen.getByLabelText("Текущий пароль"), "participant-password");
    await user.click(screen.getByRole("button", { name: "Начать подключение" }));

    await waitFor(() => expect(admin.beginTotpEnrollment).toHaveBeenCalledWith("participant-password", undefined));
    expect(await screen.findByAltText("QR-код для приложения с одноразовыми кодами")).toHaveAttribute("src", "data:image/png;base64,qr");
    expect(screen.getByText("JBSWY3DPEHPK3PXP")).toBeVisible();

    await user.type(screen.getByLabelText("Шестизначный код"), "123456");
    await user.click(screen.getByRole("button", { name: "Подтвердить" }));
    await waitFor(() => expect(admin.confirmTotpEnrollment).toHaveBeenCalledWith("123456"));
  });

  it("shows independent recovery and break-glass decisions to security staff", async () => {
    vi.mocked(admin.getSecurityState).mockResolvedValue({
      totp_enabled: true,
      totp_confirmed_at: "2026-07-27T11:00:00Z",
      enrollment_pending: false,
      enrollment_expires_at: null,
      step_up_active: true,
      step_up_method: "TOTP",
      step_up_expires_at: "2026-07-27T12:10:00Z",
      break_glass_grants: 0,
    });
    vi.mocked(admin.getUsers).mockResolvedValue([{
      id: "60000000-0000-0000-0000-000000000001",
      login: "farmer",
      member_id: participant.member_id,
      status: "ACTIVE",
      must_change_password: false,
      locked_until: null,
      last_login_at: null,
      created_at: "2026-07-27T10:00:00Z",
      version: 1,
    }]);
    vi.mocked(admin.getCooperatives).mockResolvedValue([{
      id: "40000000-0000-0000-0000-000000000001",
      code: "demo",
      name: "Демонстрационный кооператив",
      status: "ACTIVE",
      created_at: "2026-07-27T10:00:00Z",
      version: 1,
    }]);
    vi.mocked(admin.getAccountRecoveries).mockResolvedValue([{
      id: "70000000-0000-0000-0000-000000000001",
      target_user_id: "60000000-0000-0000-0000-000000000001",
      requested_by_user_id: "80000000-0000-0000-0000-000000000001",
      decided_by_user_id: null,
      reason_code: "LOST_AUTHENTICATOR",
      evidence_id: "ACT-12",
      status: "PENDING_APPROVAL",
      created_at: "2026-07-27T11:00:00Z",
      expires_at: "2026-07-27T12:00:00Z",
      decided_at: null,
      version: 1,
    }]);
    vi.mocked(admin.getBreakGlassGrants).mockResolvedValue([{
      id: "90000000-0000-0000-0000-000000000001",
      target_user_id: "60000000-0000-0000-0000-000000000001",
      role_code: "CRISIS_OPERATOR",
      cooperative_id: "40000000-0000-0000-0000-000000000001",
      requested_by_user_id: "80000000-0000-0000-0000-000000000001",
      approved_by_user_id: null,
      revoked_by_user_id: null,
      reason_code: "CRISIS_OPERATION",
      evidence_id: "INC-7",
      requested_duration_minutes: 30,
      status: "PENDING_APPROVAL",
      created_at: "2026-07-27T11:00:00Z",
      approved_at: null,
      expires_at: "2026-07-27T12:00:00Z",
      revoked_at: null,
      version: 1,
    }]);
    vi.mocked(admin.decideAccountRecovery).mockResolvedValue({ event_id: "event", object_id: "object" });
    vi.mocked(admin.decideBreakGlass).mockResolvedValue({ event_id: "event", object_id: "object" });
    const user = userEvent.setup();
    renderSecurity(securityPrincipal);

    await screen.findByRole("heading", { name: "Восстановление доступа двумя сотрудниками" });
    await user.click(screen.getByTitle("Одобрить восстановление"));
    await waitFor(() => expect(admin.decideAccountRecovery).toHaveBeenCalledWith(
      "70000000-0000-0000-0000-000000000001",
      true,
      "INDEPENDENT_RECOVERY_REVIEW",
    ));
    await user.click(screen.getByTitle("Одобрить временное право"));
    await waitFor(() => expect(admin.decideBreakGlass).toHaveBeenCalledWith(
      "90000000-0000-0000-0000-000000000001",
      true,
      "INCIDENT_CONFIRMED",
    ));
  });
});
