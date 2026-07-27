import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Ban,
  Check,
  KeyRound,
  QrCode as QrCodeIcon,
  RefreshCw,
  ShieldCheck,
  Siren,
  Smartphone,
  UserRoundCog,
  X,
} from "lucide-react";
import QrCode from "qrcode";
import { type FormEvent, useEffect, useMemo, useState } from "react";

import {
  type Principal,
  type RoleCode,
  beginTotpEnrollment,
  confirmTotpEnrollment,
  decideAccountRecovery,
  decideBreakGlass,
  disableTotp,
  getAccountRecoveries,
  getBreakGlassGrants,
  getCooperatives,
  getSecurityState,
  getUsers,
  requestAccountRecovery,
  requestBreakGlass,
  revokeBreakGlass,
  verifyTotpStepUp,
} from "./api/admin";
import { userErrorMessage } from "./shared/api-error";
import { formatLocalDateTime } from "./shared/date-time";
import "./security-center.css";

const CONTROL_ROLES: RoleCode[] = [
  "SECURITY_ADMIN",
  "NODE_SECURITY_ADMIN",
  "NODE_REGISTRAR",
  "AUDITOR",
  "NODE_AUDITOR",
];
type EmergencyRole =
  | "SECURITY_ADMIN"
  | "NODE_SECURITY_ADMIN"
  | "NODE_TECHNICAL_CUSTODIAN"
  | "CRISIS_OPERATOR"
  | "CRISIS_CONTROLLER";

const EMERGENCY_ROLES: EmergencyRole[] = [
  "SECURITY_ADMIN",
  "NODE_SECURITY_ADMIN",
  "NODE_TECHNICAL_CUSTODIAN",
  "CRISIS_OPERATOR",
  "CRISIS_CONTROLLER",
];
const NODE_EMERGENCY_ROLES: EmergencyRole[] = [
  "NODE_SECURITY_ADMIN",
  "NODE_TECHNICAL_CUSTODIAN",
];

const recoveryReasonNames: Record<string, string> = {
  LOST_AUTHENTICATOR: "Утрачен телефон или генератор кодов",
  CREDENTIAL_COMPROMISE: "Подозрение на компрометацию",
  USER_LOCKED_OUT: "Пользователь потерял доступ",
};
const grantReasonNames: Record<string, string> = {
  PRIMARY_OPERATOR_UNAVAILABLE: "Основной оператор недоступен",
  EMERGENCY_NODE_MAINTENANCE: "Аварийное обслуживание узла",
  CRISIS_OPERATION: "Кризисная операция",
};

const emergencyRoleNames: Record<EmergencyRole, string> = {
  SECURITY_ADMIN: "Администратор безопасности",
  NODE_SECURITY_ADMIN: "Администратор безопасности узла",
  NODE_TECHNICAL_CUSTODIAN: "Технический хранитель узла",
  CRISIS_OPERATOR: "Оператор кризисного режима",
  CRISIS_CONTROLLER: "Контролёр кризисного режима",
};

function hasControlRole(principal: Principal): boolean {
  return principal.roles.some((grant) => CONTROL_ROLES.includes(grant.role));
}

function SecurityStatus({ value }: { value: string }) {
  const kind = ["ACTIVE", "EXECUTED"].includes(value)
    ? "good"
    : ["REJECTED", "REVOKED", "EXPIRED"].includes(value)
      ? "bad"
      : "warn";
  return <span className={`status ${kind}`}>{value}</span>;
}

function ErrorMessage({ error }: { error: unknown }) {
  return <p className="form-error" role="alert">{userErrorMessage(error)}</p>;
}

