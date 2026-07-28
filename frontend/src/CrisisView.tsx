import { AlertTriangle, ArchiveRestore, BadgeCheck, ClipboardList, FileInput, Gauge, PackageCheck, RefreshCw, Siren, TimerReset } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useMemo, useState } from "react";

import { AdminApiError, type Principal, type RoleCode } from "./api/admin";
import {
  activateCrisisMandate, approveRationingRule, approveReserveTarget, cancelRationingPlan,
  closeCrisisMandate, confirmRationingPlan, getCrisisControllerWorkspace, getCrisisMandates,
  getCrisisOperatorWorkspace, getCrisisPaperForms, getCrisisReports, getCrisisReviews,
  getRationingAllocations, getRationingPlans, getRationingRules, getReserveSnapshots,
  getReserveTargets, issueCrisisPaperForm, issueRation, previewRationingPlan,
  proposeCrisisMandate, proposeRationingRule, proposeReserveTarget, recordCrisisPaperForm,
  recordReserveSnapshot, reviewCrisisMandate, type CrisisMandate, type CrisisPaperForm,
  type RationingAllocation, type RationingPlan, type RationingRule,
} from "./api/crisis";
import { getInventoryMembers, uploadEvidence } from "./api/inventory";
import { userErrorMessage } from "./shared/api-error";
import { formatLocalDateTime } from "./shared/date-time";
import { formatDecimal } from "./shared/decimal";
import "./crisis.css";

type Section = "reserves" | "mandates" | "rationing" | "paper" | "reports";
type RunAction = (action: () => Promise<unknown>) => void;

const statusNames: Record<string, string> = {
  DRAFT: "Черновик", ACTIVE: "Активен", CLOSED: "Закрыт", EXPIRED: "Истёк",
  RETIRED: "Заменён", PREVIEWED: "Предпросмотр", CONFIRMED: "Подтверждён",
  CANCELLED: "Отменён", PROPOSED: "Предложено", RESERVED: "Зарезервировано",
  ISSUED: "Выдано", RECORDED: "Введено", NORMAL: "Норма", WARNING: "Внимание",
  CRITICAL: "Критично", UNKNOWN: "Нет доверия",
};

function hasRole(principal: Principal, ...roles: RoleCode[]) {
  return principal.roles.some((grant) => roles.includes(grant.role));
}

function errorText(error: unknown): string {
  return userErrorMessage(error);
}

function Status({ value }: { value: string }) {
  const kind = ["ACTIVE", "CLOSED", "CONFIRMED", "ISSUED", "RECORDED", "NORMAL"].includes(value)
    ? "good"
    : ["EXPIRED", "CANCELLED", "CRITICAL", "UNKNOWN"].includes(value) ? "bad" : "warn";
  return <span className={`status ${kind}`}>{statusNames[value] ?? value}</span>;
}

function quantity(value: string | null) {
  return value === null ? "—" : formatDecimal(value, "ru-RU", { maximumFractionDigits: 12 });
}

function localInput(date: Date) {
  const shifted = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return shifted.toISOString().slice(0, 16);
}

function evidenceIds(value: File | null, cooperativeId: string) {
  if (!value) return Promise.reject(new AdminApiError("EVIDENCE_REQUIRED", null, 422));
  return uploadEvidence(cooperativeId, value, "SOLIDARITY_AID").then((id) => [id]);
}

