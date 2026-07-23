import { afterEach, describe, expect, it, vi } from "vitest";

import { getOperationalSnapshot } from "./operations";

describe("operations API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("reads the protected operational snapshot", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      data: { schema_revision: "0016_peer_protocol" },
    }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getOperationalSnapshot()).resolves.toEqual({
      schema_revision: "0016_peer_protocol",
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/operations/snapshot",
      expect.objectContaining({ credentials: "same-origin" }),
    );
  });
});
