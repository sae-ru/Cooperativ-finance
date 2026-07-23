import {
  AlertTriangle,
  BadgeCheck,
  Ban,
  Check,
  ClipboardCheck,
  FileWarning,
  Gavel,
  History,
  RefreshCw,
  RotateCcw,
  Scale,
  ShieldAlert,
  ShieldCheck,
  UserRoundCheck,
} from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useMemo, useState } from "react";

import { AdminApiError, type Principal, type RoleCode } from "./api/admin";
import { getInventoryMembers, uploadEvidence, uploadEvidenceProof } from "./api/inventory";
import {
  approveTrustPolicy,
  closeRehabilitationPlan,
  completeRehabilitationStep,
  createRehabilitationPlan,
  decideTrustAppeal,
  declareTrustConflict,
  finalizeTrustSanction,
  getArbitratorWorkspace,
  getAuditorWorkspace,
  getProtectiveMeasures,
  getRehabilitationPlans,
  getRehabilitationSteps,
  getReliabilityProfile,
  getReputationEvents,
  getTrustAppeals,
  getTrustCases,
  getTrustConflicts,
  getTrustDecisions,
  getTrustPolicies,
  getTrustSanctions,
  imposeProtectiveMeasure,
  issueOriginalDecision,
  liftProtectiveMeasure,
  markTrustCaseReady,
  openTrustCase,
  proposeTrustPolicy,
  proposeTrustSanction,
  recordReputationEvent,
  respondToTrustCase,
  submitTrustAppeal,
  type ProtectiveMeasure,
  type RehabilitationPlan,
  type TrustAppeal,
  type TrustCase,
  type TrustDecision,
} from "./api/trust";
import { formatLocalDateTime } from "./shared/date-time";
import "./trust.css";

type Section = "cases" | "appeals" | "measures" | "reputation" | "rehabilitation";
type RunAction = (action: () => Promise<unknown>) => void;

const statusNames: Record<string, string> = {
  OPEN: "Открыто",
  RESPONSE_RECEIVED: "Ответ получен",
  READY_FOR_DECISION: "Готово к решению",
  DECIDED: "Решено",
  UNDER_APPEAL: "На апелляции",
  REMANDED: "Возвращено",
  CLOSED: "Закрыто",
  SUBMITTED: "Подана",
  ACTIVE: "Действует",
  REVOKED: "Отозвано",
  CANCELLED: "Отменено",
  COMPLETED: "Завершено",
  PENDING_APPEAL: "Ожидает апелляцию",
  DISPUTED: "Оспаривается",
  OVERTURNED: "Отменено",
  AFFIRMED: "Подтверждено",
  MODIFIED: "Изменено",
};

const contextNames: Record<string, string> = {
  SUPPLY: "Поставка",
  QUALITY: "Качество",
  STORAGE: "Хранение",
  LOGISTICS: "Логистика",
  SERVICE: "Услуга",
  OBLIGATION: "Обязательство",
  GUARANTEE: "Поручительство",
  WAREHOUSE_CONTROL: "Складской контроль",
  AUDIT: "Аудит",
  ARBITRATION: "Арбитраж",
  FUND_GOVERNANCE: "Управление фондами",
  NODE_SECURITY: "Безопасность узла",
};

function hasRole(principal: Principal, ...roles: RoleCode[]): boolean {
  return principal.roles.some((grant) => roles.includes(grant.role));
}

function errorText(error: unknown): string {
  if (error instanceof AdminApiError) {
    return `${error.code}${error.requestId ? ` · ${error.requestId}` : ""}`;
  }
  return "Операция не выполнена";
}

function Status({ value }: { value: string }) {
  const kind = ["ACTIVE", "CLOSED", "COMPLETED", "AFFIRMED"].includes(value)
    ? "good"
    : ["REVOKED", "CANCELLED", "OVERTURNED", "DISPUTED"].includes(value)
      ? "bad"
      : "warn";
  return <span className={`status ${kind}`}>{statusNames[value] ?? value}</span>;
}

function localInput(value: Date): string {
  const shifted = new Date(value.getTime() - value.getTimezoneOffset() * 60_000);
  return shifted.toISOString().slice(0, 16);
}

