import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  BadgeCheck,
  Building2,
  CircleOff,
  Download,
  FileCheck2,
  FileSearch,
  FileUp,
  Link2,
  Network,
  Plus,
  RefreshCw,
  Upload,
  UserCog,
  Users,
  XCircle,
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  type Cooperative,
  type Member,
  type MemberDuplicateCheck,
  type MemberImportBatch,
  type MemberImportRow,
  type Membership,
  type Principal,
  type RoleCode,
  type UserAccount,
  applyMemberImport,
  checkMemberDuplicates,
  createCooperative,
  createMember,
  createMembership,
  decideMemberImport,
  createUser,
  getCooperatives,
  getMemberImportRows,
  getMemberImports,
  getMembers,
  getMemberships,
  getUsers,
  previewMemberImport,
  stageMemberImport,
  transitionCooperative,
  transitionMember,
  transitionMembership,
  transitionUser,
} from "./api/admin";
import { getFederationNodes } from "./api/federation";
import { fetchSystemStatus } from "./api/system";
import { userErrorMessage } from "./shared/api-error";
import { formatLocalDateTime } from "./shared/date-time";

const memberTransitions: Record<string, string[]> = {
  APPLICANT: ["PENDING_VERIFICATION", "REJECTED"],
  PENDING_VERIFICATION: ["LIMITED", "ACTIVE", "REJECTED"],
  LIMITED: ["ACTIVE", "SUSPENDED", "EXITED"],
  ACTIVE: ["SUSPENDED", "EXITED"],
  SUSPENDED: ["ACTIVE", "EXITED"],
};

const membershipTransitions: Record<string, string[]> = {
  PENDING: ["ACTIVE", "SUSPENDED", "ENDED"],
  ACTIVE: ["SUSPENDED", "ENDED"],
  SUSPENDED: ["ACTIVE", "ENDED"],
};

const statusNames: Record<string, string> = {
  APPLICANT: "Заявитель",
  PENDING_VERIFICATION: "Проверка",
  LIMITED: "Ограничен",
  ACTIVE: "Активен",
  SUSPENDED: "Приостановлен",
  REJECTED: "Отклонен",
  EXITED: "Выбыл",
  PENDING: "Ожидает",
  ENDED: "Завершено",
  DISABLED: "Отключена",
  OPERATIONAL: "Работает",
  DEGRADED: "Ограниченно работает",
};

const environmentNames: Record<string, string> = {
  dev: "Разработка",
  test: "Тест",
  pilot: "Пилот",
  production: "Рабочий контур",
  prod: "Рабочий контур",
};

type Section = "organizations" | "members" | "memberships" | "imports" | "accounts" | "nodes";
type RunAction = () => Promise<unknown>;

function hasRole(principal: Principal, ...roles: RoleCode[]): boolean {
  return principal.roles.some((grant) => roles.includes(grant.role));
}

function Status({ value }: { value: string }) {
  return <span className={`status status-${value.toLowerCase()}`}>{statusNames[value] ?? value}</span>;
}

function ErrorLine({ error }: { error: unknown }) {
  return <p className="form-error" role="alert">{userErrorMessage(error)}</p>;
}

function Empty({ text }: { text: string }) {
  return <div className="empty-state">{text}</div>;
}