function ReserveCommands({ principal, cooperativeId, targets, run }: { principal: Principal; cooperativeId: string; targets: Awaited<ReturnType<typeof getReserveTargets>>; run: RunAction }) {
  const [snapshotFile, setSnapshotFile] = useState<File | null>(null);
  const active = targets.filter((item) => item.status === "ACTIVE");
  if (!hasRole(principal, "CRISIS_OPERATOR", "CRISIS_CONTROLLER", "INVENTORY_CONTROLLER")) return null;
  return <section className="crisis-command-band">
    {hasRole(principal, "CRISIS_OPERATOR") ? <form onSubmit={(event) => {
      event.preventDefault(); const data = new FormData(event.currentTarget);
      run(() => proposeReserveTarget({ cooperative_id: cooperativeId, resource_code: data.get("code"), resource_name: data.get("name"), unit_code: data.get("unit"), target_quantity: data.get("target"), critical_minimum: data.get("critical"), warning_coverage_days: data.get("warning_days"), critical_coverage_days: data.get("critical_days"), max_snapshot_age_hours: Number(data.get("max_age")), terms: { verified_stock_only: true } }));
    }}>
      <strong>Норматив резерва</strong>
      <label>Код<input name="code" required defaultValue="FOOD_RESERVE" /></label>
      <label>Ресурс<input name="name" required defaultValue="Продовольственный резерв" /></label>
      <label>Единица<input name="unit" required defaultValue="KG" /></label>
      <label>Цель<input name="target" required type="number" min="0.000001" step="any" defaultValue="100" /></label>
      <label>Критический минимум<input name="critical" required type="number" min="0" step="any" defaultValue="20" /></label>
      <label>Порог внимания, дней<input name="warning_days" required type="number" min="0" step="any" defaultValue="10" /></label>
      <label>Критический порог, дней<input name="critical_days" required type="number" min="0" step="any" defaultValue="3" /></label>
      <label>Срок снимка, часов<input name="max_age" required type="number" min="1" max="720" defaultValue="24" /></label>
      <button className="primary-button" type="submit"><Gauge size={15} />Создать</button>
    </form> : null}
    {hasRole(principal, "CRISIS_CONTROLLER", "INVENTORY_CONTROLLER") && active.length ? <form onSubmit={(event) => {
      event.preventDefault(); const data = new FormData(event.currentTarget); const targetId = String(data.get("target_id"));
      run(async () => recordReserveSnapshot({ target_id: targetId, physical_verified_quantity: data.get("verified"), committed_quantity: data.get("committed"), consumption_rate_per_day: data.get("rate"), expiring_quantity: data.get("expiring"), quality_status: data.get("quality"), confidence: data.get("confidence"), observed_at: new Date().toISOString(), evidence_ids: await evidenceIds(snapshotFile, cooperativeId) }));
    }}>
      <strong>Проверенный снимок</strong>
      <label className="wide-field">Норматив<select name="target_id">{active.map((item) => <option value={item.id} key={item.id}>{item.resource_code} · {item.resource_name}</option>)}</select></label>
      <label>Физически проверено<input name="verified" required type="number" min="0" step="any" defaultValue="50" /></label>
      <label>Обязано<input name="committed" required type="number" min="0" step="any" defaultValue="0" /></label>
      <label>Расход в сутки<input name="rate" required type="number" min="0" step="any" defaultValue="10" /></label>
      <label>Истекает<input name="expiring" required type="number" min="0" step="any" defaultValue="0" /></label>
      <label>Качество<select name="quality"><option value="ACCEPTED">Принято</option><option value="DEGRADED">Снижено</option><option value="REJECTED">Отклонено</option></select></label>
      <label>Доверие<input name="confidence" required type="number" min="0" max="1" step="0.01" defaultValue="0.95" /></label>
      <label className="wide-field">Акт проверки<input required type="file" accept="application/pdf,image/jpeg,image/png,image/webp,text/plain" onChange={(event) => setSnapshotFile(event.target.files?.[0] ?? null)} /></label>
      <button className="primary-button" type="submit"><PackageCheck size={15} />Зафиксировать</button>
    </form> : null}
  </section>;
}