function NewCaseForm({
  principal,
  members,
  run,
}: {
  principal: Principal;
  members: Array<{ member_id: string; cooperative_id: string; display_name: string }>;
  run: RunAction;
}) {
  const mine = members.find((item) => item.member_id === principal.member_id);
  const [reference, setReference] = useState("");
  const [subject, setSubject] = useState(principal.member_id ?? "");
  const [summary, setSummary] = useState("");
  const [facts, setFacts] = useState("");
  const [outcome, setOutcome] = useState("");
  const [file, setFile] = useState<File | null>(null);
  if (!mine) return null;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const claimantMemberId = principal.member_id;
    if (!file || !claimantMemberId) return;
    run(async () => {
      const proof = await uploadEvidenceProof(mine.cooperative_id, file, "TRUST_CASE");
      await openTrustCase({
        cooperative_id: mine.cooperative_id,
        case_reference: reference,
        subject_member_id: subject,
        claimant_member_id: claimantMemberId,
        source_type: "OTHER",
        source_reference: `USER-REPORT-${reference}`,
        source_event_ids: [proof.completedEventId],
        evidence_ids: [proof.evidenceId],
        summary,
        facts,
        requested_outcome: outcome,
        confidentiality: "NORMAL",
      });
      setReference("");
      setSummary("");
      setFacts("");
      setOutcome("");
      setFile(null);
    });
  };

  return (
    <section className="trust-command-band">
      <form className="trust-case-form" onSubmit={submit}>
        <label>Номер дела<input value={reference} onChange={(event) => setReference(event.target.value.toUpperCase())} pattern="[A-Za-z0-9._-]+" required /></label>
        <label>Участник<select value={subject} onChange={(event) => setSubject(event.target.value)} required>{members.filter((item) => item.cooperative_id === mine.cooperative_id).map((item) => <option value={item.member_id} key={item.member_id}>{item.display_name}</option>)}</select></label>
        <label>Кратко<input value={summary} onChange={(event) => setSummary(event.target.value)} required /></label>
        <label>Документ<input type="file" accept="application/pdf,image/jpeg,image/png,image/webp,text/plain" onChange={(event) => setFile(event.target.files?.[0] ?? null)} required /></label>
        <label className="span-two">Факты<textarea value={facts} onChange={(event) => setFacts(event.target.value)} required /></label>
        <label className="span-two">Требуемое решение<textarea value={outcome} onChange={(event) => setOutcome(event.target.value)} required /></label>
        <button className="primary-button" type="submit"><FileWarning size={16} /><span>Открыть дело</span></button>
      </form>
    </section>
  );
}

function PolicyBand({
  principal,
  policies,
  run,
}: {
  principal: Principal;
  policies: Awaited<ReturnType<typeof getTrustPolicies>>;
  run: RunAction;
}) {
  const active = policies.find((item) => item.status === "ACTIVE");
  const proposed = policies.find((item) => item.status === "PROPOSED");
  const cooperativeId = principal.roles.find(
    (item) => item.role === "COOPERATIVE_ADMIN" && item.cooperative_id,
  )?.cooperative_id;
  const [version, setVersion] = useState("1.0.0");
  const [appealDays, setAppealDays] = useState(14);
  const [measureDays, setMeasureDays] = useState(30);
  const [quorum, setQuorum] = useState(1);
  return (
    <section className="trust-policy-strip">
      <div><ShieldCheck size={17} /><span>Политика</span><strong>{active ? `${active.semantic_version} · v${active.policy_version}` : "Не активирована"}</strong></div>
      {cooperativeId && !proposed ? (
        <form onSubmit={(event) => { event.preventDefault(); run(() => proposeTrustPolicy({ cooperative_id: cooperativeId, semantic_version: version, appeal_window_seconds: appealDays * 86_400, max_protective_seconds: measureDays * 86_400, panel_quorum: quorum, terms: { automatic_liability_execution: false, universal_reputation_score: false } })); }}>
          <label>Версия<input value={version} onChange={(event) => setVersion(event.target.value)} required /></label>
          <label>Апелляция, дней<input type="number" min="0" max="30" value={appealDays} onChange={(event) => setAppealDays(Number(event.target.value))} /></label>
          <label>Мера, дней<input type="number" min="1" max="30" value={measureDays} onChange={(event) => setMeasureDays(Number(event.target.value))} /></label>
          <label>Кворум<input type="number" min="1" max="9" value={quorum} onChange={(event) => setQuorum(Number(event.target.value))} /></label>
          <button className="secondary-button" type="submit"><ShieldAlert size={15} /><span>Предложить</span></button>
        </form>
      ) : null}
      {proposed && hasRole(principal, "AUDITOR") ? <button className="primary-button" onClick={() => run(() => approveTrustPolicy(proposed))}><Check size={16} /><span>Утвердить {proposed.semantic_version}</span></button> : null}
    </section>
  );
}

