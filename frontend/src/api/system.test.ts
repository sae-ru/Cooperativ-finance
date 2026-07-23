import { describe, expect, it, vi } from "vitest";

import { apiClient } from "./client";
import { ApiRequestError, fetchSystemStatus } from "./system";

const status = {
  status: "OPERATIONAL" as const,
  node: {
    id: "c5e7b672-f259-43d6-924a-05e5b3533c71",
    code: "node-demo-01",
    display_name: "Demo node",
    environment: "test",
    demo_data_loaded: true
  },
  release: {
    version: "0.1.0-test",
    schema_revision: "0001_system_foundation"
  },
  checks: [],
  worker: {
    status: "RUNNING" as const,
    last_seen_at: "2026-07-20T10:30:15Z"
  },
  notices: []
};

describe("fetchSystemStatus", () => {
  it("returns the typed status payload", async () => {
    vi.spyOn(apiClient, "GET").mockResolvedValueOnce({
      data: { data: status, request_id: "request-ok" },
      error: undefined,
      response: new Response(null, { status: 200 })
    } as never);

    await expect(fetchSystemStatus()).resolves.toEqual(status);
  });

  it("preserves the machine error code and request id", async () => {
    vi.spyOn(apiClient, "GET").mockResolvedValueOnce({
      data: undefined,
      error: {
        error: { code: "SERVICE_NOT_READY" },
        request_id: "request-failed"
      },
      response: new Response(null, { status: 503 })
    } as never);

    await expect(fetchSystemStatus()).rejects.toEqual(
      new ApiRequestError("SERVICE_NOT_READY", "request-failed"),
    );
  });
});