function OrganizationsSection({
  data,
  canManage,
  busy,
  run,
}: {
  data: Cooperative[];
  canManage: boolean;
  busy: boolean;
  run: (action: RunAction) => void;
}) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    run(async () => {
      await createCooperative({ code, name });
      setCode("");
      setName("");
    });
  }

  return <>
    {canManage ? <section className="action-band registry-command"><form onSubmit={submit}>
      <label>Код организации<input value={code} onChange={(event) => setCode(event.target.value)} pattern="[a-z0-9][a-z0-9-]{1,61}[a-z0-9]" required /></label>
      <label>Название организации<input value={name} onChange={(event) => setName(event.target.value)} required minLength={2} /></label>
      <button className="primary-button" type="submit" disabled={busy}><Plus size={17} /><span>Создать организацию</span></button>
    </form></section> : null}
    <section className="panel"><div className="panel-heading"><h2>Организации</h2><span>{data.length}</span></div>
      <div className="table-wrap"><table><thead><tr><th>Название</th><th>Код</th><th>Статус</th><th>Версия</th><th>Действие</th></tr></thead><tbody>{data.map((item) => <tr key={item.id}>
        <td><strong>{item.name}</strong><small>{formatLocalDateTime(item.created_at)}</small></td><td><code>{item.code}</code></td><td><Status value={item.status} /></td><td>{item.version}</td>
        <td>{canManage ? <button className="compact-command" disabled={busy} onClick={() => run(() => transitionCooperative(item, item.status === "ACTIVE" ? "SUSPENDED" : "ACTIVE"))}>{item.status === "ACTIVE" ? <CircleOff size={15} /> : <BadgeCheck size={15} />}{item.status === "ACTIVE" ? "Приостановить" : "Возобновить"}</button> : "—"}</td>
      </tr>)}</tbody></table></div>{!data.length ? <Empty text="Организации не заведены" /> : null}</section>
  </>;
}

