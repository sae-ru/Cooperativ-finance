import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AdminDirectoryView from "./AdminDirectoryView";
import * as admin from "./api/admin";
import * as federation from "./api/federation";
import * as system from "./api/system";

vi.mock("./api/admin", async (importOriginal) => ({
  ...await importOriginal<typeof import("./api/admin")>(),
  applyMemberImport: vi.fn(),
  checkMemberDuplicates: vi.fn(),
  createCooperative: vi.fn(),
  createMember: vi.fn(),
  createMembership: vi.fn(),
  createUser: vi.fn(),
  decideMemberImport: vi.fn(),
  getCooperatives: vi.fn(),
  getMemberImportRows: vi.fn(),
  getMemberImports: vi.fn(),
  getMembers: vi.fn(),
  getMemberships: vi.fn(),
  getUsers: vi.fn(),
  previewMemberImport: vi.fn(),
  stageMemberImport: vi.fn(),
  transitionCooperative: vi.fn(),
  transitionMember: vi.fn(),
  transitionMembership: vi.fn(),
  transitionUser: vi.fn(),
}));
vi.mock("./api/federation", async (importOriginal) => ({
  ...await importOriginal<typeof import("./api/federation")>(),
  getFederationNodes: vi.fn(),
}));
vi.mock("./api/system", async (importOriginal) => ({
  ...await importOriginal<typeof import("./api/system")>(),
  fetchSystemStatus: vi.fn(),
}));

const cooperativeId = "30000000-0000-0000-0000-000000000001";
const memberId = "40000000-0000-0000-0000-000000000001";
const principal: admin.Principal = {
  user_id: "10000000-0000-0000-0000-000000000001",
  login: "security",
  member_id: null,
  must_change_password: false,
  roles: [
    { assignment_id: "role-security", role: "SECURITY_ADMIN", cooperative_id: null },
    { assignment_id: "role-registrar", role: "MEMBER_REGISTRAR", cooperative_id: cooperativeId },
    { assignment_id: "role-steward", role: "DATA_STEWARD", cooperative_id: cooperativeId },
    { assignment_id: "role-node", role: "NODE_REGISTRAR", cooperative_id: null },
  ],
};

function importBatch(
  status: admin.MemberImportBatch["status"],
  overrides: Partial<admin.MemberImportBatch> = {},
): admin.MemberImportBatch {
  return {
    id: "80000000-0000-0000-0000-000000000001",
    cooperative_id: cooperativeId,
    source_name: "members.csv",
    source_sha256: "a".repeat(64),
    status,
    row_count: 2,
    ready_count: 1,
    invalid_count: 0,
    duplicate_count: 1,
    applied_count: 0,
    created_by_user_id: "10000000-0000-0000-0000-000000000099",
    reviewed_by_user_id: null,
    decision_reason_code: null,
    created_at: "2026-07-27T08:00:00Z",
    previewed_at: "2026-07-27T08:01:00Z",
    reviewed_at: null,
    applied_at: null,
    updated_at: "2026-07-27T08:01:00Z",
    version: 2,
    ...overrides,
  };
}

