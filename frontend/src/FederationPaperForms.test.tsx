import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import FederationPaperForms from "./FederationPaperForms";
import type { Principal } from "./api/admin";
import * as federation from "./api/federation";

vi.mock("./api/federation", async () => {
  const actual = await vi.importActual<typeof import("./api/federation")>("./api/federation");
  return {
    ...actual,
    issueFederationPaperForm: vi.fn(),
    recordFederationPaperForm: vi.fn(),
    voidFederationPaperForm: vi.fn()
  };
});

const node = {
  id: "node-1",
  node_code: "PEER-01",
  status: "ACTIVE"
} as federation.FederationNode;
const epoch = {
  id: "epoch-1",
  external_node_id: node.id,
  status: "OPEN"
} as federation.OfflineEpoch;
const paper = {
  id: "paper-1",
  external_node_id: node.id,
  epoch_id: epoch.id,
  serial_number: "PAPER-001",
  qr_reference: "CCPF:1:PEER-01:PAPER-001:abc",
  checksum: `sha256:${"a".repeat(64)}`,
  form_type: "GOODS_TRANSFER",
  form_version: 1,
  participant_refs: ["MEMBER-1", "MEMBER-2"],
  operation_constraints: { maximum_value: "10" },
  status: "ISSUED",
  issued_at: "2026-07-21T10:00:00Z",
  expires_at: "2027-07-21T10:00:00Z",
  payload: null,
  payload_hash: null,
  signatures: null,
  evidence_ids: null,
  issued_by_user_id: "issuer-user",
  issued_by_member_id: "issuer-member",
  recorded_by_user_id: null,
  recorded_by_member_id: null,
  recorded_at: null,
  voided_by_user_id: null,
  voided_by_member_id: null,
  voided_at: null,
  void_reason: null,
  version: 1
} as federation.FederationPaperForm;

const operator: Principal = {
  user_id: "operator",
  login: "operator",
  member_id: "member-operator",
  must_change_password: false,
  roles: [{ assignment_id: "role-operator", role: "NODE_BUSINESS_OPERATOR", cooperative_id: null }]
};
const auditor: Principal = {
  user_id: "auditor",
  login: "auditor",
  member_id: "member-auditor",
  must_change_password: false,
  roles: [{ assignment_id: "role-auditor", role: "NODE_AUDITOR", cooperative_id: null }]
};

describe("FederationPaperForms", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    const result = Promise.resolve({ event_id: "event-1", object_id: "paper-1", replayed: false });
    vi.mocked(federation.issueFederationPaperForm).mockReturnValue(result);
    vi.mocked(federation.recordFederationPaperForm).mockReturnValue(result);
    vi.mocked(federation.voidFederationPaperForm).mockReturnValue(result);
  });

  it("issues a numbered form bound to the selected epoch", async () => {
    const user = userEvent.setup();
    render(
      <FederationPaperForms
        nodes={[node]}
        epochs={[epoch]}
        forms={[]}
        principal={operator}
        run={(action) => void action()}
      />
    );

    await user.type(screen.getByLabelText("Серийный номер"), "PAPER-002");
    await user.type(screen.getByLabelText("Участники через запятую"), "MEMBER-1, MEMBER-2");
    await user.type(screen.getByLabelText("Максимум"), "25");
    await user.click(screen.getByRole("button", { name: "Выдать" }));

    await waitFor(() =>
      expect(federation.issueFederationPaperForm).toHaveBeenCalledWith(
        epoch.id,
        expect.objectContaining({
          serial_number: "PAPER-002",
          participant_refs: ["MEMBER-1", "MEMBER-2"]
        })
      )
    );
  });

  it("records or voids an issued original through the independent role", async () => {
    const user = userEvent.setup();
    render(
      <FederationPaperForms
        nodes={[node]}
        epochs={[epoch]}
        forms={[paper]}
        principal={auditor}
        run={(action) => void action()}
      />
    );

    await user.type(screen.getByLabelText("Ресурс"), "CABBAGE");
    await user.type(screen.getByLabelText("Количество"), "5");
    await user.type(screen.getByLabelText("ID скана или доказательства"), "evidence-1");
    await user.click(screen.getByRole("button", { name: "Зафиксировать" }));
    await waitFor(() =>
      expect(federation.recordFederationPaperForm).toHaveBeenCalledWith(
        paper,
        expect.objectContaining({
          evidence_ids: ["evidence-1"],
          signatures: [
            { party_ref: "MEMBER-1", kind: "WET_INK" },
            { party_ref: "MEMBER-2", kind: "WET_INK" }
          ]
        })
      )
    );

    await user.click(screen.getByTitle("Аннулировать"));
    await waitFor(() => expect(federation.voidFederationPaperForm).toHaveBeenCalledWith(
      paper,
      "Неиспользованный оригинал погашен независимым контролёром."
    ));
  });
});
