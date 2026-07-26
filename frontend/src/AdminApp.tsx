import {
  Activity,
  Check,
  Calculator,
  ClipboardList,
  Fingerprint,
  Handshake,
  HandHeart,
  FileKey2,
  KeyRound,
  LayoutDashboard,
  LogOut,
  Network,
  PackageSearch,
  Plus,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Siren,
  Scale,
  ScanSearch,
  ShoppingCart,
  UserCog,
  Users,
  Waypoints,
  X
} from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";

import {
  AdminApiError,
  type AuthSession,
  type Member,
  type Principal,
  type RoleCode,
  assignRole,
  changePassword,
  createMember,
  createMembership,
  createUser,
  decideRole,
  getAudit,
  getCooperatives,
  getMembers,
  getMemberships,
  getOverview,
  getRoles,
  getSessions,
  getUsers,
  login,
  logout,
  restoreSession,
  revokeSession,
  transitionMember
} from "./api/admin";
import { useSystemStatus } from "./features/system/use-system-status";
import InterfaceControls from "./InterfaceControls";
import { userErrorMessage } from "./shared/api-error";
import { formatLocalDateTime } from "./shared/date-time";
import "./admin.css";

const ClearingView = lazy(() => import("./ClearingView"));
const FederatedClearingView = lazy(() => import("./FederatedClearingView"));
const CrisisView = lazy(() => import("./CrisisView"));
const DiscoveryView = lazy(() => import("./DiscoveryView"));
const ExchangeView = lazy(() => import("./ExchangeView"));
const FederationView = lazy(() => import("./FederationView"));
const InventoryView = lazy(() => import("./InventoryView"));
const MemberHomeView = lazy(() => import("./MemberHomeView"));
const OperationsView = lazy(() => import("./OperationsView"));
const ResponsibilityView = lazy(() => import("./ResponsibilityView"));
const RightsView = lazy(() => import("./RightsView"));
const RiskView = lazy(() => import("./RiskView"));
const AntifraudView = lazy(() => import("./AntifraudView"));
const SolidarityView = lazy(() => import("./SolidarityView"));
const TrustView = lazy(() => import("./TrustView"));

type View = "memberHome" | "overview" | "members" | "access" | "responsibility" | "discovery" | "exchange" | "clearing" | "federatedClearing" | "risk" | "antifraud" | "trust" | "solidarity" | "crisis" | "inventory" | "rights" | "federation" | "operations" | "audit";

const roleNames: Record<RoleCode, string> = {
  EXCHANGE_PARTICIPANT: "Участник обмена",
  MEMBER_REGISTRAR: "Регистратор участников",
  COOPERATIVE_ADMIN: "Администратор кооператива",
  DATA_STEWARD: "Распорядитель данных",
  RISK_ADMIN: "Администратор рисков",
  SECURITY_ADMIN: "Администратор безопасности",
  NODE_REGISTRAR: "Регистратор узлов",
  NODE_TECHNICAL_CUSTODIAN: "Технический хранитель узла",
  NODE_SECURITY_ADMIN: "Администратор безопасности узла",
  NODE_BUSINESS_OPERATOR: "Оператор деятельности узла",
  NODE_AUDITOR: "Аудитор узла",
  AUDITOR: "Аудитор",
  ARBITRATOR: "Арбитр",
  WAREHOUSE_CUSTODIAN: "Хранитель склада",
  INVENTORY_CONTROLLER: "Контролёр запасов",
  LOGISTICS_OPERATOR: "Логист",
  RIGHTS_OPERATOR: "Оператор товарных прав",
  CLEARING_OPERATOR: "Оператор клиринга",
  CLEARING_CONTROLLER: "Контролер клиринга",
  CLEARING_FINALIZER: "Финализатор клиринга",
  SOLIDARITY_OPERATOR: "Оператор солидарной помощи",
  SOLIDARITY_CONTROLLER: "Контролёр солидарной помощи",
  CRISIS_OPERATOR: "Оператор кризисного режима",
  CRISIS_CONTROLLER: "Контролёр кризисного режима"
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
  PENDING_APPROVAL: "Нужно одобрение",
  REVOKED: "Отозвано",
  DISABLED: "Отключен"
};

function errorText(error: unknown): string {
  return userErrorMessage(error);
}

function hasRole(principal: Principal, ...roles: RoleCode[]): boolean {
  return principal.roles.some((item) => roles.includes(item.role));
}