function renderView(onManageNodes = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><AdminDirectoryView principal={principal} onManageNodes={onManageNodes} /></QueryClientProvider>);
  return onManageNodes;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(admin.getCooperatives).mockResolvedValue([{
    id: cooperativeId,
    code: "demo-coop",
    name: "Демо кооператив",
    status: "ACTIVE",
    created_at: "2026-07-27T08:00:00Z",
    updated_at: "2026-07-27T08:00:00Z",
    version: 1,
  }]);
  vi.mocked(admin.getMembers).mockResolvedValue([{
    id: memberId,
    display_name: "Анна Петрова",
    registered_by_cooperative_id: cooperativeId,
    status: "ACTIVE",
    created_at: "2026-07-27T08:00:00Z",
    updated_at: "2026-07-27T08:00:00Z",
    version: 2,
  }]);
  vi.mocked(admin.checkMemberDuplicates).mockResolvedValue({
    candidates: [],
    exact_identifier_match: false,
    normalized_name_match: false,
  });
  vi.mocked(admin.getMemberImports).mockResolvedValue([]);
  vi.mocked(admin.getMemberImportRows).mockResolvedValue([]);
  vi.mocked(admin.getMemberships).mockResolvedValue([{
    id: "50000000-0000-0000-0000-000000000001",
    cooperative_id: cooperativeId,
    member_id: memberId,
    member_number: "D-100",
    status: "ACTIVE",
    joined_at: "2026-07-27T08:00:00Z",
    ended_at: null,
    created_at: "2026-07-27T08:00:00Z",
    updated_at: "2026-07-27T08:00:00Z",
    version: 1,
  }]);
  vi.mocked(admin.getUsers).mockResolvedValue([{
    id: "10000000-0000-0000-0000-000000000002",
    login: "farmer",
    member_id: memberId,
    status: "ACTIVE",
    must_change_password: false,
    locked_until: null,
    last_login_at: "2026-07-27T09:00:00Z",
    created_at: "2026-07-27T08:00:00Z",
    version: 3,
  }]);
  vi.mocked(system.fetchSystemStatus).mockResolvedValue({
    status: "OPERATIONAL",
    node: {
      id: "20000000-0000-0000-0000-000000000001",
      code: "node-local-01",
      display_name: "Локальный узел",
      environment: "pilot",
      demo_data_loaded: false,
    },
    release: { version: "0.1.0", schema_revision: "0030_safe_member_intake" },
    checks: [],
    worker: { status: "RUNNING", last_seen_at: "2026-07-27T10:00:00Z" },
    notices: [],
  });
  vi.mocked(federation.getFederationNodes).mockResolvedValue([{
    id: "60000000-0000-0000-0000-000000000001",
    node_code: "node-remote-01",
    display_name: "Соседний узел",
    owner_organization_id: "70000000-0000-0000-0000-000000000001",
    territory: "Северный район",
    purpose: "EXCHANGE",
    status: "ACTIVE",
    trust_level: "STANDARD",
    capabilities: ["TEST_EXCHANGE"],
    supported_protocols: ["1.0"],
    supported_policies: { federation: 1 },
    last_sync_at: null,
    last_checkpoint_hash: null,
    created_at: "2026-07-27T08:00:00Z",
    updated_at: "2026-07-27T08:00:00Z",
    version: 1,
  }]);
  for (const command of [
    admin.createCooperative,
    admin.createMember,
    admin.createMembership,
    admin.createUser,
    admin.applyMemberImport,
    admin.decideMemberImport,
    admin.previewMemberImport,
    admin.stageMemberImport,
    admin.transitionCooperative,
    admin.transitionMember,
    admin.transitionMembership,
    admin.transitionUser,
  ]) {
    vi.mocked(command).mockResolvedValue({ event_id: "event-id", object_id: "object-id" });
  }
});