function CaseActions({
  principal,
  item,
  decisions,
  measures,
  sanctions,
  run,
}: {
  principal: Principal;
  item: TrustCase;
  decisions: TrustDecision[];
  measures: ProtectiveMeasure[];
  sanctions: Awaited<ReturnType<typeof getTrustSanctions>>;
  run: RunAction;
}) {
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [measureType, setMeasureType] = useState("ADDITIONAL_REVIEW");
  const [decisionOutcome, setDecisionOutcome] = useState<"SUBSTANTIATED" | "PARTLY_SUBSTANTIATED" | "UNSUBSTANTIATED">("UNSUBSTANTIATED");
  const [appealGrounds, setAppealGrounds] = useState("");
  const original = decisions.find((decision) => decision.stage === "ORIGINAL");
  const sanction = sanctions.find((value) => value.case_id === item.id && value.status !== "REVOKED");
  const ownCase = principal.member_id === item.subject_member_id || principal.member_id === item.claimant_member_id;
  const canAudit = hasRole(principal, "AUDITOR");
  const canProtect = hasRole(principal, "AUDITOR", "RISK_ADMIN");
  const canArbitrate = hasRole(principal, "ARBITRATOR");
  const withEvidence = async (kind: string, action: (evidenceId: string) => Promise<unknown>) => {
    if (!file) throw new Error("evidence_required");
    const evidenceId = await uploadEvidence(item.cooperative_id, file, kind);
    await action(evidenceId);
    setFile(null);
    setText("");
  };
  const evidenceInput = <input type="file" accept="application/pdf,image/jpeg,image/png,image/webp,text/plain" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />;
  return (
    <div className="trust-actions">
      {ownCase && ["OPEN", "REMANDED"].includes(item.status) ? <form onSubmit={(event) => { event.preventDefault(); run(() => file ? withEvidence("TRUST_RESPONSE", (id) => respondToTrustCase(item, text, [id])) : respondToTrustCase(item, text, [])); }}><label>Ответ<textarea value={text} onChange={(event) => setText(event.target.value)} required /></label><label>Документ{evidenceInput}</label><button className="primary-button"><RotateCcw size={15} /><span>Передать ответ</span></button></form> : null}
      {canAudit && ["RESPONSE_RECEIVED", "REMANDED"].includes(item.status) ? <form onSubmit={(event) => { event.preventDefault(); run(() => markTrustCaseReady(item, text)); }}><label>Отметка проверки<input value={text} onChange={(event) => setText(event.target.value)} required /></label><button className="primary-button"><ClipboardCheck size={15} /><span>Готово к решению</span></button></form> : null}
      {canProtect && item.status !== "CLOSED" ? <form onSubmit={(event) => { event.preventDefault(); const now = new Date(); run(() => imposeProtectiveMeasure(item, { measure_type: measureType, scope: { blocked_actions: measureType === "BLOCK_NEW_GUARANTEES" ? ["GUARANTEE_CREATE"] : [] }, rationale: text, expires_at: new Date(now.getTime() + 7 * 86_400_000).toISOString(), review_at: new Date(now.getTime() + 86_400_000).toISOString() })); }}><label>Защитная мера<select value={measureType} onChange={(event) => setMeasureType(event.target.value)}><option value="ADDITIONAL_REVIEW">Дополнительная проверка</option><option value="LIMIT_SCOPE">Ограничить действия</option><option value="SUSPEND_ROLE">Приостановить роль</option><option value="SUSPEND_KEY">Приостановить ключ</option><option value="BLOCK_NEW_GUARANTEES">Запретить новые поручительства</option></select></label><label>Основание<input value={text} onChange={(event) => setText(event.target.value)} required /></label><button className="secondary-button"><Ban size={15} /><span>Применить</span></button></form> : null}
      {canArbitrate && item.status === "READY_FOR_DECISION" && !original ? <form onSubmit={(event) => { event.preventDefault(); run(() => withEvidence("TRUST_DECISION", (id) => issueOriginalDecision(item, { outcome: decisionOutcome, standard_of_proof: "Verified evidence and participant response", fault_class: decisionOutcome === "UNSUBSTANTIATED" ? null : "GOOD_FAITH_ERROR", causal_findings: { reviewed: true }, established_loss: "0", reasoning: text, consequence_spec: { automatic_liability_execution: false }, evidence_ids: [id] }))); }}><label>Решение<select value={decisionOutcome} onChange={(event) => setDecisionOutcome(event.target.value as typeof decisionOutcome)}><option value="UNSUBSTANTIATED">Не подтверждено</option><option value="PARTLY_SUBSTANTIATED">Частично подтверждено</option><option value="SUBSTANTIATED">Подтверждено</option></select></label><label>Мотивировка<input value={text} onChange={(event) => setText(event.target.value)} required /></label><label>Доказательство{evidenceInput}</label><button className="primary-button"><Gavel size={15} /><span>Вынести решение</span></button></form> : null}
      {canArbitrate && item.status === "READY_FOR_DECISION" ? <button className="secondary-button trust-declaration" onClick={() => run(() => declareTrustConflict(item.id, "ORIGINAL", "CLEAR", "No conflict found after registry review."))}><UserRoundCheck size={15} /><span>Заявить об отсутствии конфликта</span></button> : null}
      {canArbitrate && original && !sanction && original.outcome !== "UNSUBSTANTIATED" ? <form onSubmit={(event) => { event.preventDefault(); const now = new Date(); run(() => proposeTrustSanction(original.id, { measure_type: "WARNING", severity: "LOW", scope: { blocked_actions: [] }, rationale: text, starts_at: now.toISOString(), expires_at: new Date(now.getTime() + 30 * 86_400_000).toISOString(), review_at: new Date(now.getTime() + 14 * 86_400_000).toISOString() })); }}><label>Основание санкции<input value={text} onChange={(event) => setText(event.target.value)} required /></label><button className="secondary-button"><ShieldAlert size={15} /><span>Предложить предупреждение</span></button></form> : null}
      {ownCase && original && item.status === "DECIDED" ? <form onSubmit={(event) => { event.preventDefault(); run(() => withEvidence("TRUST_APPEAL", (id) => submitTrustAppeal(item, original.id, sanction?.id ?? null, appealGrounds, [id]))); }}><label>Основания апелляции<textarea value={appealGrounds} onChange={(event) => setAppealGrounds(event.target.value)} required /></label><label>Новые доказательства{evidenceInput}</label><button className="primary-button"><Scale size={15} /><span>Подать апелляцию</span></button></form> : null}
      {measures.length ? <div className="trust-active-note"><ShieldAlert size={15} /><span>Мер по делу: {measures.length}</span></div> : null}
    </div>
  );
}

