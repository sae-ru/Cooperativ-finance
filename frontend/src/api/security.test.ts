import { afterEach, describe, expect, it, vi } from "vitest";

import {
  beginTotpEnrollment,
  confirmTotpEnrollment,
  decideAccountRecovery,
  decideBreakGlass,
  disableTotp,
  getAccountRecoveries,
  getBreakGlassGrants,
  getSecurityState,
  requestAccountRecovery,
  requestBreakGlass,
  revokeBreakGlass,
  verifyTotpStepUp,
} from "./admin";

function response(data: object, status = 200): Response {
  return new Response(JSON.stringify({ data }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("identity security API client", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("calls account security and TOTP endpoints", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(response({})));
    vi.stubGlobal("fetch", fetchMock);

    await getSecurityState();
    await beginTotpEnrollment("password-value-123", "123456");
    await confirmTotpEnrollment("234567");
    await verifyTotpStepUp("345678");
    await disableTotp("password-value-123", "456789", "USER_CONFIRMED_DISABLE");

    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      "/api/v1/auth/security",
      "/api/v1/auth/totp/enrollment",
      "/api/v1/auth/totp/enrollment/confirm",
      "/api/v1/auth/step-up/totp",
      "/api/v1/auth/totp",
    ]);
    expect(fetchMock.mock.calls[4]?.[1]?.method).toBe("DELETE");
  });

  it("calls dual-control recovery and break-glass endpoints with idempotency", async () => {
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(response({ event_id: "e", object_id: "o" })),
    );
    vi.stubGlobal("fetch", fetchMock);

    await getAccountRecoveries();
    await requestAccountRecovery({
      target_user_id: "user",
      temporary_password: "temporary-password-123",
      reason_code: "LOST_AUTHENTICATOR",
      evidence_id: "ACT-1",
    });
    await decideAccountRecovery("recovery", true, "INDEPENDENT_REVIEW");
    await getBreakGlassGrants();
    await requestBreakGlass({
      target_user_id: "user",
      role: "CRISIS_OPERATOR",
      cooperative_id: "cooperative",
      duration_minutes: 30,
      reason_code: "CRISIS_OPERATION",
      evidence_id: "INC-1",
    });
    await decideBreakGlass("grant", true, "INCIDENT_CONFIRMED");
    await revokeBreakGlass("grant", "EMERGENCY_ENDED");

    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      "/api/v1/admin/account-recoveries",
      "/api/v1/admin/account-recoveries",
      "/api/v1/admin/account-recoveries/recovery/decision",
      "/api/v1/admin/break-glass",
      "/api/v1/admin/break-glass",
      "/api/v1/admin/break-glass/grant/decision",
      "/api/v1/admin/break-glass/grant/revoke",
    ]);
    for (const call of [fetchMock.mock.calls[1], fetchMock.mock.calls[2], fetchMock.mock.calls[4], fetchMock.mock.calls[5], fetchMock.mock.calls[6]]) {
      expect(new Headers(call?.[1]?.headers).has("Idempotency-Key")).toBe(true);
    }
  });
});
