import { afterEach, describe, expect, it, vi } from "vitest";

import {
  applySyncPackage,
  changeFederationNodeStatus,
  downloadSyncArchive,
  getFederationNodes,
  getFederationPaperForms,
  getNodeApplications,
  getNodeBonds,
  getNodeChallenges,
  getNodeContracts,
  getNodeExposures,
  getNodeIncidents,
  getNodeKeyRotations,
  getNodeLimits,
  getNodeResponsibilities,
  getOfflineEpochs,
  getSyncConflicts,
  getSyncPackages,
  getSyncReceipts,
  importSyncPackage,
  type FederationNode,
  type SyncPackage,
} from "./federation";

function envelope(data: unknown, status = 200): Response {
  return new Response(JSON.stringify({ data }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("federation API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("uses the complete read model endpoints", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(envelope([])));
    vi.stubGlobal("fetch", fetchMock);

    await getFederationNodes();
    await getNodeApplications();
    await getNodeResponsibilities();
    await getNodeChallenges();
    await getNodeContracts();
    await getNodeLimits();
    await getNodeBonds();
    await getNodeExposures();
    await getOfflineEpochs();
    await getFederationPaperForms();
    await getSyncPackages();
    await getSyncConflicts();
    await getSyncReceipts();
    await getNodeIncidents();
    await getNodeKeyRotations();

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/v1/federation/nodes",
      "/api/v1/federation/nodes/applications",
      "/api/v1/federation/responsibilities",
      "/api/v1/federation/challenges",
      "/api/v1/federation/trust-contracts",
      "/api/v1/federation/bilateral-limits",
      "/api/v1/federation/bonds",
      "/api/v1/federation/exposures",
      "/api/v1/federation/offline-epochs",
      "/api/v1/federation/paper-forms",
      "/api/v1/federation/sync/packages",
      "/api/v1/federation/sync/conflicts",
      "/api/v1/federation/sync/receipts",
      "/api/v1/federation/incidents",
      "/api/v1/federation/key-rotations",
    ]);
  });

  it("preserves binary ZIP input and concurrency guards", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(envelope({ event_id: "event-1", object_id: "package-1", replayed: false }, 201))
      .mockResolvedValueOnce(envelope({ event_id: "event-2", object_id: "package-1", replayed: false }))
      .mockResolvedValueOnce(envelope({ event_id: "event-3", object_id: "node-1", replayed: false }))
      .mockResolvedValueOnce(new Response(new Blob(["zip"]), {
        status: 200,
        headers: { "Content-Type": "application/zip" },
      }));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("crypto", { randomUUID: () => "00000000-0000-4000-8000-000000000011" });

    const archive = new File(["PK"], "sync.zip", { type: "application/zip" });
    await importSyncPackage(archive);
    const imported = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(imported.method).toBe("POST");
    expect((imported.headers as Headers).get("Content-Type")).toBe("application/zip");
    expect((imported.headers as Headers).get("Idempotency-Key")).toBe("00000000-0000-4000-8000-000000000011");
    expect(imported.body).toBe(archive);

    const syncPackage = {
      id: "package-1",
      version: 7,
      manifest_hash: "sha256:manifest",
    } as SyncPackage;
    await applySyncPackage(syncPackage);
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toEqual({
      expected_version: 7,
      manifest_hash: "sha256:manifest",
    });

    const node = { id: "node-1", version: 4 } as FederationNode;
    await changeFederationNodeStatus(node, "quarantine", "Integrity incident");
    expect(fetchMock.mock.calls[2]?.[0]).toBe("/api/v1/federation/nodes/node-1/quarantine");
    expect(JSON.parse(String(fetchMock.mock.calls[2]?.[1]?.body))).toEqual({
      expected_version: 4,
      rationale: "Integrity incident",
    });

    expect(await downloadSyncArchive("package-1")).toBeInstanceOf(Blob);
    expect(fetchMock.mock.calls[3]?.[0]).toBe("/api/v1/federation/sync/packages/package-1/archive");
  });
});
