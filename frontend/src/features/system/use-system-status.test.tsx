import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchSystemStatus } from "../../api/system";
import { systemStatusQueryKey, useSystemStatus } from "./use-system-status";

vi.mock("../../api/system", () => ({ fetchSystemStatus: vi.fn() }));

describe("useSystemStatus", () => {
  afterEach(() => vi.clearAllMocks());

  it("retries a transient status failure and exposes the recovered node state", async () => {
    vi.mocked(fetchSystemStatus)
      .mockRejectedValueOnce(new TypeError("temporarily offline"))
      .mockResolvedValueOnce({ status: "OPERATIONAL" } as Awaited<ReturnType<typeof fetchSystemStatus>>);
    const client = new QueryClient({
      defaultOptions: { queries: { gcTime: 0 } },
    });
    const wrapper = ({ children }: PropsWithChildren) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );

    const { result } = renderHook(() => useSystemStatus(), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true), { timeout: 3_000 });
    expect(result.current.data?.status).toBe("OPERATIONAL");
    expect(fetchSystemStatus).toHaveBeenCalledTimes(2);
    expect(client.getQueryData(systemStatusQueryKey)).toEqual({ status: "OPERATIONAL" });
    client.clear();
  });
});