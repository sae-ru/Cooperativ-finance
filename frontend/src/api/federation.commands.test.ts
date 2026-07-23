import { afterEach, describe, expect, it, vi } from "vitest";

import {
  acceptNodeResponsibility,
  activateFederationNode,
  approveNodeContract,
  approveNodeLimit,
  closeOfflineEpoch,
  createNodeApplication,
  decideNodeAudit,
  decideNodeKeyRotation,
  exportSyncPackage,
  issueFederationPaperForm,
  issueNodeChallenge,
  openNodeIncident,
  openOfflineEpoch,
  proposeNodeContract,
  proposeNodeLimit,
  recordFederationPaperForm,
  recordNodeChallengeResponse,
  registerNodeBond,
  requestNodeKeyRotation,
  reserveNodeExposure,
  resolveNodeIncident,
  resolveSyncConflict,
  submitNodeApplication,
  verifyNodeIdentity,
  voidFederationPaperForm,
  type FederationNode,
  type FederationPaperForm,
  type NodeApplication,
  type NodeIncident,
  type NodeKeyRotation,
  type NodeLimit,
  type NodeResponsibility,
  type NodeTrustContract,
  type OfflineEpoch,
  type SyncConflict,
} from "./federation";

function envelope(data: unknown): Response {
  return new Response(JSON.stringify({ data }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("federation command API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sends every lifecycle command with an idempotency key", async () => {
    const result = { event_id: "event-1", object_id: "object-1", replayed: false };
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(envelope(result)));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("crypto", { randomUUID: () => "00000000-0000-4000-8000-000000000012" });

    const application = { id: "application-1", version: 3 } as NodeApplication;
    const responsibility = { id: "responsibility-1", application_id: application.id } as NodeResponsibility;
    const contract = { id: "contract-1", version: 2, terms_hash: "sha256:contract" } as NodeTrustContract;
    const limit = { id: "limit-1", version: 4, terms_hash: "sha256:limit" } as NodeLimit;
    const node = { id: "node-1", version: 5 } as FederationNode;
    const incident = { id: "incident-1", version: 2 } as NodeIncident;
    const epoch = { id: "epoch-1", version: 6 } as OfflineEpoch;
    const conflict = { id: "conflict-1", version: 7 } as SyncConflict;
    const paper = {
      id: "paper-1",
      epoch_id: epoch.id,
      version: 2,
      checksum: "sha256:paper"
    } as FederationPaperForm;
    const rotation = { id: "rotation-1", version: 3 } as NodeKeyRotation;

    await createNodeApplication({ node_code: "peer-01" });
    await acceptNodeResponsibility(responsibility);
    await submitNodeApplication(application);
    await verifyNodeIdentity(application, "verified");
    await issueNodeChallenge(application);
    await recordNodeChallengeResponse("challenge-1", "nonce", "signature", { integrity: "PASS" });
    await decideNodeAudit(application, true, "approved");
    await proposeNodeContract({ application_id: application.id });
    await approveNodeContract(contract);
    await proposeNodeLimit(node.id, { capability: "TEST_EXCHANGE" });
    await approveNodeLimit(limit);
    await registerNodeBond(node.id, { amount: "100" });
    await activateFederationNode(node);
    await openNodeIncident(node.id, { incident_type: "INTEGRITY_FAILURE" });
    await resolveNodeIncident(incident, "resolved");
    await openOfflineEpoch(node.id, { protocol_version: "1.0" });
    await closeOfflineEpoch(epoch);
    await issueFederationPaperForm(epoch.id, { serial_number: "PAPER-1" });
    await recordFederationPaperForm(paper, {
      operation_payload: { quantity: "1" },
      signatures: [{ kind: "WET_INK" }],
      evidence_ids: ["evidence-1"]
    });
    await voidFederationPaperForm(paper, "unused");
    await requestNodeKeyRotation(node.id, { reason: "SCHEDULED" });
    await decideNodeKeyRotation(rotation, true);
    await reserveNodeExposure(node.id, { delta: "5" });
    await exportSyncPackage({ peer_node_id: node.id });
    await resolveSyncConflict(conflict, "KEEP_LOCAL", "local proof prevails");

    expect(fetchMock).toHaveBeenCalledTimes(25);
    for (const call of fetchMock.mock.calls) {
      const init = call[1] as RequestInit;
      expect(init.method).toBe("POST");
      expect((init.headers as Headers).get("Idempotency-Key")).toBe("00000000-0000-4000-8000-000000000012");
    }
    expect(fetchMock.mock.calls.map((call) => call[0])).toContain("/api/v1/federation/offline-epochs/epoch-1/close");
    expect(fetchMock.mock.calls.map((call) => call[0])).toContain("/api/v1/federation/paper-forms/paper-1/record");
    expect(fetchMock.mock.calls.map((call) => call[0])).toContain("/api/v1/federation/key-rotations/rotation-1/decision");
    expect(fetchMock.mock.calls.map((call) => call[0])).toContain("/api/v1/federation/sync/conflicts/conflict-1/resolution");
  });
});