function LoginView({ onAuthenticated }: { onAuthenticated: (value: AuthSession) => void }) {
  const [loginValue, setLoginValue] = useState("");
  const [password, setPassword] = useState("");
  const mutation = useMutation({
    mutationFn: () => login(loginValue, password),
    onSuccess: onAuthenticated
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    mutation.mutate();
  }

  return (
    <main className="auth-screen">
      <section className="auth-panel" aria-labelledby="login-title">
        <div className="auth-brand">
          <img src="/mark.svg" width="44" height="44" alt="" />
          <div><strong>Cooperative Clearing</strong><span>Локальный узел</span></div>
        </div>
        <form onSubmit={submit}>
          <h1 id="login-title">Вход оператора</h1>
          <label>Учетная запись<input autoComplete="username" value={loginValue} onChange={(e) => setLoginValue(e.target.value)} required /></label>
          <label>Пароль<input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></label>
          {mutation.isError ? <p className="form-error" role="alert">{errorText(mutation.error)}</p> : null}
          <button className="primary-button" disabled={mutation.isPending} type="submit">
            <KeyRound size={17} /> <span>{mutation.isPending ? "Проверка" : "Войти"}</span>
          </button>
        </form>
      </section>
    </main>
  );
}

function PasswordChangeView({
  session,
  onChanged,
  onLogout
}: {
  session: AuthSession;
  onChanged: (value: AuthSession) => void;
  onLogout: () => void;
}) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const mutation = useMutation({
    mutationFn: () => changePassword(current, next),
    onSuccess: onChanged
  });
  return (
    <main className="auth-screen">
      <section className="auth-panel" aria-labelledby="password-title">
        <div className="auth-brand"><ShieldCheck size={36} /><div><strong>{session.principal.login}</strong><span>Первичный вход</span></div></div>
        <form onSubmit={(event) => { event.preventDefault(); mutation.mutate(); }}>
          <h1 id="password-title">Смена временного пароля</h1>
          <label>Текущий пароль<input type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} required /></label>
          <label>Новый пароль<input type="password" autoComplete="new-password" minLength={16} value={next} onChange={(e) => setNext(e.target.value)} required /></label>
          {mutation.isError ? <p className="form-error" role="alert">{errorText(mutation.error)}</p> : null}
          <button className="primary-button" disabled={mutation.isPending} type="submit"><Check size={17} /><span>Сменить пароль</span></button>
          <button className="secondary-button" disabled={mutation.isPending} type="button" onClick={onLogout}><LogOut size={17} /><span>Выйти</span></button>
        </form>
      </section>
    </main>
  );
}

function Overview() {
  const system = useSystemStatus();
  const overview = useQuery({ queryKey: ["admin-overview"], queryFn: getOverview });
  if (system.isPending || overview.isPending) return <Loading />;
  if (system.isError || !system.data || overview.isError || !overview.data) {
    return <ErrorPanel error={system.error ?? overview.error} />;
  }
  const metrics = [
    ["Участники", overview.data.members, Users],
    ["Активные", overview.data.active_members, ShieldCheck],
    ["Учетные записи", overview.data.users, UserCog],
    ["Сессии", overview.data.active_sessions, KeyRound],
    ["Кооперативы", overview.data.cooperatives, Network],
    ["Ожидают решения", overview.data.pending_role_approvals, ClipboardList]
  ] as const;
  return (
    <div className="view-stack">
      <header className="view-header"><div><span className="eyebrow">{system.data.status === "OPERATIONAL" ? "Узел работает" : "Требует внимания"}</span><h1>Состояние узла</h1><p>{system.data.node.display_name} · {system.data.node.code}</p></div><span className="release">{system.data.release.version}<br />{system.data.release.schema_revision}</span></header>
      <section className="metric-grid" aria-label="Сводка">
        {metrics.map(([label, value, Icon]) => <article className="metric" key={label}><Icon size={18} /><span>{label}</span><strong>{value}</strong></article>)}
      </section>
      <section className="panel"><div className="panel-heading"><h2>Компоненты</h2><span>{system.data.checks.length}</span></div><div className="rows">{system.data.checks.map((item) => <div className="data-row" key={item.name}><strong>{item.name}</strong><code>{item.code}</code><Status value={item.status} /></div>)}</div></section>
      <section className="panel"><div className="panel-heading"><h2>Операционные сообщения</h2><span>{system.data.notices.length}</span></div>{system.data.notices.length ? <div className="rows">{system.data.notices.map((item) => <div className="data-row" key={item.code}><strong>{item.code}</strong><span>{item.message_key}</span><time>{formatLocalDateTime(item.created_at)}</time></div>)}</div> : <Empty text="Активных сообщений нет" />}</section>
    </div>
  );
}