function AccountSecurity() {
  const client = useQueryClient();
  const security = useQuery({ queryKey: ["auth-security"], queryFn: getSecurityState });
  const [currentPassword, setCurrentPassword] = useState("");
  const [rotationCode, setRotationCode] = useState("");
  const [confirmationCode, setConfirmationCode] = useState("");
  const [stepUpCode, setStepUpCode] = useState("");
  const [disablePassword, setDisablePassword] = useState("");
  const [disableCode, setDisableCode] = useState("");
  const [enrollment, setEnrollment] = useState<Awaited<ReturnType<typeof beginTotpEnrollment>> | null>(null);
  const [qrDataUrl, setQrDataUrl] = useState("");

  const refresh = () => client.invalidateQueries({ queryKey: ["auth-security"] });
  const begin = useMutation({
    mutationFn: () => beginTotpEnrollment(currentPassword, rotationCode || undefined),
    onSuccess: (value) => {
      setEnrollment(value);
      setCurrentPassword("");
      setRotationCode("");
      void refresh();
    },
  });
  const confirm = useMutation({
    mutationFn: () => confirmTotpEnrollment(confirmationCode),
    onSuccess: () => {
      setEnrollment(null);
      setConfirmationCode("");
      void refresh();
    },
  });
  const stepUp = useMutation({
    mutationFn: () => verifyTotpStepUp(stepUpCode),
    onSuccess: () => {
      setStepUpCode("");
      void refresh();
    },
  });
  const disable = useMutation({
    mutationFn: () => disableTotp(disablePassword, disableCode, "USER_CONFIRMED_DISABLE"),
    onSuccess: () => {
      setDisablePassword("");
      setDisableCode("");
      setEnrollment(null);
      void refresh();
    },
  });

  useEffect(() => {
    let active = true;
    if (!enrollment) {
      setQrDataUrl("");
      return () => { active = false; };
    }
    void QrCode.toDataURL(enrollment.provisioning_uri, {
      errorCorrectionLevel: "M",
      margin: 2,
      width: 220,
      color: { dark: "#111827", light: "#ffffff" },
    }).then((value) => { if (active) setQrDataUrl(value); });
    return () => { active = false; };
  }, [enrollment]);

  if (security.isPending) return <div className="state"><RefreshCw className="spin" size={22} />Загрузка защиты входа</div>;
  if (security.isError || !security.data) return <div className="state error"><ErrorMessage error={security.error} /></div>;

  return (
    <>
      <section className="panel security-summary">
        <div className="panel-heading"><h2>Защита моей учётной записи</h2><ShieldCheck size={20} /></div>
        <div className="security-metrics">
          <div><span>Код из приложения</span><strong>{security.data.totp_enabled ? "Подключён" : "Не подключён"}</strong></div>
          <div><span>Подтверждение личности</span><strong>{security.data.step_up_active ? "Действует" : "Требуется"}</strong></div>
          <div><span>Аварийные права</span><strong>{security.data.break_glass_grants}</strong></div>
        </div>
        {security.data.step_up_active ? <p className="form-success"><Check size={16} />Личность подтверждена до {formatLocalDateTime(security.data.step_up_expires_at)}</p> : null}
      </section>

      {!security.data.totp_enabled || enrollment ? (
        <section className="panel">
          <div className="panel-heading"><h2>{enrollment ? "Подключите приложение с кодами" : "Подключить второй фактор"}</h2><Smartphone size={20} /></div>
          {!enrollment ? (
            <form className="security-form" onSubmit={(event) => { event.preventDefault(); begin.mutate(); }}>
              <label>Текущий пароль<input type="password" autoComplete="current-password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} required /></label>
              {security.data.totp_enabled ? <label>Текущий шестизначный код<input inputMode="numeric" pattern="[0-9]{6}" maxLength={6} value={rotationCode} onChange={(event) => setRotationCode(event.target.value)} required /></label> : null}
              <button className="primary-button" type="submit" disabled={begin.isPending}><KeyRound size={17} /><span>Начать подключение</span></button>
              {begin.isError ? <ErrorMessage error={begin.error} /> : null}
            </form>
          ) : (
            <div className="totp-enrollment">
              <div className="qr-frame">{qrDataUrl ? <img src={qrDataUrl} width="220" height="220" alt="QR-код для приложения с одноразовыми кодами" /> : <QrCodeIcon size={48} />}</div>
              <div className="enrollment-steps">
                <ol>
                  <li>Откройте приложение с одноразовыми кодами на телефоне.</li>
                  <li>Отсканируйте QR-код или введите ключ вручную.</li>
                  <li>Введите новый шестизначный код для проверки.</li>
                </ol>
                <label>Ключ для ручного ввода<code data-i18n-ignore>{enrollment.secret}</code></label>
                <form className="inline-security-form" onSubmit={(event) => { event.preventDefault(); confirm.mutate(); }}>
                  <label>Шестизначный код<input autoFocus inputMode="numeric" pattern="[0-9]{6}" maxLength={6} value={confirmationCode} onChange={(event) => setConfirmationCode(event.target.value)} required /></label>
                  <button className="primary-button" type="submit" disabled={confirm.isPending}><Check size={17} /><span>Подтвердить</span></button>
                </form>
                {confirm.isError ? <ErrorMessage error={confirm.error} /> : null}
                <small>Настройка действует до {formatLocalDateTime(enrollment.expires_at)}.</small>
              </div>
            </div>
          )}
        </section>
      ) : (
        <section className="panel">
          <div className="panel-heading"><h2>Подтвердить личность для важного действия</h2><KeyRound size={20} /></div>
          <form className="inline-security-form" onSubmit={(event) => { event.preventDefault(); stepUp.mutate(); }}>
            <label>Код из приложения<input inputMode="numeric" pattern="[0-9]{6}" maxLength={6} value={stepUpCode} onChange={(event) => setStepUpCode(event.target.value)} required /></label>
            <button className="primary-button" type="submit" disabled={stepUp.isPending}><ShieldCheck size={17} /><span>Подтвердить на 10 минут</span></button>
          </form>
          {stepUp.isError ? <ErrorMessage error={stepUp.error} /> : null}
          <details className="security-danger"><summary>Отключить второй фактор</summary><form className="security-form" onSubmit={(event) => { event.preventDefault(); disable.mutate(); }}><label>Текущий пароль<input type="password" autoComplete="current-password" value={disablePassword} onChange={(event) => setDisablePassword(event.target.value)} required /></label><label>Код из приложения<input inputMode="numeric" pattern="[0-9]{6}" maxLength={6} value={disableCode} onChange={(event) => setDisableCode(event.target.value)} required /></label><button className="secondary-button" type="submit" disabled={disable.isPending}><Ban size={17} /><span>Отключить</span></button></form>{disable.isError ? <ErrorMessage error={disable.error} /> : null}</details>
        </section>
      )}
    </>
  );
}

