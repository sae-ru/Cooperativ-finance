import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BadgeCheck,
  Building2,
  CircleOff,
  KeyRound,
  Link2,
  Network,
  Plus,
  RefreshCw,
  UserCog,
  Users,
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";

import {
  type Cooperative,
  type Member,
  type Membership,
  type Principal,
  type RoleCode,
  type UserAccount,
  createCooperative,
  createMember,
  createMembership,
  createUser,
  getCooperatives,
  getMembers,
  getMemberships,
  getUsers,
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

type Section = "organizations" | "members" | "memberships" | "accounts" | "nodes";
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
  const canCreate = hasRole(principal, "MEMBER_REGISTRAR");
  const canTransition = hasRole(principal, "MEMBER_REGISTRAR", "RISK_ADMIN");
  const allowedCooperatives = cooperatives.filter((cooperative) => cooperative.status === "ACTIVE" && principal.roles.some((grant) => grant.cooperative_id === null || grant.cooperative_id === cooperative.id));
  const [cooperativeId, setCooperativeId] = useState(allowedCooperatives[0]?.id ?? "");
  const [displayName, setDisplayName] = useState("");
  const [identifier, setIdentifier] = useState("");
  const [search, setSearch] = useState("");
  const visible = useMemo(() => data.filter((item) => item.display_name.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())), [data, search]);
  const defaultCooperativeId = allowedCooperatives[0]?.id ?? "";

  useEffect(() => {
    if (!cooperativeId && defaultCooperativeId) {
      setCooperativeId(defaultCooperativeId);
    }
  }, [cooperativeId, defaultCooperativeId]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    run(async () => {
      await createMember({
        cooperative_id: cooperativeId,
        display_name: displayName,
        ...(identifier ? { identifier_type: "EXTERNAL_REFERENCE", identifier_value: identifier } : {}),
      });
      setDisplayName("");
      setIdentifier("");
    });
  }

  return <>
    {canCreate ? <section className="action-band registry-command"><form onSubmit={submit}>
      <label>Организация<select value={cooperativeId} onChange={(event) => setCooperativeId(event.target.value)} required><option value="">Выберите</option>{allowedCooperatives.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label>
      <label>Имя участника<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} required /></label>
      <label>Внешний идентификатор<input value={identifier} onChange={(event) => setIdentifier(event.target.value)} /></label>
      <button className="primary-button" type="submit" disabled={busy || !cooperativeId}><Plus size={17} /><span>Добавить участника</span></button>
    </form></section> : null}
    <section className="panel"><div className="panel-heading access-heading"><h2>Участники</h2><label>Поиск по имени<input value={search} onChange={(event) => setSearch(event.target.value)} /></label><span>{visible.length} из {data.length}</span></div>
      <div className="table-wrap"><table><thead><tr><th>Имя</th><th>Организация регистрации</th><th>Статус</th><th>Создан</th><th>Действие</th></tr></thead><tbody>{visible.map((item) => <tr key={item.id}>
        <td><strong>{item.display_name}</strong><small>v{item.version}</small></td><td>{cooperatives.find((cooperative) => cooperative.id === item.registered_by_cooperative_id)?.name ?? "Наследованная запись"}</td><td><Status value={item.status} /></td><td>{formatLocalDateTime(item.created_at)}</td>
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
  const client = useQueryClient();
  const canReadAccounts = hasRole(principal, "SECURITY_ADMIN", "AUDITOR");
  const canReadNodes = hasRole(principal, "SECURITY_ADMIN", "AUDITOR", "NODE_REGISTRAR", "NODE_TECHNICAL_CUSTODIAN", "NODE_SECURITY_ADMIN", "NODE_BUSINESS_OPERATOR", "NODE_AUDITOR");
  const [section, setSection] = useState<Section>("members");
  const [actionError, setActionError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const cooperatives = useQuery({ queryKey: ["cooperatives"], queryFn: getCooperatives });
  const members = useQuery({ queryKey: ["members"], queryFn: getMembers });
  const memberships = useQuery({ queryKey: ["memberships"], queryFn: getMemberships });
  const accounts = useQuery({ queryKey: ["users"], queryFn: getUsers, enabled: canReadAccounts });
  const system = useQuery({ queryKey: ["system-status"], queryFn: () => fetchSystemStatus(), enabled: canReadNodes });
  const nodes = useQuery({ queryKey: ["federation", "nodes"], queryFn: getFederationNodes, enabled: canReadNodes });
  const canManageOrganizations = hasRole(principal, "NODE_REGISTRAR", "SECURITY_ADMIN");
  const tabs = [
    ["organizations", "Организации", Building2, true],
    ["members", "Участники", Users, true],
    ["memberships", "Членства", Link2, true],
    ["accounts", "Учетные записи", UserCog, canReadAccounts],
    ["nodes", "Узлы", Network, canReadNodes],
  ] as const;

  async function invalidate() {
    await Promise.all([
      client.invalidateQueries({ queryKey: ["cooperatives"] }),
      client.invalidateQueries({ queryKey: ["members"] }),
      client.invalidateQueries({ queryKey: ["memberships"] }),
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
  const currentError = section === "accounts" ? accounts.error : section === "nodes" ? system.error ?? nodes.error : baseError;
  const currentLoading = baseLoading || (section === "accounts" && canReadAccounts && accounts.isPending) || (section === "nodes" && canReadNodes && (system.isPending || nodes.isPending));

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
    {!currentLoading && !currentError && section === "accounts" ? <AccountsSection data={accounts.data ?? []} members={members.data ?? []} principal={principal} busy={busy} run={run} /> : null}
    {!currentLoading && !currentError && section === "nodes" ? <NodesSection localNode={system.data} externalNodes={nodes.data ?? []} onManageNodes={onManageNodes} /> : null}
  </div>;
}
