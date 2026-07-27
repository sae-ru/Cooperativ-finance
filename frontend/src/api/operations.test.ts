import { afterEach, describe, expect, it, vi } from "vitest";

import {
  downloadDiagnosticBundle,
  getDiagnosticPlan,
  getHostReadiness,
  getOperationalSnapshot,
} from "./operations";

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

  it("reads host readiness and the bounded diagnostic plan", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        data: { status: "ATTENTION", checks: [] },
      }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        data: { included: ["manifest.json"], excluded: ["raw_logs"], encryption: "AES-256-GCM+scrypt" },
      }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getHostReadiness()).resolves.toMatchObject({ status: "ATTENTION" });
    await expect(getDiagnosticPlan()).resolves.toMatchObject({ excluded: ["raw_logs"] });
  });

  it("downloads an encrypted bundle with a POST body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(new Uint8Array([1, 2, 3]), {
      status: 200,
      headers: { "Content-Type": "application/vnd.cooperative-clearing.diagnostic" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    const blob = await downloadDiagnosticBundle("diagnostic passphrase 2026");

    expect(blob.size).toBe(3);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/operations/diagnostic-bundle",
      expect.objectContaining({
        method: "POST",
        credentials: "same-origin",
        body: JSON.stringify({ passphrase: "diagnostic passphrase 2026" }),
      }),
    );
  });
});
