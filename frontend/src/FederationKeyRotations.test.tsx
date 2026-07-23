import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import FederationKeyRotations from "./FederationKeyRotations";
import type { Principal } from "./api/admin";
import * as federation from "./api/federation";

vi.mock("./api/federation", async () => {
  const actual = await vi.importActual<typeof import("./api/federation")>("./api/federation");
  return {
    ...actual,
    requestNodeKeyRotation: vi.fn(),
    decideNodeKeyRotation: vi.fn()
  };
});

const node = {
  id: "node-1",
  node_code: "PEER-01",
  status: "ACTIVE"
} as federation.FederationNode;
const rotation = {
  id: "rotation-1",
  node_id: node.id,
  old_certificate_id: "certificate-old",
  new_certificate_id: "certificate-new",
  reason: "SCHEDULED",
  status: "PENDING",
  requested_by_user_id: "security-user",
  decided_by_user_id: null,
  continuity_verified: true,
  created_at: "2026-07-21T10:00:00Z",
  decided_at: null,
  version: 1
} as federation.NodeKeyRotation;

const security: Principal = {
  user_id: "security",
  login: "security",
  member_id: "member-security",
  must_change_password: false,
  roles: [{ assignment_id: "role-security", role: "NODE_SECURITY_ADMIN", cooperative_id: null }]
};
const auditor: Principal = {
  user_id: "auditor",
  login: "auditor",
  member_id: "member-auditor",
  must_change_password: false,
  roles: [{ assignment_id: "role-auditor", role: "NODE_AUDITOR", cooperative_id: null }]
};

describe("FederationKeyRotations", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    const result = Promise.resolve({
      event_id: "event-1",
      object_id: rotation.id,
      replayed: false
    });
    vi.mocked(federation.requestNodeKeyRotation).mockReturnValue(result);
    vi.mocked(federation.decideNodeKeyRotation).mockReturnValue(result);
  });

  it("submits key continuity proofs", async () => {
    const user = userEvent.setup();
    render(
      <FederationKeyRotations
        nodes={[node]}
        rotations={[]}
        principal={security}
        run={(action) => void action()}
      />
    );

    await user.type(screen.getByLabelText("Новый публичный ключ Ed25519, base64"), "public-key");
    await user.type(screen.getByLabelText("Подпись старым ключом, base64"), "old-signature");
    await user.type(screen.getByLabelText("Подпись новым ключом, base64"), "new-signature");
    await user.click(screen.getByRole("button", { name: "Запросить" }));

    await waitFor(() =>
      expect(federation.requestNodeKeyRotation).toHaveBeenCalledWith(
        node.id,
        expect.objectContaining({
          reason: "SCHEDULED",
          old_signature_base64: "old-signature",
          new_signature_base64: "new-signature"
        })
      )
    );
  });

  it("keeps the rotation decision in an independent queue", async () => {
    const user = userEvent.setup();
    render(
      <FederationKeyRotations
        nodes={[node]}
        rotations={[rotation]}
        principal={auditor}
        run={(action) => void action()}
      />
    );

    await user.click(screen.getByTitle("Одобрить ротацию"));
    await waitFor(() =>
      expect(federation.decideNodeKeyRotation).toHaveBeenCalledWith(rotation, true)
    );
  });
});