describe("AdminDirectoryView", () => {
  it("keeps organizations, members, memberships, accounts, and nodes separate", async () => {
    const user = userEvent.setup();
    const manageNodes = renderView();

    expect(await screen.findByRole("heading", { name: "Участники" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Узлы" })).toHaveAttribute("title", "Узлы");
    expect(screen.getByText("Анна Петрова")).toBeInTheDocument();
    expect(screen.getAllByText("Демо кооператив").length).toBeGreaterThan(0);

    await user.click(screen.getByRole("tab", { name: "Организации" }));
    await user.type(screen.getByLabelText("Код организации"), "north-coop");
    await user.type(screen.getByLabelText("Название организации"), "Северный кооператив");
    await user.click(screen.getByRole("button", { name: "Создать организацию" }));
    await waitFor(() => expect(admin.createCooperative).toHaveBeenCalledWith({ code: "north-coop", name: "Северный кооператив" }));

    await user.click(screen.getByRole("tab", { name: "Членства" }));
    expect(await screen.findByText("D-100")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Новый статус членства D-100"), "SUSPENDED");
    await waitFor(() => expect(admin.transitionMembership).toHaveBeenCalledWith(expect.objectContaining({ id: "50000000-0000-0000-0000-000000000001" }), "SUSPENDED"));

    await user.click(screen.getByRole("tab", { name: "Учетные записи" }));
    expect(await screen.findByText("farmer")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Отключить вход" }));
    await waitFor(() => expect(admin.transitionUser).toHaveBeenCalledWith(expect.objectContaining({ login: "farmer" }), "DISABLED"));

    await user.click(screen.getByRole("tab", { name: "Узлы" }));
    expect(await screen.findByText("Локальный узел")).toBeInTheDocument();
    expect(screen.getByText("Работает")).toBeInTheDocument();
    expect(screen.getByText("Пилот")).toBeInTheDocument();
    expect(screen.getByText("Соседний узел")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Управление узлами" }));
    expect(manageNodes).toHaveBeenCalledOnce();
  });
  it("requires an explicit decision before creating a same-name member", async () => {
    vi.mocked(admin.checkMemberDuplicates).mockResolvedValue({
      candidates: [{
        member_id: memberId,
        display_name: "Anna Petrova",
        registered_by_cooperative_id: cooperativeId,
        status: "ACTIVE",
        match_basis: "NORMALIZED_NAME",
      }],
      exact_identifier_match: false,
      normalized_name_match: true,
    });
    const user = userEvent.setup();
    renderView();

    await screen.findByRole("heading", { level: 2 });
    const form = document.querySelector(".registry-command form");
    expect(form).not.toBeNull();
    const fields = within(form as HTMLFormElement).getAllByRole("textbox");
    await user.type(fields[0]!, "Anna Petrova");
    await user.type(fields[1]!, "anna-new");
    await user.click(within(form as HTMLFormElement).getByRole("button"));

    await screen.findByRole("alert");
    expect(admin.createMember).not.toHaveBeenCalled();
    await user.click(screen.getByRole("checkbox"));
    await user.click(within(form as HTMLFormElement).getByRole("button"));

    await waitFor(() => expect(admin.createMember).toHaveBeenCalledWith({
      cooperative_id: cooperativeId,
      display_name: "Anna Petrova",
      identifier_type: "EXTERNAL_REFERENCE",
      identifier_value: "anna-new",
      duplicate_resolution_code: "DISTINCT_PERSON_CONFIRMED",
    }));
  });

  it("shows an import report and sends an independent approval", async () => {
    const batch: admin.MemberImportBatch = {
      id: "80000000-0000-0000-0000-000000000001",
      cooperative_id: cooperativeId,
      source_name: "members.csv",
      source_sha256: "a".repeat(64),
      status: "PREVIEWED",
      row_count: 2,
      ready_count: 1,
      invalid_count: 0,
      duplicate_count: 1,
      applied_count: 0,
      created_by_user_id: "10000000-0000-0000-0000-000000000099",
      reviewed_by_user_id: null,
      decision_reason_code: null,
      created_at: "2026-07-27T08:00:00Z",
      previewed_at: "2026-07-27T08:01:00Z",
      reviewed_at: null,
      applied_at: null,
      updated_at: "2026-07-27T08:01:00Z",
      version: 2,
    };
    vi.mocked(admin.getMemberImports).mockResolvedValue([batch]);
    vi.mocked(admin.getMemberImportRows).mockResolvedValue([{
      id: "81000000-0000-0000-0000-000000000001",
      batch_id: batch.id,
      row_number: 1,
      display_name: "Ready Person",
      identifier_type: "EXTERNAL_REFERENCE",
      status: "READY",
      error_code: null,
      match_basis: null,
      candidate_member_id: null,
      created_member_id: null,
      created_at: "2026-07-27T08:00:00Z",
      applied_at: null,
    }]);
    const user = userEvent.setup();
    renderView();

    const tabs = await screen.findAllByRole("tab");
    await user.click(tabs[3]!);
    expect((await screen.findAllByText("members.csv")).length).toBe(2);
    expect(await screen.findByText("Ready Person")).toBeInTheDocument();
    const actions = document.querySelector(".import-actions");
    expect(actions).not.toBeNull();
    const buttons = within(actions as HTMLDivElement).getAllByRole("button");
    await user.click(buttons[1]!);

    await waitFor(() => expect(admin.decideMemberImport).toHaveBeenCalledWith(
      batch,
      true,
      "INDEPENDENT_REVIEW",
    ));
  });
  it("blocks an exact identifier duplicate without offering an override", async () => {
    vi.mocked(admin.checkMemberDuplicates).mockResolvedValue({
      candidates: [{
        member_id: memberId,
        display_name: "Anna Petrova",
        registered_by_cooperative_id: cooperativeId,
        status: "ACTIVE",
        match_basis: "EXACT_IDENTIFIER",
      }],
      exact_identifier_match: true,
      normalized_name_match: false,
    });
    const user = userEvent.setup();
    renderView();

    await screen.findByRole("heading", { level: 2 });
    const form = document.querySelector(".registry-command form");
    expect(form).not.toBeNull();
    const fields = within(form as HTMLFormElement).getAllByRole("textbox");
    await user.type(fields[0]!, "Different name");
    await user.type(fields[1]!, "existing-id");
    await user.click(within(form as HTMLFormElement).getByRole("button"));

    await screen.findByRole("alert");
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(admin.createMember).not.toHaveBeenCalled();
  });

  it("stages a CSV, downloads the template, and runs a dry run", async () => {
    const batch = importBatch("STAGED", {
      ready_count: 0,
      duplicate_count: 0,
      previewed_at: null,
      version: 1,
    });
    vi.mocked(admin.getMemberImports).mockResolvedValue([batch]);
    vi.mocked(admin.getMemberImportRows).mockResolvedValue([]);
    const linkClick = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const createObjectURL = vi.fn(() => "blob:member-template");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });
    const user = userEvent.setup();
    renderView();

    const tabs = await screen.findAllByRole("tab");
    await user.click(tabs[3]!);
    const command = document.querySelector(".import-command form");
    expect(command).not.toBeNull();
    const input = command!.querySelector('input[type="file"]') as HTMLInputElement;
    await user.selectOptions(within(command as HTMLFormElement).getByRole("combobox"), cooperativeId);
    await user.upload(input, new File(["x".repeat(1_000_001)], "too-large.csv", { type: "text/csv" }));
    expect(screen.getByRole("alert")).toBeInTheDocument();
    const validFile = new File(["display_name\nFresh member\n"], "fresh.csv", { type: "text/csv" });
    Object.defineProperty(validFile, "text", {
      value: vi.fn().mockResolvedValue("display_name\nFresh member\n"),
    });
    await user.upload(input, validFile);
    fireEvent.submit(command as HTMLFormElement);
    await waitFor(() => expect(admin.stageMemberImport).toHaveBeenCalledWith({
      cooperative_id: cooperativeId,
      source_name: "fresh.csv",
      csv_text: "display_name\nFresh member\n",
    }));
    await user.click(within(command as HTMLFormElement).getAllByRole("button")[0]!);
    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(linkClick).toHaveBeenCalledOnce();

    const actions = document.querySelector(".import-actions");
    expect(actions).not.toBeNull();
    await user.click(within(actions as HTMLDivElement).getByRole("button"));
    await waitFor(() => expect(admin.previewMemberImport).toHaveBeenCalledWith(batch));
  });

  it("applies an independently approved import", async () => {
    const batch = importBatch("APPROVED", {
      reviewed_by_user_id: principal.user_id,
      decision_reason_code: "INDEPENDENT_REVIEW",
      reviewed_at: "2026-07-27T08:02:00Z",
      version: 3,
    });
    vi.mocked(admin.getMemberImports).mockResolvedValue([batch]);
    vi.mocked(admin.getMemberImportRows).mockResolvedValue([]);
    const user = userEvent.setup();
    renderView();

    const tabs = await screen.findAllByRole("tab");
    await user.click(tabs[3]!);
    const actions = document.querySelector(".import-actions");
    expect(actions).not.toBeNull();
    await user.click(within(actions as HTMLDivElement).getByRole("button"));
    await waitFor(() => expect(admin.applyMemberImport).toHaveBeenCalledWith(batch));
  });
});