export default function TrustView({ principal }: { principal: Principal }) {
  const queryClient = useQueryClient();
  const [section, setSection] = useState<Section>("cases");
  const [selectedCaseId, setSelectedCaseId] = useState("");
  const [profileMemberId, setProfileMemberId] = useState(principal.member_id ?? "");
  const [selectedAppealId, setSelectedAppealId] = useState("");
  const [selectedPlanId, setSelectedPlanId] = useState("");
  const policies = useQuery({ queryKey: ["trust", "policies"], queryFn: getTrustPolicies });
  const cases = useQuery({ queryKey: ["trust", "cases"], queryFn: getTrustCases });
  const appeals = useQuery({ queryKey: ["trust", "appeals"], queryFn: getTrustAppeals });
  const sanctions = useQuery({ queryKey: ["trust", "sanctions"], queryFn: getTrustSanctions });
  const events = useQuery({ queryKey: ["trust", "reputation"], queryFn: getReputationEvents });
  const plans = useQuery({ queryKey: ["trust", "rehabilitation"], queryFn: getRehabilitationPlans });
  const members = useQuery({ queryKey: ["inventory-members"], queryFn: getInventoryMembers });
  const canArbitrate = hasRole(principal, "ARBITRATOR");
  const canAudit = hasRole(principal, "AUDITOR");
  const arbitratorWorkspace = useQuery({ queryKey: ["trust", "workspace", "arbitrator"], queryFn: getArbitratorWorkspace, enabled: canArbitrate });
  const auditorWorkspace = useQuery({ queryKey: ["trust", "workspace", "auditor"], queryFn: getAuditorWorkspace, enabled: canAudit });
  const selected = cases.data?.find((item) => item.id === selectedCaseId) ?? null;
  const decisions = useQuery({ queryKey: ["trust", selectedCaseId, "decisions"], queryFn: () => getTrustDecisions(selectedCaseId), enabled: Boolean(selectedCaseId) });
  const conflicts = useQuery({ queryKey: ["trust", selectedCaseId, "conflicts"], queryFn: () => getTrustConflicts(selectedCaseId), enabled: Boolean(selectedCaseId) });
  const measures = useQuery({ queryKey: ["trust", selectedCaseId, "measures"], queryFn: () => getProtectiveMeasures(selectedCaseId), enabled: Boolean(selectedCaseId) });
  const allMeasures = useQuery({ queryKey: ["trust", "all-measures", ...(cases.data?.map((item) => item.id) ?? [])], queryFn: async () => (await Promise.all((cases.data ?? []).map((item) => getProtectiveMeasures(item.id)))).flat(), enabled: Boolean(cases.data) });
  const profile = useQuery({ queryKey: ["trust", "profile", profileMemberId], queryFn: () => getReliabilityProfile(profileMemberId), enabled: Boolean(profileMemberId) });
  const steps = useQuery({ queryKey: ["trust", "rehabilitation", selectedPlanId, "steps"], queryFn: () => getRehabilitationSteps(selectedPlanId), enabled: Boolean(selectedPlanId) });

  useEffect(() => { if (!selectedCaseId && cases.data?.length) setSelectedCaseId(cases.data[0]?.id ?? ""); }, [cases.data, selectedCaseId]);
  useEffect(() => { if (!selectedAppealId && appeals.data?.length) setSelectedAppealId(appeals.data[0]?.id ?? ""); }, [appeals.data, selectedAppealId]);
  useEffect(() => { if (!selectedPlanId && plans.data?.length) setSelectedPlanId(plans.data[0]?.id ?? ""); }, [plans.data, selectedPlanId]);
  useEffect(() => {
    if (!members.data?.length) return;
    if (members.data.some((item) => item.member_id === profileMemberId)) return;
    const selectedMember = selected?.subject_member_id;
    setProfileMemberId(
      selectedMember && members.data.some((item) => item.member_id === selectedMember)
        ? selectedMember
        : (members.data[0]?.member_id ?? ""),
    );
  }, [members.data, profileMemberId, selected]);

  const refresh = async () => { await queryClient.invalidateQueries({ queryKey: ["trust"] }); };
  const mutation = useMutation({ mutationFn: (action: () => Promise<unknown>) => action(), onSuccess: refresh });
  const run: RunAction = (action) => mutation.mutate(action);
  const failed = [policies, cases, appeals, sanctions, events, plans, members].find((query) => query.isError);
  const pending = [policies, cases, appeals, sanctions, events, plans, members].some((query) => query.isPending);
  const memberName = (id: string) => members.data?.find((item) => item.member_id === id)?.display_name ?? id.slice(0, 8);
  const selectedAppeal = appeals.data?.find((item) => item.id === selectedAppealId) ?? null;
  const selectedPlan = plans.data?.find((item) => item.id === selectedPlanId) ?? null;
  const activeMeasures = allMeasures.data?.filter((item) => item.status === "ACTIVE") ?? [];
  const disputedEvents = events.data?.filter((item) => item.status === "DISPUTED") ?? [];
  const openCases = cases.data?.filter((item) => item.status !== "CLOSED").length ?? 0;
  const workspaceQueue = (arbitratorWorkspace.data?.ready_cases.length ?? 0) + (arbitratorWorkspace.data?.submitted_appeals.length ?? 0) + (auditorWorkspace.data?.cases_needing_review.length ?? 0);
  const sections: Array<[Section, string, typeof Gavel]> = [["cases", "Дела", FileWarning], ["appeals", "Апелляции", Scale], ["measures", "Меры", ShieldAlert], ["reputation", "Репутация", History], ["rehabilitation", "Реабилитация", UserRoundCheck]];

  if (pending) return <div className="view-stack"><div className="state"><RefreshCw className="spin" size={24} />Загрузка дел</div></div>;
  if (failed) return <div className="view-stack"><div className="state error" role="alert"><AlertTriangle size={22} />{errorText(failed.error)}</div></div>;

  return (
    <div className="view-stack trust-view">
      <header className="view-header"><div><span className="eyebrow">ПРОЦЕДУРНАЯ СПРАВЕДЛИВОСТЬ</span><h1>Споры и доверие</h1><p>{selected ? `${selected.case_reference} · ${memberName(selected.subject_member_id)}` : "Очередь пуста"}</p></div><div className="section-tabs">{sections.map(([key, label, Icon]) => <button type="button" className={section === key ? "active" : ""} onClick={() => setSection(key)} key={key}><Icon size={15} /><span>{label}</span></button>)}</div></header>
      <section className="metric-grid trust-metrics" aria-label="Сводка"><article className="metric"><FileWarning size={18} /><span>Открытые дела</span><strong>{openCases}</strong></article><article className="metric"><Scale size={18} /><span>Апелляции</span><strong>{appeals.data?.filter((item) => item.status === "SUBMITTED").length ?? 0}</strong></article><article className="metric"><ShieldAlert size={18} /><span>Активные меры</span><strong>{activeMeasures.length}</strong></article><article className="metric"><History size={18} /><span>Спорные факты</span><strong>{disputedEvents.length}</strong></article><article className="metric"><ClipboardCheck size={18} /><span>Моя очередь</span><strong>{workspaceQueue}</strong></article></section>
      {mutation.isError ? <p className="trust-error" role="alert">{errorText(mutation.error)}</p> : null}

      {section === "cases" ? <>
        <PolicyBand principal={principal} policies={policies.data ?? []} run={run} />
        <NewCaseForm principal={principal} members={members.data ?? []} run={run} />
        <section className="panel trust-case-layout"><div className="trust-case-list">{cases.data?.map((item) => <button type="button" className={selectedCaseId === item.id ? "active" : ""} onClick={() => setSelectedCaseId(item.id)} key={item.id}><span><strong>{item.case_reference}</strong><small>{memberName(item.subject_member_id)} · {item.summary}</small></span><Status value={item.status} /></button>)}</div>{selected ? <div className="trust-case-detail"><div className="trust-title"><div><span>{selected.source_type} · {selected.source_reference}</span><h2>{selected.summary}</h2></div><Status value={selected.status} /></div><dl><dt>Участник</dt><dd>{memberName(selected.subject_member_id)}</dd><dt>Заявитель</dt><dd>{memberName(selected.claimant_member_id)}</dd><dt>Открыто</dt><dd>{formatLocalDateTime(selected.opened_at)}</dd><dt>Факты</dt><dd>{selected.facts}</dd><dt>Требование</dt><dd>{selected.requested_outcome}</dd>{selected.response_text ? <><dt>Ответ</dt><dd>{selected.response_text}</dd></> : null}</dl><div className="trust-timeline"><span>Деклараций: {conflicts.data?.length ?? 0}</span><span>Решений: {decisions.data?.length ?? 0}</span><span>Мер: {measures.data?.length ?? 0}</span></div><CaseActions principal={principal} item={selected} decisions={decisions.data ?? []} measures={measures.data ?? []} sanctions={sanctions.data ?? []} run={run} /></div> : <div className="state">Дел нет</div>}</section>
      </> : null}

      {section === "appeals" ? <AppealsSection principal={principal} appeals={appeals.data ?? []} cases={cases.data ?? []} selected={selectedAppeal} selectedId={selectedAppealId} setSelected={setSelectedAppealId} memberName={memberName} run={run} /> : null}
      {section === "measures" ? <MeasuresSection principal={principal} measures={allMeasures.data ?? []} sanctions={sanctions.data ?? []} memberName={memberName} run={run} /> : null}
      {section === "reputation" ? <ReputationSection memberId={profileMemberId} setMemberId={setProfileMemberId} members={members.data ?? []} profile={profile.data} events={events.data ?? []} /> : null}
      {section === "rehabilitation" ? <RehabilitationSection principal={principal} plans={plans.data ?? []} selected={selectedPlan} selectedId={selectedPlanId} setSelected={setSelectedPlanId} steps={steps.data ?? []} decisions={decisions.data ?? []} caseItem={selected} memberName={memberName} run={run} /> : null}
    </div>
  );
}

