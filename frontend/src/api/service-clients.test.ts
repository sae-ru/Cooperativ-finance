import { afterEach, describe, expect, it, vi } from "vitest";

import {
  type ServiceClient,
  type ServiceClientRequest,
  decideServiceClientRequest,
  getServiceClientRequests,
  getServiceClients,
  requestServiceClientChange,
  revokeServiceClient,
  suspendServiceClient,
} from "./admin";

function response(data: unknown, status = 200): Response {
  return new Response(JSON.stringify({ data }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const serviceClient: ServiceClient = {
  id: "10000000-0000-0000-0000-000000000001",
  client_code: "svc_demo",
  owner_cooperative_id: "20000000-0000-0000-0000-000000000001",
  display_name: "Warehouse connector",
  technical_contact_name: "Alex Admin",
  technical_contact_email: "alex@example.test",
  scopes: ["catalog:read"],
  network_allowlist: ["192.0.2.10/32"],
  rate_limit_per_minute: 60,
  status: "ACTIVE",
  effective_status: "ACTIVE",
  expires_at: "2027-01-01T00:00:00Z",
  registered_by_user_id: "30000000-0000-0000-0000-000000000001",
  approved_by_user_id: "30000000-0000-0000-0000-000000000002",
  created_at: "2026-07-27T00:00:00Z",
  updated_at: "2026-07-27T00:00:00Z",
  suspended_at: null,
  revoked_at: null,
  version: 2,
};

const changeRequest: ServiceClientRequest = {
  id: "40000000-0000-0000-0000-000000000001",
  service_client_id: serviceClient.id,
  owner_cooperative_id: serviceClient.owner_cooperative_id,
  operation: "ROTATE",
  proposed_config: null,
  expected_client_version: 2,
  reason_code: "ADMIN_SECRET_ROTATION",
  status: "PENDING",
  requested_by_user_id: serviceClient.registered_by_user_id,
  decided_by_user_id: null,
  decision_reason_code: null,
  issued_credential_id: null,
  created_at: "2026-07-27T00:00:00Z",
  expires_at: "2026-07-28T00:00:00Z",
  decided_at: null,
  version: 1,
};

afterEach(() => vi.unstubAllGlobals());

describe("service client administration API", () => {
  it("loads the client and approval registries", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response([serviceClient]))
      .mockResolvedValueOnce(response([changeRequest]));
    vi.stubGlobal("fetch", fetchMock);

    expect(await getServiceClients()).toEqual([serviceClient]);
    expect(await getServiceClientRequests()).toEqual([changeRequest]);
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      "/api/v1/admin/service-clients",
      "/api/v1/admin/service-client-requests",
    ]);
  });

  it("sends versioned and idempotent lifecycle commands", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(response({
      event_id: "event-id",
      object_id: "object-id",
      replayed: false,
    }, 201)));
    vi.stubGlobal("fetch", fetchMock);

    await requestServiceClientChange({
      owner_cooperative_id: serviceClient.owner_cooperative_id,
      operation: "ROTATE",
      service_client_id: serviceClient.id,
      expected_client_version: serviceClient.version,
      reason_code: "ADMIN_SECRET_ROTATION",
    });
    await decideServiceClientRequest(changeRequest, true, "INDEPENDENT_REVIEW");
    await suspendServiceClient(serviceClient, "SECURITY_SUSPENDED");
    await revokeServiceClient(serviceClient, "SECURITY_REVOKED");

    expect(fetchMock).toHaveBeenCalledTimes(4);
    for (const [, init] of fetchMock.mock.calls) {
      expect(init.method).toBe("POST");
      expect(new Headers(init.headers).get("Idempotency-Key")).toBeTruthy();
    }
    expect(JSON.parse(fetchMock.mock.calls[1]![1].body as string)).toEqual({
      approve: true,
      reason_code: "INDEPENDENT_REVIEW",
      expected_version: 1,
    });
    expect(JSON.parse(fetchMock.mock.calls[2]![1].body as string)).toEqual({
      reason_code: "SECURITY_SUSPENDED",
      expected_version: 2,
    });
  });
});