import { afterEach, describe, expect, it, vi } from "vitest";

import {
  assignRole,
  createMembership,
  createUser,
  decideRole,
  getAudit,
  getCooperatives,
  getMembers,
  getMemberships,
  getOverview,
  getRoles,
  getSessions,
  getUsers,
  revokeSession,
  restoreSession
} from "./admin";

function response(body: object, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" }
  });
}

describe("administration API recovery and endpoint coverage", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    document.cookie = "coop_csrf=; Max-Age=0; path=/";
  });

  it("handles missing refresh state and retries after access expiry", async () => {
    expect(await restoreSession()).toBeNull();
    document.cookie = "coop_csrf=retry-csrf; path=/";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ error: { code: "AUTHENTICATION_FAILED" } }, 401))
      .mockResolvedValueOnce(response({ data: { access_token: "fresh", principal: {} } }))
      .mockResolvedValueOnce(response({ data: { members: 7 } }));
    vi.stubGlobal("fetch", fetchMock);
    expect((await getOverview()).members).toBe(7);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("shares one rotating refresh across concurrent expired requests", async () => {
    document.cookie = "coop_csrf=shared-csrf; path=/";
    let refreshCalls = 0;
    const fetchMock = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/v1/auth/refresh") {
        refreshCalls += 1;
        await Promise.resolve();
        return response({
        data: { access_token: ["shared-", "fresh"].join(""), principal: {} },
      });
      }
      const authorization = new Headers(fetchMock.mock.calls.at(-1)?.[1]?.headers).get("Authorization");
      return authorization === "Bearer shared-fresh"
        ? response({ data: { members: 7 } })
        : response({ error: { code: "AUTHENTICATION_FAILED" } }, 401);
    });
    vi.stubGlobal("fetch", fetchMock);

    const [first, second] = await Promise.all([getOverview(), getOverview()]);

    expect(first.members).toBe(7);
    expect(second.members).toBe(7);
    expect(refreshCalls).toBe(1);
  });

  it("calls collection and command endpoints", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => Promise.resolve(response({ data: [] }))));
    await Promise.all([
      getCooperatives(),
      getMembers(),
      getMemberships(),
      getUsers(),
      getRoles(),
      getSessions(),
      getAudit()
    ]);

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() =>
        Promise.resolve(response({ data: { event_id: "1", object_id: "2" } }, 201)),
      ),
    );
    await createMembership({ cooperative_id: "c", member_id: "m", member_number: "1" });
    await createUser({
      login: "operator",
      temporary_password: "long-password-value",
      member_id: null
    });
    await assignRole({ user_id: "u", role: "AUDITOR", cooperative_id: null });
    await decideRole("r", true);
    await revokeSession("s");
  });
});