const transitions: Record<string, string[]> = {
  APPLICANT: ["PENDING_VERIFICATION", "REJECTED"],
  PENDING_VERIFICATION: ["LIMITED", "ACTIVE", "REJECTED"],
  LIMITED: ["ACTIVE", "SUSPENDED", "EXITED"],
  ACTIVE: ["SUSPENDED", "EXITED"],
  SUSPENDED: ["ACTIVE", "EXITED"]
};

function MembersView({ principal }: { principal: Principal }) {
  const client = useQueryClient();
  const members = useQuery({ queryKey: ["members"], queryFn: getMembers });
  const memberships = useQuery({ queryKey: ["memberships"], queryFn: getMemberships });
  const cooperatives = useQuery({ queryKey: ["cooperatives"], queryFn: getCooperatives });
  const [name, setName] = useState("");
  const [identifier, setIdentifier] = useState("");
  const [membershipMember, setMembershipMember] = useState("");
  const [memberNumber, setMemberNumber] = useState("");
  const [memberSearch, setMemberSearch] = useState("");
  const [membershipMessage, setMembershipMessage] = useState("");
  const cooperativeId = principal.roles.find((item) => ["MEMBER_REGISTRAR", "COOPERATIVE_ADMIN"].includes(item.role) && item.cooperative_id)?.cooperative_id ?? cooperatives.data?.find((item) => item.status === "ACTIVE")?.id ?? "";
  const visibleMembers = (members.data ?? []).filter((item) => item.display_name.toLocaleLowerCase().includes(memberSearch.trim().toLocaleLowerCase())).slice(0, 25);
  const invalidate = () => Promise.all([client.invalidateQueries({ queryKey: ["members"] }), client.invalidateQueries({ queryKey: ["memberships"] }), client.invalidateQueries({ queryKey: ["admin-overview"] })]);
  const addMember = useMutation({ mutationFn: () => createMember({ display_name: name, ...(identifier ? { identifier_type: "EXTERNAL_REFERENCE", identifier_value: identifier } : {}) }), onSuccess: async () => { setName(""); setIdentifier(""); await invalidate(); } });
  const changeStatus = useMutation({ mutationFn: ({ member, target }: { member: Member; target: string }) => transitionMember(member, target), onSuccess: invalidate });
  const addMembership = useMutation({ mutationFn: () => createMembership({ cooperative_id: cooperativeId, member_id: membershipMember, member_number: memberNumber }), onSuccess: async () => { setMembershipMember(""); setMemberNumber(""); setMembershipMessage("Членство оформлено"); await invalidate(); } });
  if (members.isPending || memberships.isPending || cooperatives.isPending) return <Loading />;
  if (members.isError || memberships.isError || cooperatives.isError) return <ErrorPanel error={members.error ?? memberships.error ?? cooperatives.error} />;
  return (
    <div className="view-stack">
      <header className="view-header"><div><span className="eyebrow">Реестр</span><h1>Участники</h1><p>{members.data?.length ?? 0} записей</p></div></header>
      <section className="action-band"><form onSubmit={(event) => { event.preventDefault(); addMember.mutate(); }}><label>Имя участника<input value={name} onChange={(e) => setName(e.target.value)} required /></label><label>Внешний идентификатор<input value={identifier} onChange={(e) => setIdentifier(e.target.value)} /></label><button className="primary-button" type="submit" disabled={addMember.isPending}><Plus size={17} /><span>Добавить</span></button></form>{addMember.isError ? <p className="form-error">{errorText(addMember.error)}</p> : null}</section>
      <section className="panel"><div className="panel-heading access-heading"><h2>Карточки участников</h2><label>Поиск по имени<input value={memberSearch} onChange={(event) => setMemberSearch(event.target.value)} /></label><span>{visibleMembers.length} из {members.data?.length ?? 0}</span></div><div className="table-wrap"><table><thead><tr><th>Имя</th><th>Статус</th><th>Версия</th><th>Создан</th><th>Действие</th></tr></thead><tbody>{visibleMembers.map((member) => <tr key={member.id}><td><strong>{member.display_name}</strong></td><td><Status value={member.status} /></td><td>{member.version}</td><td>{formatLocalDateTime(member.created_at)}</td><td><select aria-label={`Новый статус ${member.display_name}`} defaultValue="" onChange={(e) => { if (e.target.value) changeStatus.mutate({ member, target: e.target.value }); e.target.value = ""; }}><option value="">Изменить</option>{(transitions[member.status] ?? []).map((value) => <option key={value} value={value}>{statusNames[value] ?? value}</option>)}</select></td></tr>)}</tbody></table></div></section>
      <section className="action-band"><form onSubmit={(event) => { event.preventDefault(); setMembershipMessage(""); addMembership.mutate(); }}><label>Участник<select value={membershipMember} onChange={(e) => setMembershipMember(e.target.value)} required><option value="">Выберите</option>{members.data?.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></label><label>Номер пая<input value={memberNumber} onChange={(e) => setMemberNumber(e.target.value)} required /></label><button className="primary-button" type="submit" disabled={addMembership.isPending || !cooperativeId}><Plus size={17} /><span>{addMembership.isPending ? "Оформляем" : "Оформить членство"}</span></button></form>{addMembership.isError ? <p className="form-error">{errorText(addMembership.error)}</p> : null}{membershipMessage ? <p className="form-success" role="status"><Check size={16} />{membershipMessage}</p> : null}</section>
      <section className="panel"><div className="panel-heading"><h2>Членства</h2><span>{memberships.data?.length ?? 0}</span></div><div className="rows">{memberships.data?.map((item) => <div className="data-row" key={item.id}><strong>{item.member_number}</strong><span>{members.data?.find((member) => member.id === item.member_id)?.display_name ?? item.member_id}</span><Status value={item.status} /></div>)}</div></section>
    </div>
  );
}

function AccessView({ principal }: { principal: Principal }) {
  const client = useQueryClient();
  const users = useQuery({ queryKey: ["users"], queryFn: getUsers });
  const roles = useQuery({ queryKey: ["roles"], queryFn: getRoles });
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: getSessions });
  const cooperatives = useQuery({ queryKey: ["cooperatives"], queryFn: getCooperatives });
  const members = useQuery({ queryKey: ["members"], queryFn: getMembers });
  const [loginValue, setLoginValue] = useState("");
  const [temporaryPassword, setTemporaryPassword] = useState("");
  const [memberId, setMemberId] = useState("");
  const [profile, setProfile] = useState<"PARTICIPANT" | "SELLER" | "TECHNICAL">("PARTICIPANT");
  const [accountSearch, setAccountSearch] = useState("");
  const [targetUser, setTargetUser] = useState("");
  const [targetRole, setTargetRole] = useState<RoleCode>("EXCHANGE_PARTICIPANT");
  const [createdMessage, setCreatedMessage] = useState("");

  useEffect(() => {
    if (!memberId) {
      const activeMember = members.data?.find((item) => item.status === "ACTIVE");
      if (activeMember) setMemberId(activeMember.id);
    }
  }, [memberId, members.data]);

  const refresh = () => Promise.all([
    client.invalidateQueries({ queryKey: ["users"] }),
    client.invalidateQueries({ queryKey: ["roles"] }),
    client.invalidateQueries({ queryKey: ["sessions"] }),
    client.invalidateQueries({ queryKey: ["admin-overview"] })
  ]);
  const addUser = useMutation({
    mutationFn: async () => {
      const linkedMemberId = profile === "TECHNICAL" ? null : memberId;
      const created = await createUser({
        login: loginValue,
        temporary_password: temporaryPassword,
        member_id: linkedMemberId
      });
      if (profile !== "TECHNICAL") {
        const cooperativeId = cooperatives.data?.[0]?.id ?? null;
        await assignRole({
          user_id: created.object_id,
          role: "EXCHANGE_PARTICIPANT",
          cooperative_id: cooperativeId
        });
        if (profile === "SELLER") {
          await assignRole({
            user_id: created.object_id,
            role: "NODE_BUSINESS_OPERATOR",
            cooperative_id: null
          });
        }
      }
      return created;
    },
    onSuccess: async (created) => {
      setTargetUser(created.object_id);
      setCreatedMessage(profile === "SELLER"
        ? "Учетная запись создана. Право продавца ожидает независимого одобрения аудитора."
        : profile === "PARTICIPANT"
          ? "Учетная запись участника создана. Можно войти и начать обмен."
          : "Техническая учетная запись создана. Назначьте ей только необходимые права.");
      setLoginValue("");
      setTemporaryPassword("");
      await refresh();
    }
  });
  const addRole = useMutation({
    mutationFn: () => assignRole({
      user_id: targetUser,
      role: targetRole,
      cooperative_id: ["SECURITY_ADMIN", "NODE_REGISTRAR", "NODE_TECHNICAL_CUSTODIAN", "NODE_SECURITY_ADMIN", "NODE_BUSINESS_OPERATOR", "NODE_AUDITOR", "AUDITOR", "ARBITRATOR"].includes(targetRole)
        ? null
        : (cooperatives.data?.[0]?.id ?? null)
    }),
    onSuccess: refresh
  });
  const decision = useMutation({ mutationFn: ({ id, approve }: { id: string; approve: boolean }) => decideRole(id, approve), onSuccess: refresh });
  const revoke = useMutation({ mutationFn: revokeSession, onSuccess: refresh });

  if (users.isPending || roles.isPending || sessions.isPending || members.isPending || cooperatives.isPending) return <Loading />;
  if (users.isError || roles.isError || sessions.isError || members.isError || cooperatives.isError) {
    return <ErrorPanel error={users.error ?? roles.error ?? sessions.error ?? members.error ?? cooperatives.error} />;
  }

  const normalizedSearch = accountSearch.trim().toLocaleLowerCase();
  const visibleUsers = (users.data ?? [])
    .filter((item) => !normalizedSearch || item.login.toLocaleLowerCase().includes(normalizedSearch))
    .slice(0, 25);
  const pendingRoles = (roles.data ?? []).filter((item) => item.status === "PENDING_APPROVAL");
  const visibleRoles = (roles.data ?? [])
    .filter((item) => Boolean(targetUser) && item.user_id === targetUser)
    .slice(0, 40);
  const activeSessions = (sessions.data ?? []).filter((item) => item.status === "ACTIVE").slice(0, 20);
  const userName = (userId: string) => users.data?.find((user) => user.id === userId)?.login ?? userId;

  return (
    <div className="view-stack access-view">
      <header className="view-header"><div><span className="eyebrow">Доступ</span><h1>Пользователи и права</h1><p>Создайте вход для участника и выберите, что ему разрешено делать</p></div></header>

      <section className="panel onboarding-panel">
        <div className="panel-heading"><h2>Новая учетная запись</h2><span>Шаг 1 из 2</span></div>
        <form className="onboarding-form" onSubmit={(event) => { event.preventDefault(); setCreatedMessage(""); addUser.mutate(); }}>
          <label>Для кого<select value={memberId} onChange={(event) => setMemberId(event.target.value)} disabled={profile === "TECHNICAL"} required={profile !== "TECHNICAL"}><option value="">Выберите участника</option>{members.data?.filter((item) => item.status === "ACTIVE").map((item) => <option value={item.id} key={item.id}>{item.display_name}</option>)}</select></label>
          <label>Что сможет делать<select value={profile} onChange={(event) => setProfile(event.target.value as "PARTICIPANT" | "SELLER" | "TECHNICAL")}><option value="PARTICIPANT">Искать и получать товары за паи</option><option value="SELLER">Также предлагать свои товары</option><option value="TECHNICAL">Техническая учетная запись без участника</option></select></label>
          <label>Логин<input autoComplete="off" value={loginValue} onChange={(event) => setLoginValue(event.target.value)} required /></label>
          <label>Временный пароль<input type="password" autoComplete="new-password" minLength={16} value={temporaryPassword} onChange={(event) => setTemporaryPassword(event.target.value)} required /></label>
          <button className="primary-button" type="submit" disabled={addUser.isPending || (profile !== "TECHNICAL" && !memberId)}><Plus size={17} /><span>{addUser.isPending ? "Создаем" : "Создать вход"}</span></button>
        </form>
        {profile === "SELLER" ? <p className="form-note"><ShieldCheck size={16} />Право публиковать предложения включится после одобрения другим администратором.</p> : null}
        {addUser.isError ? <p className="form-error">{errorText(addUser.error)}</p> : null}
        {createdMessage ? <p className="form-success" role="status"><Check size={16} />{createdMessage}</p> : null}
      </section>

      {pendingRoles.length ? <section className="panel pending-access-panel"><div className="panel-heading"><h2>Ожидают независимого решения</h2><span>{pendingRoles.length}</span></div><div className="rows">{pendingRoles.map((item) => <div className="data-row role-row" key={item.id}><strong>{roleNames[item.role_code]}</strong><span>{userName(item.user_id)}</span><Status value={item.status} /><span className="icon-actions"><button title="Одобрить" onClick={() => decision.mutate({ id: item.id, approve: true })}><Check size={16} /></button><button title="Отклонить" onClick={() => decision.mutate({ id: item.id, approve: false })}><X size={16} /></button></span></div>)}</div></section> : null}

      <section className="panel">
        <div className="panel-heading access-heading"><h2>Учетные записи</h2><label>Поиск по логину<input value={accountSearch} onChange={(event) => setAccountSearch(event.target.value)} /></label><span>{visibleUsers.length} из {users.data?.length ?? 0}</span></div>
        <div className="table-wrap"><table><thead><tr><th>Логин</th><th>Участник</th><th>Статус</th><th>Смена пароля</th><th>Последний вход</th></tr></thead><tbody>{visibleUsers.map((item) => <tr key={item.id}><td><strong>{item.login}</strong></td><td>{members.data?.find((member) => member.id === item.member_id)?.display_name ?? "Техническая"}</td><td><Status value={item.status} /></td><td>{item.must_change_password ? "При первом входе" : "Выполнена"}</td><td>{formatLocalDateTime(item.last_login_at)}</td></tr>)}</tbody></table></div>
      </section>

      <section className="panel">
        <div className="panel-heading"><h2>Дополнительные права</h2><span>Шаг 2 из 2</span></div>
        <div className="action-band"><form onSubmit={(event) => { event.preventDefault(); addRole.mutate(); }}><label>Учетная запись<select value={targetUser} onChange={(event) => setTargetUser(event.target.value)} required><option value="">Выберите</option>{users.data?.filter((item) => item.id !== principal.user_id).map((item) => <option key={item.id} value={item.id}>{item.login}</option>)}</select></label><label>Право<select value={targetRole} onChange={(event) => setTargetRole(event.target.value as RoleCode)}>{Object.entries(roleNames).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label><button className="primary-button" type="submit"><Plus size={17} /><span>Назначить</span></button></form>{addRole.isError ? <p className="form-error">{errorText(addRole.error)}</p> : null}</div>
        {visibleRoles.length ? <div className="rows">{visibleRoles.map((item) => <div className="data-row role-row" key={item.id}><strong>{roleNames[item.role_code]}</strong><span>{userName(item.user_id)}</span><Status value={item.status} /></div>)}</div> : <Empty text="Выберите учетную запись, чтобы увидеть ее права" />}
      </section>

      <details className="panel access-details"><summary>Активные сессии ({activeSessions.length})</summary><div className="rows">{activeSessions.map((item) => <div className="data-row role-row" key={item.id}><strong>{userName(item.user_id)}</strong><span>{formatLocalDateTime(item.last_seen_at)}</span><Status value={item.status} /><button className="icon-button" title="Отозвать сессию" onClick={() => revoke.mutate(item.id)}><X size={16} /></button></div>)}</div></details>
    </div>
  );
}
function AuditView() {
  const audit = useQuery({ queryKey: ["audit"], queryFn: getAudit, refetchInterval: 30_000 });
  if (audit.isPending) return <Loading />;
  if (audit.isError) return <ErrorPanel error={audit.error} />;
  return <div className="view-stack"><header className="view-header"><div><span className="eyebrow">Append-only</span><h1>Журнал аудита</h1><p>{audit.data?.length ?? 0} последних событий</p></div></header><section className="panel"><div className="table-wrap"><table><thead><tr><th>Время</th><th>Действие</th><th>Объект</th><th>Результат</th><th>Причина</th></tr></thead><tbody>{audit.data?.map((item) => <tr key={item.id}><td>{formatLocalDateTime(item.occurred_at)}</td><td><strong>{item.action}</strong><small>{item.actor_user_id ?? "system"}</small></td><td>{item.object_type}<small>{item.object_id}</small></td><td><Status value={item.outcome} /></td><td>{item.reason_code ?? "—"}</td></tr>)}</tbody></table></div></section></div>;
}