function MandateCommands({ principal, cooperativeId, mandates, run }: { principal: Principal; cooperativeId: string; mandates: CrisisMandate[]; run: RunAction }) {
  const [file, setFile] = useState<File | null>(null);
  const now = Date.now();
  if (!hasRole(principal, "CRISIS_OPERATOR", "CRISIS_CONTROLLER", "AUDITOR")) return null;
  return <section className="crisis-command-band">
    {hasRole(principal, "CRISIS_OPERATOR") ? <form onSubmit={(event) => {
      event.preventDefault(); const data = new FormData(event.currentTarget);
      run(async () => proposeCrisisMandate({ cooperative_id: cooperativeId, mandate_code: data.get("code"), crisis_type: data.get("type"), scope_payload: { territory: data.get("territory") }, capabilities: ["ENABLE_RATIONING", "ENABLE_PAPER_FORMS", "ENHANCED_AUDIT"], evidence_ids: await evidenceIds(file, cooperativeId), rationale: data.get("rationale"), exit_criteria: data.get("exit"), safe_state: data.get("safe"), starts_at: new Date(String(data.get("starts"))).toISOString(), review_at: new Date(String(data.get("review"))).toISOString(), expires_at: new Date(String(data.get("expires"))).toISOString(), maximum_end_at: new Date(String(data.get("maximum"))).toISOString() }));
    }}>
      <strong>Кризисный мандат</strong>
      <label>Код<input name="code" required defaultValue="CRISIS-001" /></label>
      <label>Основание<select name="type"><option value="PAYMENT_FAILURE">Платёжный сбой</option><option value="CRITICAL_SHORTAGE">Дефицит</option><option value="CONNECTIVITY_LOSS">Потеря связи</option><option value="ENERGY_FAILURE">Энергосбой</option><option value="LOGISTICS_FAILURE">Логистический сбой</option><option value="WAREHOUSE_INCIDENT">Инцидент склада</option></select></label>
      <label>Территория<input name="territory" required defaultValue="local-node" /></label>
      <label>Начало<input name="starts" type="datetime-local" required defaultValue={localInput(new Date(now + 60_000))} /></label>
      <label>Review<input name="review" type="datetime-local" required defaultValue={localInput(new Date(now + 6 * 3_600_000))} /></label>
      <label>Истечение<input name="expires" type="datetime-local" required defaultValue={localInput(new Date(now + 24 * 3_600_000))} /></label>
      <label>Предел<input name="maximum" type="datetime-local" required defaultValue={localInput(new Date(now + 48 * 3_600_000))} /></label>
      <label className="wide-field">Основание решения<textarea name="rationale" required defaultValue="Подтверждённый сбой требует временного ограниченного режима." /></label>
      <label>Критерий выхода<input name="exit" required defaultValue="Все временные операции сверены." /></label>
      <label>Безопасное состояние<input name="safe" required defaultValue="Временные полномочия прекращаются." /></label>
      <label className="wide-field">Доказательство<input required type="file" accept="application/pdf,image/jpeg,image/png,image/webp,text/plain" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></label>
      <button className="primary-button" type="submit"><Siren size={15} />Предложить</button>
    </form> : null}
    {hasRole(principal, "CRISIS_CONTROLLER", "AUDITOR") ? <div className="crisis-action-list">
      {mandates.filter((item) => item.status === "DRAFT").map((item) => <article key={item.id}><div><strong>{item.mandate_code}</strong><span>{item.crisis_type}</span></div><button className="compact-command" onClick={() => run(() => activateCrisisMandate(item))}><BadgeCheck size={14} />Активировать</button></article>)}
      {mandates.filter((item) => item.status === "ACTIVE").map((item) => <article key={item.id}><div><strong>{item.mandate_code}</strong><span>Review {formatLocalDateTime(item.review_at)}</span></div><button className="compact-command" onClick={() => run(() => reviewCrisisMandate(item, { decision: "CONTINUE", facts_payload: { reviewed_in_gui: true }, rationale: "Независимый review подтверждает сохранение исходного срока.", new_review_at: new Date(Date.now() + 3_600_000).toISOString(), new_expires_at: null }))}><TimerReset size={14} />Review</button><button className="compact-command" onClick={() => run(() => closeCrisisMandate(item, "Все временные операции и бумажные формы сверены."))}><ArchiveRestore size={14} />Закрыть</button></article>)}
    </div> : null}
  </section>;
}

