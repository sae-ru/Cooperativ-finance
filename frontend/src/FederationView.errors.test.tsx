import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import FederationView from "./FederationView";
import { AdminApiError, type Principal } from "./api/admin";
import * as admin from "./api/admin";
import * as federation from "./api/federation";

vi.mock("./api/admin", async () => {
  const actual = await vi.importActual<typeof import("./api/admin")>("./api/admin");
  return { ...actual, getUsers: vi.fn(), getRoles: vi.fn() };
});

vi.mock("./api/federation", async () => {
  const actual = await vi.importActual<typeof import("./api/federation")>("./api/federation");
  return Object.fromEntries(
    Object.entries(actual).map(([key, value]) => [key, typeof value === "function" ? vi.fn() : value]),
  );
});

const principal: Principal = {
  user_id: "auditor-1",
  login: "node-auditor",
  member_id: null,
  must_change_password: false,
  roles: [{ assignment_id: "assignment-1", role: "NODE_AUDITOR", cooperative_id: null }],
};

function renderView() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <FederationView principal={principal} />
    </QueryClientProvider>,
  );
}

describe("FederationView failures", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(federation.getFederationNodes).mockResolvedValue([]);
    vi.mocked(federation.getNodeApplications).mockResolvedValue([]);
    vi.mocked(federation.getNodeResponsibilities).mockResolvedValue([]);
    vi.mocked(federation.getNodeChallenges).mockResolvedValue([]);
    vi.mocked(federation.getNodeContracts).mockResolvedValue([]);
    vi.mocked(federation.getNodeLimits).mockResolvedValue([]);
    vi.mocked(federation.getNodeBonds).mockResolvedValue([]);
    vi.mocked(federation.getNodeExposures).mockResolvedValue([]);
    vi.mocked(federation.getOfflineEpochs).mockResolvedValue([]);
    vi.mocked(federation.getSyncPackages).mockResolvedValue([]);
    vi.mocked(federation.getSyncConflicts).mockResolvedValue([]);
    vi.mocked(federation.getSyncReceipts).mockResolvedValue([]);
    vi.mocked(federation.getNodeIncidents).mockResolvedValue([]);
    vi.mocked(federation.getFederationPaperForms).mockResolvedValue([]);
    vi.mocked(federation.getNodeKeyRotations).mockResolvedValue([]);
    vi.mocked(admin.getUsers).mockResolvedValue([]);
    vi.mocked(admin.getRoles).mockResolvedValue([]);
  });

  it("shows a stable API error code and request identifier", async () => {
    vi.mocked(federation.getFederationNodes).mockRejectedValue(
      new AdminApiError("FEDERATION_UNAVAILABLE", "request-11", 503),
    );

    renderView();

    expect(await screen.findByRole("alert")).toHaveTextContent("FEDERATION_UNAVAILABLE · request-11");
  });

  it("does not expose internals for an unstructured transport failure", async () => {
    vi.mocked(federation.getNodeContracts).mockRejectedValue(new Error("socket details"));

    renderView();

    expect(await screen.findByRole("alert")).toHaveTextContent("Операция не выполнена");
    expect(screen.getByRole("alert")).not.toHaveTextContent("socket details");
  });
});