function MembersSection({
  data,
  cooperatives,
  principal,
  busy,
  run,
}: {
  data: Member[];
  cooperatives: Cooperative[];
  principal: Principal;
  busy: boolean;
  run: (action: RunAction) => void;
}) {
  const { t } = useTranslation();
  const canCreate = hasRole(principal, "MEMBER_REGISTRAR");
  const canTransition = hasRole(principal, "MEMBER_REGISTRAR", "RISK_ADMIN");
  const allowedCooperatives = cooperatives.filter((cooperative) => cooperative.status === "ACTIVE" && principal.roles.some((grant) => grant.cooperative_id === null || grant.cooperative_id === cooperative.id));
  const [cooperativeId, setCooperativeId] = useState(allowedCooperatives[0]?.id ?? "");
  const [displayName, setDisplayName] = useState("");
  const [identifier, setIdentifier] = useState("");
  const [duplicateReview, setDuplicateReview] = useState<MemberDuplicateCheck | null>(null);
  const [distinctConfirmed, setDistinctConfirmed] = useState(false);
  const [search, setSearch] = useState("");
  const visible = useMemo(() => data.filter((item) => item.display_name.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())), [data, search]);
  const defaultCooperativeId = allowedCooperatives[0]?.id ?? "";

  useEffect(() => {
    if (!cooperativeId && defaultCooperativeId) {
      setCooperativeId(defaultCooperativeId);
    }
  }, [cooperativeId, defaultCooperativeId]);

  function resetDuplicateReview() {
    setDuplicateReview(null);
    setDistinctConfirmed(false);
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    run(async () => {
      const payload = {
        cooperative_id: cooperativeId,
        display_name: displayName,
        ...(identifier ? { identifier_type: "EXTERNAL_REFERENCE", identifier_value: identifier } : {}),
      };
      const review = await checkMemberDuplicates(payload);
      setDuplicateReview(review);
      if (review.exact_identifier_match) return;
      if (review.normalized_name_match && !distinctConfirmed) return;
      await createMember({
        ...payload,
        ...(review.normalized_name_match
          ? { duplicate_resolution_code: "DISTINCT_PERSON_CONFIRMED" }
          : {}),
      });
      setDisplayName("");
      setIdentifier("");
      resetDuplicateReview();
    });
  }

  return <>
    {canCreate ? <section className="action-band registry-command"><form onSubmit={submit}>
      <label>Организация<select value={cooperativeId} onChange={(event) => { setCooperativeId(event.target.value); resetDuplicateReview(); }} required><option value="">Выберите</option>{allowedCooperatives.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label>
      <label>Имя участника<input value={displayName} onChange={(event) => { setDisplayName(event.target.value); resetDuplicateReview(); }} required /></label>
      <label>Внешний идентификатор<input value={identifier} onChange={(event) => { setIdentifier(event.target.value); resetDuplicateReview(); }} /></label>
      <button className="primary-button" type="submit" disabled={busy || !cooperativeId}><FileSearch size={17} /><span>{duplicateReview?.normalized_name_match && distinctConfirmed ? t("admin.intake.addDistinctMember") : t("admin.intake.checkAndAdd")}</span></button>
    </form>
      {duplicateReview?.candidates.length ? <div className={`duplicate-review ${duplicateReview.exact_identifier_match ? "blocking" : "warning"}`} role="alert">
        <AlertTriangle size={20} />
        <div><strong>{duplicateReview.exact_identifier_match ? t("admin.intake.identifierAlreadyUsed") : t("admin.intake.similarMembersFound")}</strong>
          <div className="duplicate-candidates">{duplicateReview.candidates.map((candidate) => <span key={`${candidate.member_id}:${candidate.match_basis}`}><b data-i18n-ignore="true">{candidate.display_name}</b><small>{candidate.match_basis === "EXACT_IDENTIFIER" ? t("admin.intake.exactIdentifier") : t("admin.intake.sameName")}</small></span>)}</div>
          {!duplicateReview.exact_identifier_match ? <label className="duplicate-confirm"><input type="checkbox" checked={distinctConfirmed} onChange={(event) => setDistinctConfirmed(event.target.checked)} />{t("admin.intake.confirmDistinctPerson")}</label> : null}
        </div>
      </div> : duplicateReview ? <div className="duplicate-review clear" role="status"><FileCheck2 size={20} /><strong>{t("admin.intake.noDuplicates")}</strong></div> : null}
    </section> : null}
    <section className="panel"><div className="panel-heading access-heading"><h2>Участники</h2><label>Поиск по имени<input value={search} onChange={(event) => setSearch(event.target.value)} /></label><span>{visible.length} из {data.length}</span></div>
      <div className="table-wrap"><table><thead><tr><th>Имя</th><th>Организация регистрации</th><th>Статус</th><th>Создан</th><th>Действие</th></tr></thead><tbody>{visible.map((item) => <tr key={item.id}>
        <td><strong data-i18n-ignore="true">{item.display_name}</strong><small>v{item.version}</small></td><td>{cooperatives.find((cooperative) => cooperative.id === item.registered_by_cooperative_id)?.name ?? "Наследованная запись"}</td><td><Status value={item.status} /></td><td>{formatLocalDateTime(item.created_at)}</td>
        <td>{canTransition && (memberTransitions[item.status]?.length ?? 0) > 0 ? <select aria-label={`Новый статус ${item.display_name}`} defaultValue="" disabled={busy} onChange={(event) => { const target = event.target.value; event.target.value = ""; if (target) run(() => transitionMember(item, target)); }}><option value="">Изменить</option>{memberTransitions[item.status]?.map((target) => <option value={target} key={target}>{statusNames[target] ?? target}</option>)}</select> : "—"}</td>
      </tr>)}</tbody></table></div>{!visible.length ? <Empty text="Участники не найдены" /> : null}</section>
  </>;
}
function MembershipsSection({
  data,
  members,
  cooperatives,
  principal,
  busy,
  run,
}: {
  data: Membership[];
  members: Member[];
  cooperatives: Cooperative[];
  principal: Principal;
  busy: boolean;
  run: (action: RunAction) => void;
}) {
  const canManage = hasRole(principal, "MEMBER_REGISTRAR", "COOPERATIVE_ADMIN");
  const [cooperativeId, setCooperativeId] = useState(cooperatives.find((item) => item.status === "ACTIVE")?.id ?? "");
  const [memberId, setMemberId] = useState("");
  const [number, setNumber] = useState("");
  const defaultCooperativeId = cooperatives.find((item) => item.status === "ACTIVE")?.id ?? "";

  useEffect(() => {
    if (!cooperativeId && defaultCooperativeId) {
      setCooperativeId(defaultCooperativeId);
    }
  }, [cooperativeId, defaultCooperativeId]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    run(async () => {
      await createMembership({ cooperative_id: cooperativeId, member_id: memberId, member_number: number });
      setMemberId("");
      setNumber("");
    });
  }

  return <>
    {canManage ? <section className="action-band registry-command"><form onSubmit={submit}>
      <label>Организация<select value={cooperativeId} onChange={(event) => setCooperativeId(event.target.value)} required><option value="">Выберите</option>{cooperatives.filter((item) => item.status === "ACTIVE").map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label>
      <label>Участник<select value={memberId} onChange={(event) => setMemberId(event.target.value)} required><option value="">Выберите</option>{members.map((item) => <option value={item.id} key={item.id}>{item.display_name}</option>)}</select></label>
      <label>Номер членства<input value={number} onChange={(event) => setNumber(event.target.value)} required /></label>
      <button className="primary-button" type="submit" disabled={busy || !cooperativeId || !memberId}><Link2 size={17} /><span>Оформить членство</span></button>
    </form></section> : null}
    <section className="panel"><div className="panel-heading"><h2>Членства</h2><span>{data.length}</span></div>
      <div className="table-wrap"><table><thead><tr><th>Номер</th><th>Участник</th><th>Организация</th><th>Статус</th><th>Действие</th></tr></thead><tbody>{data.map((item) => <tr key={item.id}>
        <td><strong>{item.member_number}</strong><small>v{item.version}</small></td><td>{members.find((member) => member.id === item.member_id)?.display_name ?? item.member_id}</td><td>{cooperatives.find((cooperative) => cooperative.id === item.cooperative_id)?.name ?? item.cooperative_id}</td><td><Status value={item.status} /></td>
        <td>{canManage && (membershipTransitions[item.status]?.length ?? 0) > 0 ? <select aria-label={`Новый статус членства ${item.member_number}`} defaultValue="" disabled={busy} onChange={(event) => { const target = event.target.value; event.target.value = ""; if (target) run(() => transitionMembership(item, target)); }}><option value="">Изменить</option>{membershipTransitions[item.status]?.map((target) => <option value={target} key={target}>{statusNames[target] ?? target}</option>)}</select> : "—"}</td>
      </tr>)}</tbody></table></div>{!data.length ? <Empty text="Членства не оформлены" /> : null}</section>
  </>;
}

function AccountsSection({
  data,
  members,
  principal,
  busy,
  run,
}: {
  data: UserAccount[];
  members: Member[];
  principal: Principal;
  busy: boolean;
  run: (action: RunAction) => void;
}) {
  const canManage = hasRole(principal, "SECURITY_ADMIN");
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [memberId, setMemberId] = useState("");
  const [search, setSearch] = useState("");
  const visible = data.filter((item) => item.login.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    run(async () => {
      await createUser({ login, temporary_password: password, member_id: memberId || null });
      setLogin("");
      setPassword("");
      setMemberId("");
    });
  }

  return <>
    {canManage ? <section className="action-band registry-command"><form onSubmit={submit}>
      <label>Логин<input value={login} onChange={(event) => setLogin(event.target.value)} autoComplete="off" required /></label>
      <label>Временный пароль<input value={password} onChange={(event) => setPassword(event.target.value)} type="password" autoComplete="new-password" required minLength={12} /></label>
      <label>Участник<select value={memberId} onChange={(event) => setMemberId(event.target.value)}><option value="">Техническая запись</option>{members.filter((member) => member.status === "ACTIVE").map((member) => <option value={member.id} key={member.id}>{member.display_name}</option>)}</select></label>
      <button className="primary-button" type="submit" disabled={busy}><Plus size={17} /><span>Создать учетную запись</span></button>
    </form></section> : null}
    <section className="panel"><div className="panel-heading access-heading"><h2>Учетные записи</h2><label>Поиск по логину<input value={search} onChange={(event) => setSearch(event.target.value)} /></label><span>{visible.length} из {data.length}</span></div>
      <div className="table-wrap"><table><thead><tr><th>Логин</th><th>Участник</th><th>Статус</th><th>Последний вход</th><th>Действие</th></tr></thead><tbody>{visible.map((item) => <tr key={item.id}>
        <td><strong>{item.login}</strong><small>v{item.version}</small></td><td>{members.find((member) => member.id === item.member_id)?.display_name ?? "Техническая запись"}</td><td><Status value={item.status} /></td><td>{formatLocalDateTime(item.last_login_at)}</td>
        <td>{canManage && item.id !== principal.user_id ? <button className="compact-command" disabled={busy} onClick={() => run(() => transitionUser(item, item.status === "ACTIVE" ? "DISABLED" : "ACTIVE"))}>{item.status === "ACTIVE" ? <CircleOff size={15} /> : <BadgeCheck size={15} />}{item.status === "ACTIVE" ? "Отключить вход" : "Разрешить вход"}</button> : item.id === principal.user_id ? "Текущая запись" : "—"}</td>
      </tr>)}</tbody></table></div>{!visible.length ? <Empty text="Учетные записи не найдены" /> : null}</section>
  </>;
}

function ImportSection({
  data,
  members,
  cooperatives,
  principal,
  busy,
  run,
}: {
  data: MemberImportBatch[];
  members: Member[];
  cooperatives: Cooperative[];
  principal: Principal;
  busy: boolean;
  run: (action: RunAction) => void;
}) {
  const { t, i18n } = useTranslation();
  const canStage = hasRole(principal, "MEMBER_REGISTRAR");
  const canReview = hasRole(principal, "DATA_STEWARD");
  const canApply = hasRole(principal, "MEMBER_REGISTRAR");
  const allowedCooperatives = cooperatives.filter((cooperative) => cooperative.status === "ACTIVE" && principal.roles.some((grant) => grant.cooperative_id === null || grant.cooperative_id === cooperative.id));
  const [cooperativeId, setCooperativeId] = useState(allowedCooperatives[0]?.id ?? "");
  const [selectedId, setSelectedId] = useState(data[0]?.id ?? "");
  const [file, setFile] = useState<File | null>(null);
  const [fileKey, setFileKey] = useState(0);
  const [fileError, setFileError] = useState("");
  const selected = data.find((item) => item.id === selectedId) ?? data[0];
  const rows = useQuery({
    queryKey: ["member-import-rows", selected?.id],
    queryFn: () => getMemberImportRows(selected!.id),
    enabled: Boolean(selected),
  });

  useEffect(() => {
    if ((!selectedId || !data.some((item) => item.id === selectedId)) && data[0]) {
      setSelectedId(data[0].id);
    }
  }, [data, selectedId]);

  function chooseFile(next: File | null) {
    setFile(next);
    setFileError(next && next.size > 1_000_000 ? t("admin.intake.fileTooLarge") : "");
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file || fileError) return;
    run(async () => {
      await stageMemberImport({
        cooperative_id: cooperativeId,
        source_name: file.name,
        csv_text: await file.text(),
      });
      setFile(null);
      setFileKey((value) => value + 1);
    });
  }

  function downloadTemplate() {
    const exampleName = i18n.language.startsWith("ru") ? "Новый участник" : "New member";
    const content = `display_name,identifier_type,identifier_value\n${exampleName},EXTERNAL_REFERENCE,member-001\n`;
    const url = URL.createObjectURL(new Blob(["\ufeff", content], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "member-import-template.csv";
    link.click();
    URL.revokeObjectURL(url);
  }

  function importStatus(value: string) {
    return <span className={`status status-${value.toLowerCase()}`}>{t(`admin.intake.status.${value}`, { defaultValue: value })}</span>;
  }

  const selectedRows = rows.data ?? [];
  return <>
    {canStage ? <section className="action-band registry-command import-command"><form onSubmit={submit}>
      <label>{t("admin.intake.cooperative")}<select value={cooperativeId} onChange={(event) => setCooperativeId(event.target.value)} required><option value="">{t("admin.intake.chooseCooperative")}</option>{allowedCooperatives.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label>
      <label className="import-file"><span>{t("admin.intake.csvFile")}</span><input key={fileKey} type="file" accept=".csv,text/csv,text/plain" onChange={(event) => chooseFile(event.target.files?.[0] ?? null)} required /><small>{file?.name ?? t("admin.intake.noFile")}</small></label>
      <button className="icon-button" type="button" title={t("admin.intake.downloadTemplate")} aria-label={t("admin.intake.downloadTemplate")} onClick={downloadTemplate}><Download size={18} /></button>
      <button className="primary-button" type="submit" disabled={busy || !cooperativeId || !file || Boolean(fileError)}><Upload size={17} /><span>{t("admin.intake.stage")}</span></button>
    </form>{fileError ? <p className="form-error" role="alert">{fileError}</p> : null}</section> : null}
    <div className="import-workspace">
      <section className="panel"><div className="panel-heading"><h2>{t("admin.intake.batches")}</h2><span>{data.length}</span></div>
        <div className="table-wrap"><table><thead><tr><th>{t("admin.intake.file")}</th><th>{t("admin.intake.statusLabel")}</th><th>{t("admin.intake.rows")}</th><th>{t("admin.intake.created")}</th></tr></thead><tbody>{data.map((item) => <tr className={selected?.id === item.id ? "selected-row" : ""} key={item.id}><td><button className="table-link" onClick={() => setSelectedId(item.id)}><strong data-i18n-ignore="true">{item.source_name}</strong><small>{cooperatives.find((cooperative) => cooperative.id === item.cooperative_id)?.name ?? item.cooperative_id}</small></button></td><td>{importStatus(item.status)}</td><td>{item.row_count}</td><td>{formatLocalDateTime(item.created_at)}</td></tr>)}</tbody></table></div>{!data.length ? <Empty text={t("admin.intake.noBatches")} /> : null}
      </section>
      <section className="panel import-detail"><div className="panel-heading"><h2>{selected ? selected.source_name : t("admin.intake.preview")}</h2>{selected ? importStatus(selected.status) : null}</div>
        {selected ? <><dl className="import-summary"><div><dt>{t("admin.intake.ready")}</dt><dd>{selected.ready_count}</dd></div><div><dt>{t("admin.intake.duplicates")}</dt><dd>{selected.duplicate_count}</dd></div><div><dt>{t("admin.intake.errors")}</dt><dd>{selected.invalid_count}</dd></div><div><dt>{t("admin.intake.applied")}</dt><dd>{selected.applied_count}</dd></div></dl>
          <div className="import-actions">
            {canStage && ["STAGED", "PREVIEWED"].includes(selected.status) ? <button className="compact-command" disabled={busy} onClick={() => run(() => previewMemberImport(selected))}><FileSearch size={16} />{selected.status === "STAGED" ? t("admin.intake.dryRun") : t("admin.intake.refreshDryRun")}</button> : null}
            {canReview && selected.status === "PREVIEWED" && selected.created_by_user_id !== principal.user_id ? <><button className="compact-command approve" disabled={busy || selected.ready_count < 1} onClick={() => run(() => decideMemberImport(selected, true, "INDEPENDENT_REVIEW"))}><FileCheck2 size={16} />{t("admin.intake.approve")}</button><button className="compact-command" disabled={busy} onClick={() => run(() => decideMemberImport(selected, false, "REJECTED_BY_STEWARD"))}><XCircle size={16} />{t("admin.intake.reject")}</button></> : null}
            {canReview && selected.status === "PREVIEWED" && selected.created_by_user_id === principal.user_id ? <span className="independent-note"><AlertTriangle size={15} />{t("admin.intake.anotherReviewer")}</span> : null}
            {canApply && selected.status === "APPROVED" ? <button className="primary-button" disabled={busy} onClick={() => run(() => applyMemberImport(selected))}><FileCheck2 size={16} />{t("admin.intake.apply")}</button> : null}
          </div>
          {rows.isPending ? <div className="state" role="status"><RefreshCw className="spin" size={20} />{t("admin.intake.loadingRows")}</div> : rows.isError ? <ErrorLine error={rows.error} /> : <div className="table-wrap"><table><thead><tr><th>#</th><th>{t("admin.intake.memberName")}</th><th>{t("admin.intake.identifierType")}</th><th>{t("admin.intake.result")}</th><th>{t("admin.intake.match")}</th></tr></thead><tbody>{selectedRows.map((row: MemberImportRow) => <tr key={row.id}><td>{row.row_number}</td><td><strong data-i18n-ignore="true">{row.display_name}</strong></td><td>{row.identifier_type ?? "—"}</td><td>{importStatus(row.status)}{row.error_code ? <small>{t(`admin.intake.rowError.${row.error_code}`, { defaultValue: row.error_code })}</small> : null}</td><td>{row.candidate_member_id ? <span data-i18n-ignore="true">{members.find((member) => member.id === row.candidate_member_id)?.display_name ?? row.candidate_member_id}</span> : row.match_basis ? t(`admin.intake.matchBasis.${row.match_basis}`, { defaultValue: row.match_basis }) : "—"}</td></tr>)}</tbody></table></div>}
        </> : <Empty text={t("admin.intake.selectBatch")} />}
      </section>
    </div>
  </>;
}
function NodesSection({
  localNode,
  externalNodes,
  onManageNodes,
}: {
  localNode: Awaited<ReturnType<typeof fetchSystemStatus>> | undefined;
  externalNodes: Awaited<ReturnType<typeof getFederationNodes>>;
  onManageNodes: () => void;
}) {
  return <section className="panel"><div className="panel-heading"><h2>Узлы</h2><button className="compact-command" onClick={onManageNodes}><Network size={15} />Управление узлами</button></div>
    <div className="table-wrap"><table><thead><tr><th>Узел</th><th>Тип</th><th>Среда или территория</th><th>Статус</th><th>Версия</th></tr></thead><tbody>
      {localNode ? <tr><td><strong>{localNode.node.display_name}</strong><small>{localNode.node.code}</small></td><td>Локальный</td><td>{environmentNames[localNode.node.environment] ?? localNode.node.environment}</td><td><Status value={localNode.status} /></td><td>{localNode.release.version}</td></tr> : null}
      {externalNodes.map((node) => <tr key={node.id}><td><strong>{node.display_name}</strong><small>{node.node_code}</small></td><td>Внешний</td><td>{node.territory}</td><td><Status value={node.status} /></td><td>v{node.version}</td></tr>)}
    </tbody></table></div>{!localNode && !externalNodes.length ? <Empty text="Узлы не зарегистрированы" /> : null}</section>;
}

export default function AdminDirectoryView({
  principal,
  onManageNodes,
}: {
  principal: Principal;
  onManageNodes: () => void;
}) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const canReadAccounts = hasRole(principal, "SECURITY_ADMIN", "AUDITOR");
  const canReadImports = hasRole(principal, "MEMBER_REGISTRAR", "DATA_STEWARD", "AUDITOR");
  const canReadNodes = hasRole(principal, "SECURITY_ADMIN", "AUDITOR", "NODE_REGISTRAR", "NODE_TECHNICAL_CUSTODIAN", "NODE_SECURITY_ADMIN", "NODE_BUSINESS_OPERATOR", "NODE_AUDITOR");
  const [section, setSection] = useState<Section>("members");
  const [actionError, setActionError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const cooperatives = useQuery({ queryKey: ["cooperatives"], queryFn: getCooperatives });
  const members = useQuery({ queryKey: ["members"], queryFn: getMembers });
  const memberships = useQuery({ queryKey: ["memberships"], queryFn: getMemberships });
  const imports = useQuery({ queryKey: ["member-imports"], queryFn: getMemberImports, enabled: canReadImports });
  const accounts = useQuery({ queryKey: ["users"], queryFn: getUsers, enabled: canReadAccounts });
  const system = useQuery({ queryKey: ["system-status"], queryFn: () => fetchSystemStatus(), enabled: canReadNodes });
  const nodes = useQuery({ queryKey: ["federation", "nodes"], queryFn: getFederationNodes, enabled: canReadNodes });
  const canManageOrganizations = hasRole(principal, "NODE_REGISTRAR", "SECURITY_ADMIN");
  const tabs = [
    ["organizations", "Организации", Building2, true],
    ["members", "Участники", Users, true],
    ["memberships", "Членства", Link2, true],
    ["imports", t("admin.intake.tab"), FileUp, canReadImports],
    ["accounts", "Учетные записи", UserCog, canReadAccounts],
    ["nodes", "Узлы", Network, canReadNodes],
  ] as const;

  async function invalidate() {
    await Promise.all([
      client.invalidateQueries({ queryKey: ["cooperatives"] }),
      client.invalidateQueries({ queryKey: ["members"] }),
      client.invalidateQueries({ queryKey: ["memberships"] }),
      client.invalidateQueries({ queryKey: ["member-imports"] }),
      client.invalidateQueries({ queryKey: ["member-import-rows"] }),
      client.invalidateQueries({ queryKey: ["users"] }),
      client.invalidateQueries({ queryKey: ["sessions"] }),
      client.invalidateQueries({ queryKey: ["admin-overview"] }),
    ]);
  }

  function run(action: RunAction) {
    setBusy(true);
    setActionError(null);
    void action().then(invalidate).catch(setActionError).finally(() => setBusy(false));
  }

  const baseLoading = cooperatives.isPending || members.isPending || memberships.isPending;
  const baseError = cooperatives.error ?? members.error ?? memberships.error;
  const currentError = section === "imports" ? imports.error : section === "accounts" ? accounts.error : section === "nodes" ? system.error ?? nodes.error : baseError;
  const currentLoading = baseLoading || (section === "imports" && canReadImports && imports.isPending) || (section === "accounts" && canReadAccounts && accounts.isPending) || (section === "nodes" && canReadNodes && (system.isPending || nodes.isPending));

  return <div className="view-stack admin-directory">
    <header className="view-header"><div><span className="eyebrow">Администрирование</span><h1>Реестры системы</h1><p>Люди, организации, доступ и узлы</p></div>
      <div className="section-tabs" role="tablist" aria-label="Реестры">{tabs.filter(([, , , visible]) => visible).map(([key, label, Icon]) => <button role="tab" aria-label={label} title={label} aria-selected={section === key} className={section === key ? "active" : ""} onClick={() => setSection(key)} key={key}><Icon size={16} /><span>{label}</span></button>)}</div>
    </header>
    {busy ? <div className="registry-progress" role="status"><RefreshCw className="spin" size={16} />Сохраняем изменение</div> : null}
    {actionError ? <ErrorLine error={actionError} /> : null}
    {currentLoading ? <div className="state" role="status"><RefreshCw className="spin" size={24} />Загрузка реестра</div> : currentError ? <ErrorLine error={currentError} /> : null}
    {!currentLoading && !currentError && section === "organizations" ? <OrganizationsSection data={cooperatives.data ?? []} canManage={canManageOrganizations} busy={busy} run={run} /> : null}
    {!currentLoading && !currentError && section === "members" ? <MembersSection data={members.data ?? []} cooperatives={cooperatives.data ?? []} principal={principal} busy={busy} run={run} /> : null}
    {!currentLoading && !currentError && section === "memberships" ? <MembershipsSection data={memberships.data ?? []} members={members.data ?? []} cooperatives={cooperatives.data ?? []} principal={principal} busy={busy} run={run} /> : null}
    {!currentLoading && !currentError && section === "imports" ? <ImportSection data={imports.data ?? []} members={members.data ?? []} cooperatives={cooperatives.data ?? []} principal={principal} busy={busy} run={run} /> : null}
    {!currentLoading && !currentError && section === "accounts" ? <AccountsSection data={accounts.data ?? []} members={members.data ?? []} principal={principal} busy={busy} run={run} /> : null}
    {!currentLoading && !currentError && section === "nodes" ? <NodesSection localNode={system.data} externalNodes={nodes.data ?? []} onManageNodes={onManageNodes} /> : null}
  </div>;
}
