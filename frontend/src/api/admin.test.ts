import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AdminApiError,
  applyMemberImport,
  checkMemberDuplicates,
  createMember,
  decideMemberImport,
  getMemberImportRows,
  getMemberImports,
  getOverview,
  login,
  logout,
  previewMemberImport,
  restoreSession,
  stageMemberImport,
  type MemberImportBatch,
} from "./admin";

function response(body: object | null, status = 200): Response {
  return new Response(body === null ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "X-Request-ID": "request-1" }
  });
}

describe("administration API client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    document.cookie = "coop_csrf=; Max-Age=0; path=/";
  });

  it("logs in, sends authenticated commands, and logs out", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ data: { access_token: "access", principal: {} } }))
      .mockResolvedValueOnce(response({ data: { members: 2 } }))
      .mockResolvedValueOnce(response({ data: { event_id: "1", object_id: "2" } }, 201))
      .mockResolvedValueOnce(response(null, 204));
    vi.stubGlobal("fetch", fetchMock);
    await login("security", "production-password");
    expect((await getOverview()).members).toBe(2);
    expect((await createMember({ cooperative_id: "coop-1", display_name: "Member" })).object_id).toBe("2");
    await logout();
    const commandRequest = fetchMock.mock.calls[2]?.[1] as RequestInit;
    expect(new Headers(commandRequest.headers).get("Authorization")).toBe("Bearer access");
    expect(new Headers(commandRequest.headers).get("Idempotency-Key")).toBeTruthy();
  });

  it("restores a session with CSRF and reports safe API errors", async () => {
    document.cookie = "coop_csrf=csrf-value; path=/";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ data: { access_token: "rotated", principal: {} } }))
      .mockResolvedValueOnce(response({ error: { code: "AUTHORIZATION_DENIED" }, request_id: "denied-1" }, 403));
    vi.stubGlobal("fetch", fetchMock);
    expect((await restoreSession())?.access_token).toBe("rotated");
    await expect(getOverview()).rejects.toEqual(
      expect.objectContaining<Partial<AdminApiError>>({
        code: "AUTHORIZATION_DENIED",
        requestId: "denied-1",
        status: 403
      }),
    );
    const refreshRequest = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(new Headers(refreshRequest.headers).get("X-CSRF-Token")).toBe("csrf-value");
  });
  it("executes every staged member-import request with versioned commands", async () => {
    const batch = {
      id: "batch-1",
      version: 3,
    } as MemberImportBatch;
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ data: { candidates: [] } }))
      .mockResolvedValueOnce(response({ data: [] }))
      .mockResolvedValueOnce(response({ data: [] }))
      .mockResolvedValueOnce(response({ data: { event_id: "1", object_id: "batch-1" } }, 201))
      .mockResolvedValueOnce(response({ data: { event_id: "2", object_id: "batch-1" } }, 201))
      .mockResolvedValueOnce(response({ data: { event_id: "3", object_id: "batch-1" } }, 201))
      .mockResolvedValueOnce(response({ data: { event_id: "4", object_id: "batch-1" } }, 201));
    vi.stubGlobal("fetch", fetchMock);

    await checkMemberDuplicates({ cooperative_id: "coop-1", display_name: "New member" });
    await getMemberImports();
    await getMemberImportRows(batch.id);
    await stageMemberImport({
      cooperative_id: "coop-1",
      source_name: "members.csv",
      csv_text: "display_name\nNew member\n",
    });
    await previewMemberImport(batch);
    await decideMemberImport(batch, true, "INDEPENDENT_REVIEW");
    await applyMemberImport(batch);

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/v1/admin/members/duplicate-check",
      "/api/v1/admin/imports?limit=500",
      "/api/v1/admin/imports/batch-1/rows",
      "/api/v1/admin/imports",
      "/api/v1/admin/imports/batch-1/dry-run",
      "/api/v1/admin/imports/batch-1/decision",
      "/api/v1/admin/imports/batch-1/apply",
    ]);
    const decision = fetchMock.mock.calls[5]?.[1] as RequestInit;
    expect(JSON.parse(String(decision.body))).toEqual({
      approve: true,
      reason_code: "INDEPENDENT_REVIEW",
      expected_version: 3,
    });
    expect(new Headers(decision.headers).get("Idempotency-Key")).toBeTruthy();
  });
});
