import {
  Check,
  ClipboardCheck,
  Copy,
  FileKey,
  Link2,
  RefreshCw,
  RotateCcw,
  ShieldAlert,
  ShieldCheck,
  X,
} from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useMemo, useState } from "react";

import {
  AdminApiError,
  getCooperatives,
  type Principal,
  type RoleCode,
} from "./api/admin";
import {
  acceptResponsibility,
  decideResponsibility,
  getJournalIntegrity,
  getOutboxStatus,
  getResponsibilityAssignments,
  getResponsibilityCandidates,
  getSignedEvents,
  previewResponsibility,
  proposeResponsibility,
  type CanonicalPreview,
  type ResponsibilityAssignment,
  type ResponsibilityProposal,
} from "./api/responsibility";
import { userErrorMessage } from "./shared/api-error";
import { formatLocalDateTime } from "./shared/date-time";

type Section = "assignments" | "journal";

const statusNames: Record<string, string> = {
  PENDING_APPROVAL: "Ожидает решения",
  PENDING_ACCEPTANCE: "Ожидает принятия",
  ACTIVE: "Действует",
  REJECTED: "Отклонено",
  RELEASED: "Завершено",
};

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
  CRISIS_CONTROLLER: "Контролёр кризисного режима",
};

const subjectNames: Record<string, string> = {
  warehouse: "Склад",
  warehouse_zone: "Складская зона",
  transport_route: "Маршрут доставки",
  clearing_batch: "Клиринговый пакет",
  public_fund: "Общественный фонд",
  node_link: "Внешний узел",
  offer: "Предложение",
};

function hasRole(principal: Principal, ...roles: RoleCode[]): boolean {
  return principal.roles.some((grant) => roles.includes(grant.role));
}

function errorText(error: unknown): string {
  return userErrorMessage(error);
}

function shortId(value: string | null): string {
  return value ? `${value.slice(0, 8)}…${value.slice(-4)}` : "—";
}

function StatusPill({ value }: { value: string }) {
  const kind = value === "ACTIVE" ? "good" : value === "REJECTED" ? "bad" : "warn";
  return <span className={`status ${kind}`}>{statusNames[value] ?? value}</span>;
}

function AssignmentAction({
  assignment,
  principal,
  onRefresh,
}: {
  assignment: ResponsibilityAssignment;
  principal: Principal;
  onRefresh: () => Promise<unknown>;
}) {
  const decision = useMutation({
    mutationFn: (approve: boolean) => decideResponsibility(assignment.id, approve),
    onSuccess: onRefresh,
  });
  const acceptance = useMutation({
    mutationFn: () => acceptResponsibility(assignment),
    onSuccess: onRefresh,
  });
  const canDecide =
    assignment.status === "PENDING_APPROVAL" &&
    hasRole(principal, "RISK_ADMIN", "AUDITOR") &&
    assignment.created_by_user_id !== principal.user_id &&
    assignment.member_id !== principal.member_id;
  const canAccept =
    assignment.status === "PENDING_ACCEPTANCE" && assignment.member_id === principal.member_id;

  if (canDecide) {
    return (
      <span className="icon-actions">
        <button
          aria-label="Одобрить назначение"
          title="Одобрить"
          disabled={decision.isPending}
          onClick={() => decision.mutate(true)}
        >
          <Check size={16} />
        </button>
        <button
          aria-label="Отклонить назначение"
          title="Отклонить"
          disabled={decision.isPending}
          onClick={() => decision.mutate(false)}
        >
          <X size={16} />
        </button>
      </span>
    );
  }
  if (canAccept) {
    return (
      <button
        className="compact-command"
        disabled={acceptance.isPending}
        onClick={() => acceptance.mutate()}
      >
        <ClipboardCheck size={15} /> <span>Принять</span>
      </button>
    );
  }
  if (decision.isError || acceptance.isError) {
    return <span className="inline-error">{errorText(decision.error ?? acceptance.error)}</span>;
  }
  return <span className="muted-value">—</span>;
}