function AppealsSection({ principal, appeals, cases, selected, selectedId, setSelected, memberName, run }: { principal: Principal; appeals: TrustAppeal[]; cases: TrustCase[]; selected: TrustAppeal | null; selectedId: string; setSelected: (value: string) => void; memberName: (id: string) => string; run: RunAction }) {
  const [outcome, setOutcome] = useState<"AFFIRMED" | "MODIFIED" | "OVERTURNED" | "REMANDED">("OVERTURNED");
  const [reasoning, setReasoning] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const item = cases.find((value) => value.id === selected?.case_id);
  return <section className="panel trust-appeal-layout"><div className="trust-case-list">{appeals.map((appeal) => <button className={appeal.id === selectedId ? "active" : ""} onClick={() => setSelected(appeal.id)} key={appeal.id}><span><strong>{cases.find((value) => value.id === appeal.case_id)?.case_reference ?? appeal.case_id.slice(0, 8)}</strong><small>{memberName(appeal.appellant_member_id)} · {appeal.grounds}</small></span><Status value={appeal.outcome ?? appeal.status} /></button>)}</div>{selected && item ? <div className="trust-case-detail"><div className="trust-title"><div><span>{formatLocalDateTime(selected.submitted_at)}</span><h2>{selected.grounds}</h2></div><Status value={selected.outcome ?? selected.status} /></div>{hasRole(principal, "ARBITRATOR") && selected.status === "SUBMITTED" ? <div className="trust-actions"><button className="secondary-button trust-declaration" onClick={() => run(() => declareTrustConflict(item.id, "APPEAL", "CLEAR", "Independent panel membership verified."))}><UserRoundCheck size={15} /><span>Подтвердить независимость</span></button><form onSubmit={(event) => { event.preventDefault(); if (!file) return; run(async () => { const evidenceId = await uploadEvidence(item.cooperative_id, file, "TRUST_APPEAL_DECISION"); await decideTrustAppeal(selected, item.version, { outcome, reasoning, evidence_ids: [evidenceId] }); setFile(null); setReasoning(""); }); }}><label>Исход<select value={outcome} onChange={(event) => setOutcome(event.target.value as typeof outcome)}><option value="OVERTURNED">Отменить</option><option value="MODIFIED">Изменить</option><option value="AFFIRMED">Подтвердить</option><option value="REMANDED">Вернуть на рассмотрение</option></select></label><label>Мотивировка<textarea value={reasoning} onChange={(event) => setReasoning(event.target.value)} required /></label><label>Доказательство<input type="file" onChange={(event) => setFile(event.target.files?.[0] ?? null)} required /></label><button className="primary-button"><Gavel size={15} /><span>Решить апелляцию</span></button></form></div> : null}</div> : <div className="state">Апелляций нет</div>}</section>;
}