function SecurityAdministration({ principal }: { principal: Principal }) {
  const client = useQueryClient();
  const users = useQuery({ queryKey: ["users"], queryFn: getUsers });
  const cooperatives = useQuery({ queryKey: ["cooperatives"], queryFn: getCooperatives });
  const recoveries = useQuery({ queryKey: ["account-recoveries"], queryFn: getAccountRecoveries });
  const grants = useQuery({ queryKey: ["break-glass"], queryFn: getBreakGlassGrants });
  const [recoveryUser, setRecoveryUser] = useState("");
  const [temporaryPassword, setTemporaryPassword] = useState("");
  const [recoveryReason, setRecoveryReason] = useState("LOST_AUTHENTICATOR");
  const [recoveryEvidence, setRecoveryEvidence] = useState("");
  const [grantUser, setGrantUser] = useState("");
  const [grantRole, setGrantRole] = useState<EmergencyRole>("CRISIS_OPERATOR");
  const [grantCooperative, setGrantCooperative] = useState("");
  const [grantDuration, setGrantDuration] = useState(30);
  const [grantReason, setGrantReason] = useState("PRIMARY_OPERATOR_UNAVAILABLE");
  const [grantEvidence, setGrantEvidence] = useState("");

  const refresh = () => Promise.all([
    client.invalidateQueries({ queryKey: ["account-recoveries"] }),
    client.invalidateQueries({ queryKey: ["break-glass"] }),
  ]);
  const createRecovery = useMutation({
    mutationFn: () => requestAccountRecovery({
      target_user_id: recoveryUser,
      temporary_password: temporaryPassword,
      reason_code: recoveryReason,
      evidence_id: recoveryEvidence,
    }),
    onSuccess: () => {
      setTemporaryPassword("");
      setRecoveryEvidence("");
      void refresh();
    },
  });
  const recoveryDecision = useMutation({
    mutationFn: ({ id, approve }: { id: string; approve: boolean }) =>
      decideAccountRecovery(id, approve, approve ? "INDEPENDENT_RECOVERY_REVIEW" : "RECOVERY_REJECTED"),
    onSuccess: refresh,
  });
  const createGrant = useMutation({
    mutationFn: () => requestBreakGlass({
      target_user_id: grantUser,
      role: grantRole,
      cooperative_id: NODE_EMERGENCY_ROLES.includes(grantRole) ? null : grantCooperative,
      duration_minutes: grantDuration,
      reason_code: grantReason,
      evidence_id: grantEvidence,
    }),
    onSuccess: () => {
      setGrantEvidence("");
      void refresh();
    },
  });
  const grantDecision = useMutation({
    mutationFn: ({ id, approve }: { id: string; approve: boolean }) =>
      decideBreakGlass(id, approve, approve ? "INCIDENT_CONFIRMED" : "EMERGENCY_ACCESS_REJECTED"),
    onSuccess: refresh,
  });
  const revokeGrant = useMutation({
    mutationFn: (id: string) => revokeBreakGlass(id, "EMERGENCY_ENDED"),
    onSuccess: refresh,
  });

  const userName = (id: string) => users.data?.find((item) => item.id === id)?.login ?? id;
  const pendingRecoveries = useMemo(() => recoveries.data?.filter((item) => item.status === "PENDING_APPROVAL") ?? [], [recoveries.data]);
  const visibleGrants = useMemo(() => grants.data?.filter((item) => ["PENDING_APPROVAL", "ACTIVE"].includes(item.status)) ?? [], [grants.data]);
  const isLoading = users.isPending || cooperatives.isPending || recoveries.isPending || grants.isPending;
  const loadError = users.error ?? cooperatives.error ?? recoveries.error ?? grants.error;

  if (isLoading) return <div className="state"><RefreshCw className="spin" size={22} />Загрузка управления доступом</div>;
  if (loadError) return <div className="state error"><ErrorMessage error={loadError} /></div>;

  return (
    <>
      <section className="panel">
        <div className="panel-heading"><h2>Восстановление доступа двумя сотрудниками</h2><UserRoundCog size={20} /></div>
        <p className="section-intro">Один сотрудник создаёт запрос, другой независимо подтверждает его. Старые сеансы и второй фактор пользователя будут отозваны.</p>
        <form className="security-admin-form" onSubmit={(event: FormEvent) => { event.preventDefault(); createRecovery.mutate(); }}>
          <label>Пользователь<select value={recoveryUser} onChange={(event) => setRecoveryUser(event.target.value)} required><option value="">Выберите пользователя</option>{users.data?.filter((item) => item.id !== principal.user_id).map((item) => <option key={item.id} value={item.id}>{item.login}</option>)}</select></label>
          <label>Временный пароль<input type="password" autoComplete="new-password" minLength={16} value={temporaryPassword} onChange={(event) => setTemporaryPassword(event.target.value)} required /></label>
          <label>Причина<select value={recoveryReason} onChange={(event) => setRecoveryReason(event.target.value)}>{Object.entries(recoveryReasonNames).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
          <label>Номер акта или доказательства<input value={recoveryEvidence} onChange={(event) => setRecoveryEvidence(event.target.value)} minLength={2} required /></label>
          <button className="primary-button" type="submit" disabled={createRecovery.isPending}><UserRoundCog size={17} /><span>Создать запрос</span></button>
        </form>
        {createRecovery.isError ? <ErrorMessage error={createRecovery.error} /> : null}
        <div className="security-workflow-list">{pendingRecoveries.map((item) => <div className="security-workflow-row" key={item.id}><div><strong>{userName(item.target_user_id)}</strong><small>{recoveryReasonNames[item.reason_code] ?? item.reason_code} · {item.evidence_id}</small></div><SecurityStatus value={item.status} /><time>{formatLocalDateTime(item.expires_at)}</time>{item.requested_by_user_id !== principal.user_id && item.target_user_id !== principal.user_id ? <span className="icon-actions"><button title="Одобрить восстановление" onClick={() => recoveryDecision.mutate({ id: item.id, approve: true })}><Check size={16} /></button><button title="Отклонить восстановление" onClick={() => recoveryDecision.mutate({ id: item.id, approve: false })}><X size={16} /></button></span> : <small>Нужен другой сотрудник</small>}</div>)}</div>
        {recoveryDecision.isError ? <ErrorMessage error={recoveryDecision.error} /> : null}
      </section>

      <section className="panel">
        <div className="panel-heading"><h2>Временный аварийный доступ</h2><Siren size={20} /></div>
        <p className="section-intro">Право ограничено задачей и временем. Оно не становится постоянным, а каждое использование попадает в журнал аудита.</p>
        <form className="security-admin-form" onSubmit={(event: FormEvent) => { event.preventDefault(); createGrant.mutate(); }}>
          <label>Пользователь<select value={grantUser} onChange={(event) => setGrantUser(event.target.value)} required><option value="">Выберите пользователя</option>{users.data?.filter((item) => item.id !== principal.user_id).map((item) => <option key={item.id} value={item.id}>{item.login}</option>)}</select></label>
          <label>Временное право<select value={grantRole} onChange={(event) => setGrantRole(event.target.value as EmergencyRole)}>{EMERGENCY_ROLES.map((role) => <option value={role} key={role}>{emergencyRoleNames[role]}</option>)}</select></label>
          {!NODE_EMERGENCY_ROLES.includes(grantRole) ? <label>Кооператив<select value={grantCooperative} onChange={(event) => setGrantCooperative(event.target.value)} required><option value="">Выберите кооператив</option>{cooperatives.data?.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label> : null}
          <label>Срок, минут<input type="number" min={15} max={60} step={15} value={grantDuration} onChange={(event) => setGrantDuration(Number(event.target.value))} required /></label>
          <label>Причина<select value={grantReason} onChange={(event) => setGrantReason(event.target.value)}>{Object.entries(grantReasonNames).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
          <label>Номер инцидента или акта<input value={grantEvidence} onChange={(event) => setGrantEvidence(event.target.value)} minLength={2} required /></label>
          <button className="primary-button" type="submit" disabled={createGrant.isPending}><Siren size={17} /><span>Запросить временное право</span></button>
        </form>
        {createGrant.isError ? <ErrorMessage error={createGrant.error} /> : null}
        <div className="security-workflow-list">{visibleGrants.map((item) => <div className="security-workflow-row" key={item.id}><div><strong>{userName(item.target_user_id)} · {emergencyRoleNames[item.role_code as EmergencyRole] ?? item.role_code}</strong><small>{grantReasonNames[item.reason_code] ?? item.reason_code} · {item.evidence_id}</small></div><SecurityStatus value={item.status} /><time>{formatLocalDateTime(item.expires_at)}</time>{item.status === "PENDING_APPROVAL" && item.requested_by_user_id !== principal.user_id && item.target_user_id !== principal.user_id ? <span className="icon-actions"><button title="Одобрить временное право" onClick={() => grantDecision.mutate({ id: item.id, approve: true })}><Check size={16} /></button><button title="Отклонить временное право" onClick={() => grantDecision.mutate({ id: item.id, approve: false })}><X size={16} /></button></span> : item.status === "ACTIVE" ? <button className="icon-button" title="Отозвать временное право" onClick={() => revokeGrant.mutate(item.id)}><Ban size={16} /></button> : <small>Нужен другой сотрудник</small>}</div>)}</div>
        {(grantDecision.isError || revokeGrant.isError) ? <ErrorMessage error={grantDecision.error ?? revokeGrant.error} /> : null}
      </section>
    </>
  );
}

export default function SecurityCenter({ principal }: { principal: Principal }) {
  return (
    <div className="view-stack security-center">
      <header className="view-header"><div><span className="eyebrow">Безопасность</span><h1>Вход и аварийный доступ</h1><p>Подтвердите личность перед важными действиями и управляйте восстановлением без скрытых полномочий.</p></div></header>
      <AccountSecurity />
      {hasControlRole(principal) ? <SecurityAdministration principal={principal} /> : null}
    </div>
  );
}
