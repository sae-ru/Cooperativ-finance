import type { paths } from "./schema";
import { apiClient } from "./client";

export type SystemStatusEnvelope =
  paths["/api/v1/system/status"]["get"]["responses"][200]["content"]["application/json"];

export type SystemStatus = SystemStatusEnvelope["data"];

export class ApiRequestError extends Error {
  constructor(
    public readonly code: string,
    public readonly requestId: string | null,
  ) {
    super(code);
    this.name = "ApiRequestError";
  }
}

export async function fetchSystemStatus(signal?: AbortSignal): Promise<SystemStatus> {
  const { data, error, response } = await apiClient.GET("/api/v1/system/status", {
    signal
  });
  if (!response.ok || data === undefined) {
    const envelope = error as
      | { error?: { code?: string }; request_id?: string }
      | undefined;
    throw new ApiRequestError(
      envelope?.error?.code ?? "SYSTEM_STATUS_UNAVAILABLE",
      envelope?.request_id ?? response.headers.get("X-Request-ID"),
    );
  }
  return data.data;
}