function MeasuresSection({ principal, measures, sanctions, memberName, run }: { principal: Principal; measures: ProtectiveMeasure[]; sanctions: Awaited<ReturnType<typeof getTrustSanctions>>; memberName: (id: string) => string; run: RunAction }) {
  const [reason, setReason] = useState("Основания меры более не действуют.");
  return <><section className="action-band trust-lift-band"><label>Основание снятия<input value={reason} onChange={(event) => setReason(event.target.value)} /></label></section><section className="panel"><div className="panel-heading"><h2>Защитные меры</h2><span>{measures.length}</span></div><div className="table-wrap"><table className="trust-table"><thead><tr><th>Участник</th><th>Мера</th><th>Срок проверки</th><th>Состояние</th><th></th></tr></thead><tbody>{measures.map((item) => <tr key={item.id}><td><strong>{memberName(item.subject_member_id)}</strong><small>{item.rationale}</small></td><td>{item.measure_type}</td><td>{formatLocalDateTime(item.review_at)}</td><td><Status value={item.status} /></td><td>{item.status === "ACTIVE" && hasRole(principal, "AUDITOR", "RISK_ADMIN") ? <button className="compact-command" onClick={() => run(() => liftProtectiveMeasure(item, reason))}><Check size={14} /><span>Снять</span></button> : null}</td></tr>)}</tbody></table></div></section><section className="panel"><div className="panel-heading"><h2>Санкции</h2><span>{sanctions.length}</span></div><div className="table-wrap"><table className="trust-table"><thead><tr><th>Участник</th><th>Санкция</th><th>Апелляция до</th><th>Состояние</th><th></th></tr></thead><tbody>{sanctions.map((item) => <tr key={item.id}><td><strong>{memberName(item.subject_member_id)}</strong><small>{item.rationale}</small></td><td>{item.measure_type} · {item.severity}</td><td>{formatLocalDateTime(item.appeal_until)}</td><td><Status value={item.status} /></td><td>{item.status === "PENDING_APPEAL" && hasRole(principal, "ARBITRATOR") ? <button className="compact-command" onClick={() => run(() => finalizeTrustSanction(item))}><BadgeCheck size={14} /><span>Финализировать</span></button> : null}</td></tr>)}</tbody></table></div></section></>;
}