function Status({ value }: { value: string }) {
  const kind = ["ACTIVE", "UP", "SUCCESS"].includes(value) ? "good" : ["REVOKED", "REJECTED", "DOWN", "FAILURE", "DENIED"].includes(value) ? "bad" : "warn";
  return <span className={`status ${kind}`}>{statusNames[value] ?? value}</span>;
}

function Loading() { return <div className="state" role="status"><RefreshCw className="spin" size={24} /><span>Загрузка</span></div>; }
function Empty({ text }: { text: string }) { return <div className="state"><ShieldCheck size={22} /><span>{text}</span></div>; }
function ErrorPanel({ error }: { error: unknown }) { return <div className="state error" role="alert"><Activity size={24} /><strong>{errorText(error)}</strong></div>; }

function Workspace({ session, onLogout }: { session: AuthSession; onLogout: () => void }) {
  const principal = session.principal;
  const available = useMemo(() => {
    const isEverydayParticipant = hasRole(principal, "EXCHANGE_PARTICIPANT") && !hasRole(principal, "MEMBER_REGISTRAR", "COOPERATIVE_ADMIN", "DATA_STEWARD", "RISK_ADMIN", "SECURITY_ADMIN", "AUDITOR", "NODE_REGISTRAR", "NODE_TECHNICAL_CUSTODIAN", "NODE_SECURITY_ADMIN", "NODE_AUDITOR");
    if (isEverydayParticipant) return ["memberHome", "discovery", "exchange"] as View[];
    const result: View[] = ["overview"];
    if (hasRole(principal, "MEMBER_REGISTRAR", "COOPERATIVE_ADMIN", "RISK_ADMIN", "DATA_STEWARD")) result.push("members");
    if (hasRole(principal, "SECURITY_ADMIN", "AUDITOR")) result.push("access");
    if (hasRole(principal, "COOPERATIVE_ADMIN", "RISK_ADMIN", "DATA_STEWARD", "SECURITY_ADMIN", "AUDITOR", "NODE_REGISTRAR")) result.push("responsibility");
    if (principal.member_id || hasRole(principal, "SECURITY_ADMIN", "AUDITOR", "NODE_BUSINESS_OPERATOR", "NODE_AUDITOR")) result.push("discovery");
    if (principal.member_id || hasRole(principal, "SECURITY_ADMIN", "AUDITOR")) result.push("exchange");
    if (principal.member_id || hasRole(principal, "CLEARING_OPERATOR", "CLEARING_CONTROLLER", "CLEARING_FINALIZER", "COOPERATIVE_ADMIN", "SECURITY_ADMIN", "AUDITOR")) result.push("clearing");
    if (hasRole(principal, "CLEARING_OPERATOR", "CLEARING_CONTROLLER", "CLEARING_FINALIZER", "RISK_ADMIN", "NODE_BUSINESS_OPERATOR", "NODE_AUDITOR", "SECURITY_ADMIN", "AUDITOR")) result.push("federatedClearing");
    if (principal.member_id || hasRole(principal, "COOPERATIVE_ADMIN", "RISK_ADMIN", "SECURITY_ADMIN", "AUDITOR")) result.push("risk");
    if (hasRole(principal, "RISK_ADMIN", "SECURITY_ADMIN", "AUDITOR")) result.push("antifraud");
    if (principal.member_id || hasRole(principal, "COOPERATIVE_ADMIN", "RISK_ADMIN", "SECURITY_ADMIN", "AUDITOR", "ARBITRATOR")) result.push("trust");
    if (principal.member_id || hasRole(principal, "SOLIDARITY_OPERATOR", "SOLIDARITY_CONTROLLER", "COOPERATIVE_ADMIN", "SECURITY_ADMIN", "AUDITOR")) result.push("solidarity");
    if (principal.member_id || hasRole(principal, "CRISIS_OPERATOR", "CRISIS_CONTROLLER", "INVENTORY_CONTROLLER", "COOPERATIVE_ADMIN", "SECURITY_ADMIN", "AUDITOR")) result.push("crisis");
    if (hasRole(principal, "COOPERATIVE_ADMIN", "DATA_STEWARD", "RISK_ADMIN", "SECURITY_ADMIN", "AUDITOR", "WAREHOUSE_CUSTODIAN", "INVENTORY_CONTROLLER", "LOGISTICS_OPERATOR")) result.push("inventory");
    if (hasRole(principal, "COOPERATIVE_ADMIN", "RIGHTS_OPERATOR", "RISK_ADMIN", "SECURITY_ADMIN", "AUDITOR", "WAREHOUSE_CUSTODIAN")) result.push("rights");
    if (hasRole(principal, "SECURITY_ADMIN", "AUDITOR", "NODE_REGISTRAR", "NODE_TECHNICAL_CUSTODIAN", "NODE_SECURITY_ADMIN", "NODE_BUSINESS_OPERATOR", "NODE_AUDITOR")) result.push("federation");
    if (hasRole(principal, "COOPERATIVE_ADMIN", "SECURITY_ADMIN", "AUDITOR")) result.push("operations");
    if (hasRole(principal, "SECURITY_ADMIN", "AUDITOR")) result.push("audit");
    return result;
  }, [principal]);
  const [view, setView] = useState<View>(available[0] ?? "overview");
  const [discoverySection, setDiscoverySection] = useState<"search" | "sell" | "intents">("search");
  const navigateParticipant = (target: "discovery" | "exchange", section?: "search" | "sell" | "intents") => {
    if (target === "discovery" && section) setDiscoverySection(section);
    setView(target);
  };
  const navigationRef = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!window.matchMedia?.("(max-width: 760px)").matches) return;
    navigationRef.current?.querySelector<HTMLButtonElement>("button.active")?.scrollIntoView({
      block: "nearest",
      inline: "center"
    });
  }, [view]);
  const nav = [
    ["memberHome", "Главная", LayoutDashboard],
    ["overview", "Обзор", LayoutDashboard],
    ["members", "Участники", Users],
    ["access", "Доступ", UserCog],
    ["responsibility", "Ответственность", Fingerprint],
    ["discovery", "Рынок", ShoppingCart],
    ["exchange", "Сделки", Handshake],
    ["clearing", "Клиринг", Calculator],
    ["federatedClearing", "Межузловой клиринг", Waypoints],
    ["risk", "Риск и паи", ShieldAlert],
    ["antifraud", "Проверка аномалий", ScanSearch],
    ["trust", "Споры", Scale],
    ["solidarity", "Помощь", HandHeart],
    ["crisis", "Кризис", Siren],
    ["inventory", "Склад", PackageSearch],
    ["rights", "Товарные права", FileKey2],
    ["federation", "Узлы и offline", Network],
    ["operations", "Эксплуатация", Activity],
    ["audit", "Аудит", ClipboardList]
  ] as const;
  return <div className="admin-shell"><aside className="admin-sidebar"><div className="brand"><img src="/mark.svg" width="36" height="36" alt="" /><div><strong>Cooperative Clearing</strong><span>Локальный узел</span></div></div><nav ref={navigationRef} aria-label="Основная навигация">{nav.filter(([key]) => available.includes(key)).map(([key, label, Icon]) => <button aria-current={view === key ? "page" : undefined} className={view === key ? "active" : ""} onClick={() => { if (key === "discovery") setDiscoverySection("search"); setView(key); }} key={key}><Icon size={18} /><span>{label}</span></button>)}</nav><div className="operator"><ShieldCheck size={17} /><div><strong>{principal.login}</strong><span>{principal.roles.map((item) => roleNames[item.role]).join(", ")}</span></div><button title="Выйти" onClick={onLogout}><LogOut size={17} /></button></div></aside><main className="admin-main"><div className="admin-topbar"><InterfaceControls placement="topbar" /></div><Suspense fallback={<Loading />}>{view === "memberHome" ? <MemberHomeView onNavigate={navigateParticipant} /> : view === "overview" ? <Overview /> : view === "members" ? <MembersView principal={principal} /> : view === "access" ? <AccessView principal={principal} /> : view === "responsibility" ? <ResponsibilityView principal={principal} /> : view === "discovery" ? <DiscoveryView principal={principal} initialSection={discoverySection} /> : view === "exchange" ? <ExchangeView principal={principal} /> : view === "clearing" ? <ClearingView principal={principal} /> : view === "federatedClearing" ? <FederatedClearingView principal={principal} /> : view === "risk" ? <RiskView principal={principal} /> : view === "antifraud" ? <AntifraudView principal={principal} /> : view === "trust" ? <TrustView principal={principal} /> : view === "solidarity" ? <SolidarityView principal={principal} /> : view === "crisis" ? <CrisisView principal={principal} /> : view === "inventory" ? <InventoryView principal={principal} /> : view === "rights" ? <RightsView principal={principal} /> : view === "federation" ? <FederationView principal={principal} /> : view === "operations" ? <OperationsView /> : <AuditView />}</Suspense></main></div>;
}

export default function AdminApp() {
  const [session, setSession] = useState<AuthSession | null>(null);
  const [restoring, setRestoring] = useState(true);
  useEffect(() => { let active = true; void restoreSession().then((value) => { if (active) { setSession(value); setRestoring(false); } }); return () => { active = false; }; }, []);
  const handleLogout = () => { void logout().finally(() => setSession(null)); };
  if (restoring) return <><InterfaceControls placement="floating" /><main className="auth-screen"><Loading /></main></>;
  if (!session) return <><InterfaceControls placement="floating" /><LoginView onAuthenticated={setSession} /></>;
  if (session.principal.must_change_password) return <><InterfaceControls placement="floating" /><PasswordChangeView session={session} onChanged={setSession} onLogout={handleLogout} /></>;
  return <Workspace session={session} onLogout={handleLogout} />;
}