function AllocationAction({ item, cooperativeId, run }: { item: RationingAllocation; cooperativeId: string; run: RunAction }) {
  const [file, setFile] = useState<File | null>(null);
  if (item.status !== "RESERVED") return null;
  return <div className="inline-evidence-action"><input aria-label="Акт выдачи" type="file" accept="application/pdf,image/jpeg,image/png,image/webp,text/plain" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /><button title="Подтвердить выдачу" onClick={() => run(async () => issueRation(item.id, "Выдача подтверждена актом и не создаёт долг.", await evidenceIds(file, cooperativeId)))}><PackageCheck size={15} /></button></div>;
}

function PaperRecordAction({ item, run }: { item: CrisisPaperForm; run: RunAction }) {
  const [note, setNote] = useState("Бумажный оригинал сверен и сохранён.");
  if (item.status !== "ISSUED") return null;
  return <div className="paper-record-action"><input value={note} onChange={(event) => setNote(event.target.value)} /><button title="Ввести форму" onClick={() => run(() => recordCrisisPaperForm(item.id, item.checksum, { note, paper_original_retained: true }))}><FileInput size={15} /></button></div>;
}

export default function CrisisView({ principal }: { principal: Principal }) {
  const queryClient = useQueryClient();
  const [section, setSection] = useState<Section>("reserves");
  const cooperativeId = principal.roles.find((grant) => grant.cooperative_id)?.cooperative_id ?? "";
  const targets = useQuery({ queryKey: ["crisis", "targets"], queryFn: getReserveTargets });
  const snapshots = useQuery({ queryKey: ["crisis", "snapshots"], queryFn: () => getReserveSnapshots() });
  const mandates = useQuery({ queryKey: ["crisis", "mandates"], queryFn: getCrisisMandates });
  const reviews = useQuery({ queryKey: ["crisis", "reviews"], queryFn: () => getCrisisReviews() });
  const rules = useQuery({ queryKey: ["crisis", "rules"], queryFn: () => getRationingRules() });
  const plans = useQuery({ queryKey: ["crisis", "plans"], queryFn: () => getRationingPlans() });
  const allocations = useQuery({ queryKey: ["crisis", "allocations"], queryFn: () => getRationingAllocations() });
  const forms = useQuery({ queryKey: ["crisis", "forms"], queryFn: () => getCrisisPaperForms() });
  const reports = useQuery({ queryKey: ["crisis", "reports"], queryFn: getCrisisReports });
  const members = useQuery({ queryKey: ["crisis", "members"], queryFn: getInventoryMembers });
  const operator = useQuery({ queryKey: ["crisis", "operator"], queryFn: getCrisisOperatorWorkspace, enabled: hasRole(principal, "CRISIS_OPERATOR") });
  const controller = useQuery({ queryKey: ["crisis", "controller"], queryFn: getCrisisControllerWorkspace, enabled: hasRole(principal, "CRISIS_CONTROLLER", "AUDITOR") });
  const mutation = useMutation({ mutationFn: (action: () => Promise<unknown>) => action(), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["crisis"] }) });
  const run: RunAction = (action) => mutation.mutate(action);
  const loading = [targets, snapshots, mandates, reviews, rules, plans, allocations, forms, reports, members].some((item) => item.isLoading);
  const failure = [targets, snapshots, mandates, reviews, rules, plans, allocations, forms, reports, members, operator, controller].find((item) => item.error)?.error ?? mutation.error;
  const latest = useMemo(() => new Map((snapshots.data ?? []).map((item) => [item.target_id, item])), [snapshots.data]);
  const activeMandates = (mandates.data ?? []).filter((item) => item.effective_status === "ACTIVE");
  const activeRules = (rules.data ?? []).filter((item) => item.status === "ACTIVE");
  const tabs = [["reserves", "Резервы", Gauge], ["mandates", "Мандаты", Siren], ["rationing", "Нормирование", ClipboardList], ["paper", "Бумага", FileInput], ["reports", "Отчёты", ArchiveRestore]] as const;

  return <div className="view-stack crisis-view">
    <header className="view-header"><div><span className="eyebrow">Ограниченные полномочия</span><h1>Резервы и кризис</h1><p>{activeMandates.length ? `${activeMandates.length} активный мандат` : "Штатный режим"}</p></div><div className="section-tabs">{tabs.map(([key, label, Icon]) => <button className={section === key ? "active" : ""} onClick={() => setSection(key)} key={key}><Icon size={16} /><span>{label}</span></button>)}</div></header>
    <section className="metric-grid crisis-metrics"><div className="metric"><Gauge /><span>Ресурсы<strong>{targets.data?.filter((item) => item.status === "ACTIVE").length ?? 0}</strong></span></div><div className="metric"><AlertTriangle /><span>Критические<strong>{snapshots.data?.filter((item) => item.reserve_level === "CRITICAL").length ?? 0}</strong></span></div><div className="metric"><Siren /><span>Мандаты<strong>{activeMandates.length}</strong></span></div><div className="metric"><ClipboardList /><span>Назначения<strong>{allocations.data?.filter((item) => item.status === "RESERVED").length ?? 0}</strong></span></div><div className="metric"><FileInput /><span>Бумажная очередь<strong>{forms.data?.filter((item) => item.status === "ISSUED").length ?? 0}</strong></span></div></section>
    {failure ? <p className="crisis-error" role="alert">{errorText(failure)}</p> : null}
    {loading ? <div className="state"><RefreshCw className="spin" /><span>Загрузка</span></div> : null}

    {!loading && section === "reserves" ? <><ReserveCommands principal={principal} cooperativeId={cooperativeId} targets={targets.data ?? []} run={run} />{hasRole(principal, "CRISIS_CONTROLLER") && controller.data?.draft_targets.length ? <section className="panel"><header className="panel-heading"><div><strong>На утверждение</strong><span>{controller.data.draft_targets.length}</span></div></header><div className="crisis-action-list">{controller.data.draft_targets.map((item) => <article key={item.id}><div><strong>{item.resource_code}</strong><span>{item.resource_name}</span></div><button className="compact-command" onClick={() => run(() => approveReserveTarget(item))}><BadgeCheck size={14} />Утвердить</button></article>)}</div></section> : null}<section className="panel"><div className="table-wrap"><table className="crisis-table"><thead><tr><th>Ресурс</th><th>Норматив</th><th>Проверено</th><th>Доступно</th><th>Покрытие</th><th>Состояние</th></tr></thead><tbody>{(targets.data ?? []).map((item) => { const snapshot = latest.get(item.id); return <tr key={item.id}><td><strong>{item.resource_name}</strong><small>{item.resource_code} · {item.unit_code} · v{item.policy_version}</small></td><td>{quantity(item.target_quantity)}</td><td>{snapshot ? quantity(snapshot.physical_verified_quantity) : "—"}</td><td>{snapshot ? quantity(snapshot.available_quantity) : "—"}</td><td>{snapshot ? `${quantity(snapshot.coverage_days)} дн.` : "—"}</td><td><Status value={snapshot?.reserve_level ?? item.status} /></td></tr>; })}</tbody></table></div></section></> : null}

    {!loading && section === "mandates" ? <><MandateCommands principal={principal} cooperativeId={cooperativeId} mandates={mandates.data ?? []} run={run} /><section className="panel"><div className="table-wrap"><table className="crisis-table"><thead><tr><th>Мандат</th><th>Основание</th><th>Review</th><th>Истечение</th><th>Статус</th></tr></thead><tbody>{(mandates.data ?? []).map((item) => <tr key={item.id}><td><strong>{item.mandate_code}</strong><small>{item.capabilities.join(", ")}</small></td><td>{item.crisis_type}</td><td>{formatLocalDateTime(item.review_at)}</td><td>{formatLocalDateTime(item.expires_at)}</td><td><Status value={item.effective_status ?? item.status} /></td></tr>)}</tbody></table></div></section><section className="panel"><header className="panel-heading"><div><strong>Независимые review</strong><span>{reviews.data?.length ?? 0}</span></div></header><div className="crisis-review-list">{(reviews.data ?? []).map((item) => <article key={item.id}><strong>{item.decision} · раунд {item.decision_round}</strong><span>{item.rationale}</span><time>{formatLocalDateTime(item.created_at)}</time></article>)}</div></section></> : null}

    {!loading && section === "rationing" ? <><section className="crisis-command-band">{hasRole(principal, "CRISIS_OPERATOR") && activeMandates[0] && (targets.data ?? []).some((item) => item.status === "ACTIVE") ? <form onSubmit={(event) => { event.preventDefault(); const data = new FormData(event.currentTarget); run(() => proposeRationingRule({ mandate_id: data.get("mandate"), target_id: data.get("target"), formula: data.get("formula"), eligibility_policy: { active_membership: true }, protected_minimum: data.get("minimum"), maximum_per_member: data.get("maximum"), period_hours: Number(data.get("period")) })); }}><strong>Правило нормирования</strong><label>Мандат<select name="mandate">{activeMandates.map((item) => <option value={item.id} key={item.id}>{item.mandate_code}</option>)}</select></label><label>Ресурс<select name="target">{(targets.data ?? []).filter((item) => item.status === "ACTIVE").map((item) => <option value={item.id} key={item.id}>{item.resource_code}</option>)}</select></label><label>Формула<select name="formula"><option value="EQUAL_PER_MEMBER">Поровну</option><option value="WEIGHTED_PRIORITY">По весу</option></select></label><label>Защищённый минимум<input name="minimum" type="number" min="0" step="any" defaultValue="2" /></label><label>Максимум<input name="maximum" type="number" min="0.000001" step="any" defaultValue="5" /></label><label>Период, часов<input name="period" type="number" min="1" max="720" defaultValue="24" /></label><button className="primary-button" type="submit"><ClipboardList size={15} />Создать</button></form> : null}{hasRole(principal, "CRISIS_OPERATOR") && activeRules[0] && members.data?.length ? <form onSubmit={(event) => { event.preventDefault(); const data = new FormData(event.currentTarget); run(() => previewRationingPlan(String(data.get("rule")), [{ member_id: String(data.get("member")), weight: Number(data.get("weight")) }])); }}><strong>Предпросмотр</strong><label>Правило<select name="rule">{activeRules.map((item) => <option value={item.id} key={item.id}>{item.policy_version} · {item.formula}</option>)}</select></label><label>Участник<select name="member">{members.data.map((item) => <option value={item.member_id} key={item.member_id}>{item.display_name}</option>)}</select></label><label>Вес<input name="weight" type="number" min="1" max="100" defaultValue="1" /></label><button className="primary-button" type="submit"><Gauge size={15} />Рассчитать</button></form> : null}</section>{hasRole(principal, "CRISIS_CONTROLLER") && controller.data?.draft_rules.length ? <section className="panel"><div className="crisis-action-list">{controller.data.draft_rules.map((item) => <article key={item.id}><div><strong>{item.formula}</strong><span>{quantity(item.maximum_per_member)} за период</span></div><button className="compact-command" onClick={() => run(() => approveRationingRule(item))}><BadgeCheck size={14} />Утвердить</button></article>)}</div></section> : null}<section className="panel"><div className="table-wrap"><table className="crisis-table"><thead><tr><th>План</th><th>Доступно</th><th>Распределено</th><th>Получатели</th><th>Статус</th><th>Действия</th></tr></thead><tbody>{(plans.data ?? []).map((item) => <tr key={item.id}><td><code>{item.allocations_hash}</code></td><td>{quantity(item.available_input)}</td><td>{quantity(item.total_allocated)}</td><td>{item.eligible_count}</td><td><Status value={item.status} /></td><td><div className="table-actions">{item.status === "PREVIEWED" && hasRole(principal, "CRISIS_CONTROLLER") ? <button title="Подтвердить" onClick={() => run(() => confirmRationingPlan(item))}><BadgeCheck size={15} /></button> : null}{["PREVIEWED", "CONFIRMED"].includes(item.status) && hasRole(principal, "CRISIS_CONTROLLER") ? <button title="Отменить" onClick={() => run(() => cancelRationingPlan(item, "Отмена оператором после проверки."))}><AlertTriangle size={15} /></button> : null}</div></td></tr>)}</tbody></table></div></section><section className="panel"><div className="table-wrap"><table className="crisis-table"><thead><tr><th>Получатель</th><th>Количество</th><th>Статус</th><th>Выдача</th></tr></thead><tbody>{(allocations.data ?? []).map((item) => <tr key={item.id}><td><code>{item.member_id}</code></td><td>{quantity(item.quantity)}</td><td><Status value={item.status} /></td><td><AllocationAction item={item} cooperativeId={cooperativeId} run={run} /></td></tr>)}</tbody></table></div></section></> : null}

    {!loading && section === "paper" ? <><section className="crisis-command-band">{hasRole(principal, "CRISIS_OPERATOR") && activeMandates.length && members.data?.length ? <form onSubmit={(event) => { event.preventDefault(); const data = new FormData(event.currentTarget); run(() => issueCrisisPaperForm({ mandate_id: data.get("mandate"), serial_number: data.get("serial"), form_type: data.get("type"), assigned_to_member_id: data.get("member"), expires_at: new Date(String(data.get("expires"))).toISOString() })); }}><strong>Нумерованная форма</strong><label>Мандат<select name="mandate">{activeMandates.map((item) => <option value={item.id} key={item.id}>{item.mandate_code}</option>)}</select></label><label>Номер<input name="serial" required defaultValue="PAPER-001" /></label><label>Тип<select name="type"><option value="INCIDENT">Инцидент</option><option value="RATION_ISSUANCE">Выдача</option><option value="RESERVE_SNAPSHOT">Снимок</option><option value="EXCEPTION">Исключение</option></select></label><label>Ответственный<select name="member">{members.data.map((item) => <option value={item.member_id} key={item.member_id}>{item.display_name}</option>)}</select></label><label>Истечение<input name="expires" type="datetime-local" required defaultValue={localInput(new Date(Date.now() + 6 * 3_600_000))} /></label><button className="primary-button" type="submit"><FileInput size={15} />Выдать</button></form> : null}</section><section className="panel"><div className="table-wrap"><table className="crisis-table"><thead><tr><th>Номер</th><th>Тип</th><th>Ответственный</th><th>Истечение</th><th>Статус</th><th>Ввод</th></tr></thead><tbody>{(forms.data ?? []).map((item) => <tr key={item.id}><td><strong>{item.serial_number}</strong><small>{item.checksum}</small></td><td>{item.form_type}</td><td><code>{item.assigned_to_member_id}</code></td><td>{formatLocalDateTime(item.expires_at)}</td><td><Status value={item.status} /></td><td>{hasRole(principal, "CRISIS_CONTROLLER") ? <PaperRecordAction item={item} run={run} /> : null}</td></tr>)}</tbody></table></div></section></> : null}

    {!loading && section === "reports" ? <section className="panel"><header className="panel-heading"><div><strong>Итоговые отчёты</strong><span>{reports.data?.length ?? 0}</span></div></header><div className="crisis-report-list">{(reports.data ?? []).map((item) => <article key={item.id}><header><div><strong>{String(item.report_payload.mandate_code ?? item.mandate_id)}</strong><span>{formatLocalDateTime(item.generated_at)}</span></div><code>{item.report_hash}</code></header><dl><dt>Правила</dt><dd>{String(item.report_payload.rationing_rule_count ?? 0)}</dd><dt>Планы</dt><dd>{String(item.report_payload.rationing_plan_count ?? 0)}</dd><dt>Выдачи</dt><dd>{String(item.report_payload.ration_issuance_count ?? 0)}</dd><dt>Формы</dt><dd>{String(item.report_payload.paper_form_count ?? 0)}</dd></dl></article>)}</div></section> : null}
  </div>;
}