function ReputationSection({ memberId, setMemberId, members, profile, events }: { memberId: string; setMemberId: (value: string) => void; members: Array<{ member_id: string; display_name: string }>; profile: Awaited<ReturnType<typeof getReliabilityProfile>> | undefined; events: Awaited<ReturnType<typeof getReputationEvents>> }) {
  const visible = events.filter((item) => item.subject_member_id === memberId);
  return <><section className="action-band trust-profile-filter"><label>Участник<select value={memberId} onChange={(event) => setMemberId(event.target.value)}>{members.map((item) => <option value={item.member_id} key={item.member_id}>{item.display_name}</option>)}</select></label><div><span>Меры</span><strong>{profile?.active_measures ?? 0}</strong></div><div><span>Санкции</span><strong>{profile?.active_sanctions ?? 0}</strong></div><div><span>Реабилитация</span><strong>{profile?.rehabilitation_active ?? 0}</strong></div></section><section className="panel"><div className="panel-heading"><h2>Контекстная матрица</h2><span>{profile?.contexts.length ?? 0}</span></div><div className="table-wrap"><table className="trust-profile-table"><thead><tr><th>Контекст</th><th>Выполнено</th><th>Нарушения</th><th>Самоисправления</th><th>Спорные</th><th>Коррекции</th><th>Наблюдений</th></tr></thead><tbody>{profile?.contexts.map((item) => <tr key={item.context}><td><strong>{contextNames[item.context] ?? item.context}</strong></td><td>{item.confirmed_fulfillments}</td><td>{item.confirmed_breaches}</td><td>{item.self_reported_errors}</td><td>{item.disputed_events}</td><td>{item.corrections}</td><td>{item.sample_count}</td></tr>)}</tbody></table></div></section><section className="panel"><div className="panel-heading"><h2>Факты и исправления</h2><span>{visible.length}</span></div><div className="rows">{visible.map((item) => <div className="trust-event-row" key={item.id}><span className={`trust-event-mark ${item.classification.toLowerCase()}`}><History size={15} /></span><div><strong>{contextNames[item.context] ?? item.context} · {item.classification}</strong><small>{formatLocalDateTime(item.observation_end)} · достоверность {item.confidence}</small></div><Status value={item.status} /><code>{item.corrects_event_id ? `исправляет ${item.corrects_event_id.slice(0, 8)}` : item.id.slice(0, 8)}</code></div>)}</div></section></>;
}