export default function ResponsibilityView({ principal }: { principal: Principal }) {
  const client = useQueryClient();
  const canPropose = hasRole(principal, "COOPERATIVE_ADMIN", "RISK_ADMIN");
  const canReadJournal = principal.roles.some(
    (grant) =>
      grant.cooperative_id === null &&
      ["RISK_ADMIN", "SECURITY_ADMIN", "AUDITOR", "NODE_REGISTRAR"].includes(grant.role),
  );
  const scopedCooperative = principal.roles.find((grant) => grant.cooperative_id)?.cooperative_id ?? "";
  const [section, setSection] = useState<Section>("assignments");
  const [cooperativeId, setCooperativeId] = useState(scopedCooperative);
  const [candidateId, setCandidateId] = useState("");
  const [subjectType, setSubjectType] = useState("warehouse_zone");
  const [subjectId, setSubjectId] = useState<string>(() => crypto.randomUUID());
  const [scope, setScope] = useState("");
  const [maxExposure, setMaxExposure] = useState("0.0000");
  const [exposureUnit, setExposureUnit] = useState("SHARE_UNIT");
  const [validUntil, setValidUntil] = useState("");
  const [acceptedPreview, setAcceptedPreview] = useState<{
    draftKey: string;
    data: CanonicalPreview;
  } | null>(null);

  const assignments = useQuery({
    queryKey: ["responsibility-assignments"],
    queryFn: getResponsibilityAssignments,
    refetchInterval: 20_000,
  });
  const cooperatives = useQuery({
    queryKey: ["cooperatives"],
    queryFn: getCooperatives,
    enabled: canPropose,
  });
  const candidates = useQuery({
    queryKey: ["responsibility-candidates", cooperativeId],
    queryFn: () => getResponsibilityCandidates(cooperativeId),
    enabled: canPropose && Boolean(cooperativeId),
  });
  const integrity = useQuery({
    queryKey: ["journal-integrity"],
    queryFn: getJournalIntegrity,
    enabled: canReadJournal,
    refetchInterval: 30_000,
  });
  const outbox = useQuery({
    queryKey: ["journal-outbox"],
    queryFn: getOutboxStatus,
    enabled: canReadJournal,
    refetchInterval: 15_000,
  });
  const events = useQuery({
    queryKey: ["signed-events"],
    queryFn: getSignedEvents,
    enabled: canReadJournal,
    refetchInterval: 30_000,
  });

  useEffect(() => {
    if (!cooperativeId && cooperatives.data?.[0]) setCooperativeId(cooperatives.data[0].id);
  }, [cooperativeId, cooperatives.data]);

  useEffect(() => {
    if (candidateId && !candidates.data?.some((item) => item.role_assignment_id === candidateId)) {
      setCandidateId("");
    }
  }, [candidateId, candidates.data]);

  const candidate = candidates.data?.find((item) => item.role_assignment_id === candidateId);
  const proposal = useMemo<ResponsibilityProposal | null>(() => {
    if (!candidate || !cooperativeId) return null;
    return {
      cooperative_id: cooperativeId,
      member_id: candidate.member_id,
      role_assignment_id: candidate.role_assignment_id,
      subject_type: subjectType,
      subject_id: subjectId,
      scope,
      max_exposure: maxExposure,
      exposure_unit: exposureUnit,
      valid_until: validUntil ? new Date(validUntil).toISOString() : null,
    };
  }, [candidate, cooperativeId, exposureUnit, maxExposure, scope, subjectId, subjectType, validUntil]);
  const draftKey = proposal ? JSON.stringify(proposal) : "";
  const previewCurrent = acceptedPreview?.draftKey === draftKey ? acceptedPreview.data : null;

  const refreshAssignments = () =>
    Promise.all([
      client.invalidateQueries({ queryKey: ["responsibility-assignments"] }),
      client.invalidateQueries({ queryKey: ["signed-events"] }),
      client.invalidateQueries({ queryKey: ["journal-integrity"] }),
      client.invalidateQueries({ queryKey: ["journal-outbox"] }),
    ]);
  const preview = useMutation({
    mutationFn: (value: ResponsibilityProposal) => previewResponsibility(value),
    onSuccess: (data) => setAcceptedPreview({ draftKey, data }),
  });
  const propose = useMutation({
    mutationFn: (value: ResponsibilityProposal) => proposeResponsibility(value),
    onSuccess: async () => {
      setAcceptedPreview(null);
      setSubjectId(crypto.randomUUID());
      setScope("");
      await refreshAssignments();
    },
  });

  function requestPreview(event: FormEvent) {
    event.preventDefault();
    if (proposal) preview.mutate(proposal);
  }

  function submitProposal() {
    if (proposal && previewCurrent) {
      propose.mutate({ ...proposal, expected_summary_hash: previewCurrent.summary_hash });
    }
  }

  const totals = useMemo(() => {
    const values = assignments.data ?? [];
    return {
      total: values.length,
      pending: values.filter((item) => item.status.startsWith("PENDING_")).length,
      active: values.filter((item) => item.status === "ACTIVE").length,
      exposure: values
        .filter((item) => item.status === "ACTIVE")
        .reduce((sum, item) => sum + Number(item.max_exposure), 0),
    };
  }, [assignments.data]);

  if (assignments.isPending) {
    return <div className="state"><RefreshCw className="spin" size={24} /><span>Загрузка</span></div>;
  }
  if (assignments.isError) {
    return <div className="state error"><ShieldAlert size={24} /><strong>{errorText(assignments.error)}</strong></div>;
  }

  return (
    <div className="view-stack responsibility-view">
      <header className="view-header">
        <div><span className="eyebrow">Персональная цепочка</span><h1>Ответственность</h1><p>{totals.total} назначений</p></div>
        <div className="section-tabs" role="tablist" aria-label="Раздел ответственности">
          <button role="tab" aria-selected={section === "assignments"} className={section === "assignments" ? "active" : ""} onClick={() => setSection("assignments")}><ClipboardCheck size={16} /><span>Назначения</span></button>
          {canReadJournal ? <button role="tab" aria-selected={section === "journal"} className={section === "journal" ? "active" : ""} onClick={() => setSection("journal")}><Link2 size={16} /><span>Журнал</span></button> : null}
        </div>
      </header>

      {section === "assignments" ? (
        <>
          <section className="metric-grid responsibility-metrics" aria-label="Сводка ответственности">
            <article className="metric"><ClipboardCheck size={18} /><span>Всего</span><strong>{totals.total}</strong></article>
            <article className="metric"><ShieldCheck size={18} /><span>Действуют</span><strong>{totals.active}</strong></article>
            <article className="metric"><ShieldAlert size={18} /><span>Ожидают</span><strong>{totals.pending}</strong></article>
            <article className="metric"><FileKey size={18} /><span>Активный лимит</span><strong>{totals.exposure.toLocaleString("ru-RU")}</strong></article>
          </section>

          {canPropose ? (
            <section className="responsibility-command-band">
              <div className="panel-heading"><h2>Новое назначение</h2><span>RFC8785-JCS-1</span></div>
              <form className="responsibility-form" onSubmit={requestPreview}>
                <label>Кооператив<select value={cooperativeId} onChange={(event) => setCooperativeId(event.target.value)} required>{cooperatives.data?.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label>
                <label>Ответственный<select value={candidateId} onChange={(event) => setCandidateId(event.target.value)} required><option value="">Выберите</option>{candidates.data?.map((item) => <option value={item.role_assignment_id} key={item.role_assignment_id}>{item.display_name} · {roleNames[item.role_code]}</option>)}</select></label>
                <label>Тип объекта<select value={subjectType} onChange={(event) => setSubjectType(event.target.value)}>{Object.entries(subjectNames).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
                <label>Идентификатор объекта<span className="input-with-action"><input value={subjectId} onChange={(event) => setSubjectId(event.target.value)} pattern="[0-9a-fA-F-]{36}" required /><button type="button" title="Новый идентификатор" aria-label="Создать новый идентификатор" onClick={() => setSubjectId(crypto.randomUUID())}><RotateCcw size={15} /></button></span></label>
                <label className="scope-field">Границы ответственности<input value={scope} onChange={(event) => setScope(event.target.value)} maxLength={200} required /></label>
                <label>Предельный объём<input type="number" min="0" max="9999999999999999.9999" step="0.0001" value={maxExposure} onChange={(event) => setMaxExposure(event.target.value)} required /></label>
                <label>Единица<input value={exposureUnit} onChange={(event) => setExposureUnit(event.target.value.toUpperCase())} pattern="[A-Za-z0-9._-]+" maxLength={32} required /></label>
                <label>Действует до<input type="datetime-local" value={validUntil} onChange={(event) => setValidUntil(event.target.value)} /></label>
                <div className="command-buttons">
                  <button className="secondary-button" type="submit" disabled={!proposal || preview.isPending}><FileKey size={16} /><span>Сформировать</span></button>
                  <button className="primary-button" type="button" disabled={!previewCurrent || propose.isPending} onClick={submitProposal}><ShieldCheck size={16} /><span>Создать</span></button>
                </div>
              </form>
              {previewCurrent ? <div className="canonical-preview"><div><strong>Canonical summary</strong><code>{previewCurrent.summary_hash}</code></div><button title="Копировать JSON" aria-label="Копировать canonical JSON" onClick={() => void navigator.clipboard.writeText(previewCurrent.canonical_json)}><Copy size={15} /></button><pre>{previewCurrent.canonical_json}</pre></div> : null}
              {preview.isError || propose.isError ? <p className="form-error">{errorText(preview.error ?? propose.error)}</p> : null}
            </section>
          ) : null}

          <section className="panel">
            <div className="panel-heading"><h2>Назначения</h2><span>{assignments.data?.length ?? 0}</span></div>
            <div className="table-wrap">
              <table className="responsibility-table"><thead><tr><th>Статус</th><th>Ответственный</th><th>Объект</th><th>Границы и лимит</th><th>Этап</th></tr></thead><tbody>
                {assignments.data?.map((item) => {
                  const responsible = candidates.data?.find((value) => value.member_id === item.member_id);
                  return <tr key={item.id}><td><StatusPill value={item.status} /><small>{formatLocalDateTime(item.created_at)}</small></td><td><strong>{responsible?.display_name ?? shortId(item.member_id)}</strong><small>{responsible ? roleNames[responsible.role_code] : shortId(item.role_assignment_id)}</small></td><td><strong>{subjectNames[item.subject_type] ?? item.subject_type}</strong><small>{shortId(item.subject_id)}</small></td><td><strong>{item.max_exposure} {item.exposure_unit}</strong><small>{item.scope}</small></td><td><AssignmentAction assignment={item} principal={principal} onRefresh={refreshAssignments} /></td></tr>;
                })}
              </tbody></table>
            </div>
          </section>
        </>
      ) : (
        <>
          <section className="metric-grid responsibility-metrics" aria-label="Состояние подписанного журнала">
            <article className="metric"><ShieldCheck size={18} /><span>Целостность</span><strong>{integrity.data?.ok ? "OK" : "FAIL"}</strong></article>
            <article className="metric"><Link2 size={18} /><span>События</span><strong>{integrity.data?.checked_events ?? 0}</strong></article>
            <article className="metric"><RefreshCw size={18} /><span>В очереди</span><strong>{outbox.data?.pending ?? 0}</strong></article>
            <article className="metric"><ShieldAlert size={18} /><span>Карантин</span><strong>{outbox.data?.quarantined ?? 0}</strong></article>
          </section>
          {integrity.isError || outbox.isError || events.isError ? <div className="state error"><ShieldAlert size={22} /><strong>{errorText(integrity.error ?? outbox.error ?? events.error)}</strong></div> : null}
          <section className="panel journal-head">
            <div className="panel-heading"><h2>Головка цепочки</h2><StatusPill value={integrity.data?.ok ? "ACTIVE" : "REJECTED"} /></div>
            <div className="evidence-grid"><div><span>Узел</span><code>{integrity.data?.node_id ?? "—"}</code></div><div><span>Последовательность</span><strong>{integrity.data?.last_sequence ?? 0}</strong></div><div><span>Последний hash</span><code>{integrity.data?.last_event_hash ?? "—"}</code></div><div><span>Опубликовано</span><strong>{outbox.data?.published ?? 0}</strong></div></div>
          </section>
          <section className="panel">
            <div className="panel-heading"><h2>Подписанные события</h2><span>{events.data?.length ?? 0}</span></div>
            <div className="table-wrap"><table className="journal-table"><thead><tr><th>Seq</th><th>Событие</th><th>Агрегат</th><th>Hash и подпись</th><th>Время</th></tr></thead><tbody>{events.data?.map((event) => <tr key={event.event_id}><td><strong>#{event.local_sequence}</strong><small>{shortId(event.event_id)}</small></td><td><strong>{event.event_type}</strong><small>v{event.aggregate_version}</small></td><td>{event.aggregate_type}<small>{shortId(event.aggregate_id)}</small></td><td><details><summary>{shortId(event.event_hash)} · {event.signatures[0]?.algorithm ?? "—"}</summary><pre>{event.canonical_json}</pre></details></td><td>{formatLocalDateTime(event.recorded_at)}</td></tr>)}</tbody></table></div>
          </section>
        </>
      )}
    </div>
  );
}