function RehabilitationSection({ principal, plans, selected, selectedId, setSelected, steps, decisions, caseItem, memberName, run }: { principal: Principal; plans: RehabilitationPlan[]; selected: RehabilitationPlan | null; selectedId: string; setSelected: (value: string) => void; steps: Awaited<ReturnType<typeof getRehabilitationSteps>>; decisions: TrustDecision[]; caseItem: TrustCase | null; memberName: (id: string) => string; run: RunAction }) {
  const [file, setFile] = useState<File | null>(null);
  const [reason, setReason] = useState("Все шаги подтверждены независимыми доказательствами.");
  const original = decisions.find((item) => item.stage === "ORIGINAL");
  return <><section className="panel trust-rehab-layout"><div className="trust-case-list">{plans.map((plan) => <button className={plan.id === selectedId ? "active" : ""} onClick={() => setSelected(plan.id)} key={plan.id}><span><strong>{plan.title}</strong><small>{memberName(plan.subject_member_id)} · до {formatLocalDateTime(plan.due_at)}</small></span><Status value={plan.status} /></button>)}</div>{selected ? <div className="trust-case-detail"><div className="trust-title"><div><span>{memberName(selected.subject_member_id)}</span><h2>{selected.title}</h2></div><Status value={selected.status} /></div><div className="trust-steps">{steps.map((step) => <div key={step.id}><span>{step.sequence}</span><div><strong>{step.description}</strong><small>{step.completion_criterion}</small></div><Status value={step.status} />{step.status === "PENDING" && principal.member_id === selected.subject_member_id ? <button className="compact-command" disabled={!file} onClick={() => { if (!file || !caseItem) return; run(async () => { const evidenceId = await uploadEvidence(caseItem.cooperative_id, file, "TRUST_REHABILITATION"); await completeRehabilitationStep(selected, step.id, [evidenceId]); setFile(null); }); }}><Check size={14} /><span>Завершить</span></button> : null}</div>)}</div>{selected.status === "ACTIVE" ? <div className="trust-actions"><label>Подтверждение<input type="file" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></label>{hasRole(principal, "ARBITRATOR") ? <form onSubmit={(event) => { event.preventDefault(); run(() => closeRehabilitationPlan(selected, "OBLIGATION", reason)); }}><label>Заключение<input value={reason} onChange={(event) => setReason(event.target.value)} required /></label><button className="primary-button"><BadgeCheck size={15} /><span>Закрыть план</span></button></form> : null}</div> : null}</div> : <div className="state">Планов нет</div>}</section>{hasRole(principal, "ARBITRATOR") && original && caseItem && !plans.some((item) => item.case_id === caseItem.id && item.status === "ACTIVE") ? <section className="action-band"><button className="secondary-button" onClick={() => { const now = new Date(); run(() => createRehabilitationPlan(original.id, { title: "План восстановления доверия", completion_criteria: { evidence_required: true }, starts_at: now.toISOString(), due_at: new Date(now.getTime() + 30 * 86_400_000).toISOString(), steps: [{ description: "Подтвердить корректирующее действие", completion_criterion: "Проверяемый документ принят" }] })); }}><UserRoundCheck size={15} /><span>Создать план по выбранному делу</span></button></section> : null}</>;
}
