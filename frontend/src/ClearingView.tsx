import {
  AlertTriangle,
  BadgeCheck,
  BookOpenCheck,
  Calculator,
  Check,
  FileCheck2,
  Fingerprint,
  Gavel,
  ListTree,
  LockKeyhole,
  Play,
  Plus,
  RefreshCw,
  Scale,
  ShieldCheck,
  Snowflake,
  UsersRound,
} from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useMemo, useState } from "react";

import { AdminApiError, getCooperatives, type Principal, type RoleCode } from "./api/admin";
import {
  approveClearingPolicy,
  approveClearingPreview,
  collectClearingCycle,
  createClearingCycle,
  decideClearingDispute,
  finalizeClearingCycle,
  freezeClearingInput,
  getClearingAccountingExport,
  getClearingApprovals,
  getClearingCycles,
  getClearingDisputes,
  getClearingEntries,
  getClearingInput,
  getClearingPolicies,
  getClearingPositions,
  getClearingProof,
  getClearingStatements,
  markClearingReady,
  openClearingDispute,
  previewClearingCycle,
  proposeClearingPolicy,
  reconcileClearingCycle,
  verifyClearingProof,
  type ClearingCycle,
  type ClearingDispute,
} from "./api/clearing";
import { getInventoryMembers, getUnits, uploadEvidence } from "./api/inventory";
import { userErrorMessage } from "./shared/api-error";
import { formatLocalDateTime } from "./shared/date-time";
import "./clearing.css";

type Section = "cycles" | "entries" | "positions" | "control" | "proof" | "statements";

const statusNames: Record<string, string> = {
  DRAFT: "Черновик",
  COLLECTING: "Сбор обязательств",
  INPUT_FROZEN: "Вход зафиксирован",
  PREVIEWED: "Расчет готов",
  DISPUTE_WINDOW: "Окно возражений",
  DISPUTED: "Есть спор",
  READY_TO_FINALIZE: "Готов к фиксации",
  FINALIZED: "Зафиксирован",
  RECONCILED: "Сверен",
  CANCELLED: "Отменен",
  FAILED_FINALIZATION: "Ошибка фиксации",
  PROPOSED: "Предложена",
  ACTIVE: "Действует",
  SUPERSEDED: "Заменена",
  INCLUDED: "Включено",
  EXCLUDED: "Исключено",
  OPEN: "Открыт",
  UPHELD: "Подтвержден",
  REJECTED: "Отклонен",
};

const detailedStatuses = new Set([
  "PREVIEWED",
  "DISPUTE_WINDOW",
  "DISPUTED",
  "READY_TO_FINALIZE",
  "FINALIZED",
  "RECONCILED",
]);

function hasRole(principal: Principal, ...roles: RoleCode[]): boolean {
  return principal.roles.some((grant) => roles.includes(grant.role));
}

function errorText(error: unknown): string {
  return userErrorMessage(error);
}

function exact(value: string | number): string {
  const parsed = Number(value);
  return Number.isFinite(parsed)
    ? new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 12 }).format(parsed)
    : String(value);
}

function localDate(value: Date): string {
  const shifted = new Date(value.getTime() - value.getTimezoneOffset() * 60_000);
  return shifted.toISOString().slice(0, 16);
}

function Status({ value }: { value: string }) {
  const kind = ["ACTIVE", "RECONCILED", "INCLUDED", "REJECTED"].includes(value)
    ? "good"
    : ["DISPUTED", "FAILED_FINALIZATION", "UPHELD", "EXCLUDED"].includes(value)
      ? "bad"
      : "warn";
  return <span className={`status ${kind}`}>{statusNames[value] ?? value}</span>;
}

function Hash({ value }: { value: string | null }) {
  return value ? <code className="clearing-hash" title={value}>{value}</code> : <span>—</span>;
}

export default function ClearingView({ principal }: { principal: Principal }) {
  const queryClient = useQueryClient();
  const [section, setSection] = useState<Section>("cycles");
  const [selectedCycleId, setSelectedCycleId] = useState("");
  const [statementMemberId, setStatementMemberId] = useState(principal.member_id ?? "");

  const policies = useQuery({ queryKey: ["clearing", "policies"], queryFn: getClearingPolicies });
  const cycles = useQuery({ queryKey: ["clearing", "cycles"], queryFn: getClearingCycles });
  const members = useQuery({ queryKey: ["inventory-members"], queryFn: getInventoryMembers });
  const units = useQuery({ queryKey: ["inventory-units"], queryFn: getUnits });
  const cooperatives = useQuery({ queryKey: ["cooperatives"], queryFn: getCooperatives });
  const selected = cycles.data?.find((cycle) => cycle.id === selectedCycleId) ?? null;
  const canOperator = hasRole(principal, "CLEARING_OPERATOR");
  const canController = hasRole(principal, "CLEARING_CONTROLLER");
  const canFinalizer = hasRole(principal, "CLEARING_FINALIZER");
  const canAudit = hasRole(
    principal,
    "CLEARING_OPERATOR",
    "CLEARING_CONTROLLER",
    "CLEARING_FINALIZER",
    "COOPERATIVE_ADMIN",
    "SECURITY_ADMIN",
    "AUDITOR",
  );
  const hasDetails = Boolean(selected && detailedStatuses.has(selected.status));
  const hasFrozenInput = Boolean(selected && !["DRAFT", "COLLECTING"].includes(selected.status));
  const hasProof = Boolean(selected && ["FINALIZED", "RECONCILED"].includes(selected.status));

  useEffect(() => {
    if (!selectedCycleId && cycles.data?.length) setSelectedCycleId(cycles.data?.[0]?.id ?? "");
  }, [cycles.data, selectedCycleId]);

  const input = useQuery({
    queryKey: ["clearing", selectedCycleId, "input"],
    queryFn: () => getClearingInput(selectedCycleId),
    enabled: hasFrozenInput && canAudit,
  });
  const entries = useQuery({
    queryKey: ["clearing", selectedCycleId, "entries"],
    queryFn: () => getClearingEntries(selectedCycleId),
    enabled: hasDetails,
  });
  const positions = useQuery({
    queryKey: ["clearing", selectedCycleId, "positions"],
    queryFn: () => getClearingPositions(selectedCycleId),
    enabled: hasDetails,
  });
  const approvals = useQuery({
    queryKey: ["clearing", selectedCycleId, "approvals"],
    queryFn: () => getClearingApprovals(selectedCycleId),
    enabled: hasDetails && canAudit,
  });
  const disputes = useQuery({
    queryKey: ["clearing", selectedCycleId, "disputes"],
    queryFn: () => getClearingDisputes(selectedCycleId),
    enabled: hasDetails,
  });
  const proof = useQuery({
    queryKey: ["clearing", selectedCycleId, "proof"],
    queryFn: () => getClearingProof(selectedCycleId),
    enabled: hasProof && canAudit,
  });
  const statements = useQuery({
    queryKey: ["clearing", selectedCycleId, "statements", statementMemberId],
    queryFn: () => getClearingStatements(selectedCycleId, statementMemberId),
    enabled: hasProof && Boolean(statementMemberId),
  });
  const accounting = useQuery({
    queryKey: ["clearing", selectedCycleId, "accounting"],
    queryFn: () => getClearingAccountingExport(selectedCycleId),
    enabled: selected?.status === "RECONCILED" && canAudit,
  });

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["clearing"] });
  };
  const command = useMutation({
    mutationFn: (action: () => Promise<unknown>) => action(),
    onSuccess: refresh,
  });
  const verification = useMutation({
    mutationFn: () => verifyClearingProof(proof.data?.proof_payload ?? {}),
  });

  const baseQueries = [policies, cycles, members, units, cooperatives];
  if (baseQueries.some((query) => query.isPending)) {
    return <div className="view-stack"><div className="state"><RefreshCw className="spin" size={24} />Загрузка клиринга</div></div>;
  }
  const failed = baseQueries.find((query) => query.isError);
  if (failed) {
    return <div className="view-stack"><div className="state error" role="alert"><AlertTriangle size={22} />{errorText(failed.error)}</div></div>;
  }

  const policyData = policies.data ?? [];
  const cycleData = cycles.data ?? [];
  const memberData = members.data ?? [];
  const entryData = entries.data ?? [];
  const positionData = positions.data ?? [];
  const disputeData = disputes.data ?? [];
  const activePolicy = policyData.find((item) => item.status === "ACTIVE") ?? null;
  const clearedTotal = entryData.reduce((sum, item) => sum + Number(item.cleared_amount), 0);
  const participantCount = new Set(
    entryData.flatMap((item) => [item.debtor_member_id, item.creditor_member_id]),
  ).size;
  const memberName = (id: string) =>
    memberData.find((item) => item.member_id === id)?.display_name ?? id.slice(0, 8);

  const sections: Array<[Section, string, typeof Calculator]> = [
    ["cycles", "Циклы", Calculator],
    ["entries", "Обязательства", ListTree],
    ["positions", "Позиции", UsersRound],
    ["control", "Контроль", Gavel],
    ["proof", "Доказательство", Fingerprint],
    ["statements", "Выписки", BookOpenCheck],
  ];

  return (
    <div className="view-stack clearing-view">
      <header className="view-header">
        <div>
          <span className="eyebrow">ДЕТЕРМИНИРОВАННЫЙ ВЗАИМОЗАЧЕТ</span>
          <h1>Локальный клиринг</h1>
          <p>Фиксация входа, независимое подтверждение, окно возражений и проверяемый результат</p>
        </div>
        <div className="section-tabs">
          {sections.map(([key, label, Icon]) => (
            <button type="button" className={section === key ? "active" : ""} onClick={() => setSection(key)} key={key}>
              <Icon size={15} /><span>{label}</span>
            </button>
          ))}
        </div>
      </header>

      <section className="metric-grid clearing-metrics" aria-label="Сводка клиринга">
        <article className="metric"><Calculator size={18} /><span>Циклов</span><strong>{cycleData.length}</strong></article>
        <article className="metric"><ShieldCheck size={18} /><span>Политика</span><strong>{activePolicy ? `v${activePolicy.policy_version}` : "—"}</strong></article>
        <article className="metric"><ListTree size={18} /><span>Входов</span><strong>{selected?.collected_count ?? 0}</strong></article>
        <article className="metric"><Scale size={18} /><span>Зачтено</span><strong>{exact(clearedTotal)}</strong></article>
        <article className="metric"><UsersRound size={18} /><span>Участников</span><strong>{participantCount}</strong></article>
        <article className="metric"><Gavel size={18} /><span>Споров</span><strong>{disputeData.filter((item) => item.status === "OPEN").length}</strong></article>
      </section>

      {command.isError ? <p className="form-error clearing-error" role="alert">{errorText(command.error)}</p> : null}

      {section === "cycles" ? (
        <CyclesSection
          principal={principal}
          policies={policyData}
          cycles={cycleData}
          selected={selected}
          selectedCycleId={selectedCycleId}
          onSelect={setSelectedCycleId}
          units={units.data ?? []}
          cooperativeId={cooperatives.data?.[0]?.id ?? ""}
          canOperator={canOperator}
          canController={canController}
          busy={command.isPending}
          run={(action) => command.mutate(action)}
        />
      ) : null}

      {section === "entries" ? (
        <section className="panel">
          <div className="panel-heading"><h2>Состав расчета</h2><span>{entryData.length}</span></div>
          {!selected ? <Empty text="Выберите цикл" /> : entries.isPending ? <Loading /> : entries.isError ? <ErrorLine error={entries.error} /> : (
            <div className="table-wrap"><table className="clearing-table clearing-entry-table"><thead><tr><th>Обязательство</th><th>Должник</th><th>Кредитор</th><th>До</th><th>Зачтено</th><th>После</th><th>Статус</th></tr></thead><tbody>
              {entryData.map((item) => <tr key={item.id}><td><strong>{item.obligation_id.slice(0, 8)}</strong><small>v{item.obligation_version}</small></td><td>{memberName(item.debtor_member_id)}</td><td>{memberName(item.creditor_member_id)}</td><td>{exact(item.amount_before)}</td><td><strong>{exact(item.cleared_amount)}</strong></td><td>{exact(item.amount_after)}</td><td><Status value={item.inclusion_status} />{item.exclusion_reason ? <small>{item.exclusion_reason}</small> : null}</td></tr>)}
            </tbody></table></div>
          )}
        </section>
      ) : null}

      {section === "positions" ? (
        <section className="panel">
          <div className="panel-heading"><h2>Позиции участников</h2><span>{positionData.length}</span></div>
          {!selected ? <Empty text="Выберите цикл" /> : positions.isPending ? <Loading /> : positions.isError ? <ErrorLine error={positions.error} /> : (
            <div className="table-wrap"><table className="clearing-table"><thead><tr><th>Участник</th><th>Входящие до</th><th>Исходящие до</th><th>Зачтено входящих</th><th>Зачтено исходящих</th><th>Сальдо до</th><th>Сальдо после</th></tr></thead><tbody>
              {positionData.map((item) => <tr key={item.id}><td><strong>{memberName(item.member_id)}</strong><small>{item.member_id}</small></td><td>{exact(item.incoming_before)}</td><td>{exact(item.outgoing_before)}</td><td>{exact(item.incoming_cleared)}</td><td>{exact(item.outgoing_cleared)}</td><td>{exact(item.net_before)}</td><td><strong>{exact(item.net_after)}</strong></td></tr>)}
            </tbody></table></div>
          )}
        </section>
      ) : null}

      {section === "control" ? (
        <ControlSection
          principal={principal}
          cycle={selected}
          entries={entryData}
          disputes={disputeData}
          approvals={approvals.data ?? []}
          memberName={memberName}
          canController={canController}
          busy={command.isPending}
          run={(action) => command.mutate(action)}
        />
      ) : null}

      {section === "proof" ? (
        <section className="panel clearing-proof">
          <div className="panel-heading"><h2>Доказательство результата</h2><span>{proof.data ? "1" : "0"}</span></div>
          {!selected ? <Empty text="Выберите цикл" /> : !canAudit ? <Empty text="Доступно контрольным ролям" /> : proof.isPending ? <Loading /> : proof.isError ? <ErrorLine error={proof.error} /> : proof.data ? (
            <div className="proof-layout"><dl><dt>Proof hash</dt><dd><Hash value={proof.data.proof_hash} /></dd><dt>Событие фиксации</dt><dd><code>{proof.data.finalized_event_id}</code></dd><dt>Hash события узла</dt><dd><Hash value={proof.data.node_event_hash} /></dd><dt>Создано</dt><dd>{formatLocalDateTime(proof.data.created_at)}</dd></dl><div><button className="primary-button" type="button" disabled={verification.isPending} onClick={() => verification.mutate()}><BadgeCheck size={17} /><span>Проверить</span></button>{verification.data ? <p className={`verification-result ${verification.data.valid ? "valid" : "invalid"}`}><BadgeCheck size={17} />{verification.data.valid ? "Доказательство действительно" : "Проверка не пройдена"}</p> : null}<details><summary>Канонический пакет</summary><pre>{JSON.stringify(proof.data.proof_payload, null, 2)}</pre></details></div></div>
          ) : <Empty text="Доказательство появится после финализации" />}
        </section>
      ) : null}

      {section === "statements" ? (
        <section className="panel">
          <div className="panel-heading"><h2>Выписки и учетный пакет</h2><span>{statements.data?.length ?? 0}</span></div>
          <div className="clearing-statement-filter"><label>Участник<select value={statementMemberId} onChange={(event) => setStatementMemberId(event.target.value)}><option value="">Выберите</option>{memberData.filter((item) => canAudit || item.member_id === principal.member_id).map((item) => <option value={item.member_id} key={item.member_id}>{item.display_name}</option>)}</select></label></div>
          {statements.isPending ? <Loading /> : statements.isError ? <ErrorLine error={statements.error} /> : <div className="rows">{(statements.data ?? []).map((item) => <div className="data-row clearing-statement" key={item.id}><strong>{memberName(item.member_id)}</strong><Hash value={item.statement_hash} /><details><summary>Состав</summary><pre>{JSON.stringify(item.statement_payload, null, 2)}</pre></details></div>)}</div>}
          {canAudit && selected?.status === "RECONCILED" ? <div className="accounting-package"><FileCheck2 size={20} /><div><strong>Учетный пакет</strong>{accounting.data ? <Hash value={accounting.data.package_hash} /> : accounting.isError ? <span>{errorText(accounting.error)}</span> : <span>Загрузка</span>}</div>{accounting.data ? <details><summary>Состав</summary><pre>{JSON.stringify(accounting.data.export_payload, null, 2)}</pre></details> : null}</div> : null}
        </section>
      ) : null}

      {selected ? <section className="clearing-integrity" aria-label="Контрольные хеши"><div><span>Input</span><Hash value={selected.input_hash} /></div><div><span>Parameters</span><Hash value={selected.parameters_hash} /></div><div><span>Result</span><Hash value={selected.result_hash} /></div>{input.data ? <div><span>Snapshot v{input.data.input_version}</span><strong>{formatLocalDateTime(input.data.frozen_at)}</strong></div> : null}</section> : null}

      {selected ? <WorkflowBar cycle={selected} canOperator={canOperator} canController={canController} canFinalizer={canFinalizer} busy={command.isPending} run={(action) => command.mutate(action)} /> : null}
    </div>
  );
}

function CyclesSection({
  principal,
  policies,
  cycles,
  selected,
  selectedCycleId,
  onSelect,
  units,
  cooperativeId,
  canOperator,
  canController,
  busy,
  run,
}: {
  principal: Principal;
  policies: Awaited<ReturnType<typeof getClearingPolicies>>;
  cycles: ClearingCycle[];
  selected: ClearingCycle | null;
  selectedCycleId: string;
  onSelect: (value: string) => void;
  units: Awaited<ReturnType<typeof getUnits>>;
  cooperativeId: string;
  canOperator: boolean;
  canController: boolean;
  busy: boolean;
  run: (action: () => Promise<unknown>) => void;
}) {
  const activePolicy = policies.find((item) => item.status === "ACTIVE") ?? null;
  const proposedPolicies = policies.filter((item) => item.status === "PROPOSED");
  return (
    <>
      {canOperator ? <PolicyForm cooperativeId={cooperativeId} units={units} run={run} busy={busy} /> : null}
      {canController && proposedPolicies.length ? <section className="action-band clearing-approvals"><div><strong>Политики на независимом контроле</strong><span>{proposedPolicies.length}</span></div>{proposedPolicies.map((policy) => <button type="button" key={policy.id} disabled={busy || policy.proposed_by_member_id === principal.member_id} onClick={() => run(() => approveClearingPolicy(policy))}><Check size={16} /><span>Утвердить v{policy.policy_version}</span></button>)}</section> : null}
      {canOperator && activePolicy ? <CycleForm cooperativeId={cooperativeId} policy={activePolicy} run={run} busy={busy} /> : null}
      <section className="panel clearing-cycle-panel">
        <div className="panel-heading"><h2>Расчетные циклы</h2><span>{cycles.length}</span></div>
        <div className="clearing-cycle-layout">
          <div className="clearing-cycle-list" role="listbox" aria-label="Расчетные циклы">
            {cycles.map((cycle) => <button type="button" role="option" aria-selected={cycle.id === selectedCycleId} className={cycle.id === selectedCycleId ? "active" : ""} onClick={() => onSelect(cycle.id)} key={cycle.id}><span><strong>{cycle.cycle_code}</strong><small>{formatLocalDateTime(cycle.period_start)} — {formatLocalDateTime(cycle.period_end)}</small></span><Status value={cycle.status} /></button>)}
            {!cycles.length ? <Empty text="Циклы еще не созданы" /> : null}
          </div>
          <div className="clearing-cycle-detail">
            {selected ? <><div className="cycle-title"><div><span>Выбранный цикл</span><h2>{selected.cycle_code}</h2></div><Status value={selected.status} /></div><dl><dt>Версия</dt><dd>{selected.version}</dd><dt>Собрано</dt><dd>{selected.collected_count}</dd><dt>Окно возражений</dt><dd>{formatLocalDateTime(selected.dispute_until)}</dd><dt>Расчет</dt><dd>{formatLocalDateTime(selected.previewed_at)}</dd><dt>Фиксация</dt><dd>{formatLocalDateTime(selected.finalized_at)}</dd><dt>Сверка</dt><dd>{formatLocalDateTime(selected.reconciled_at)}</dd></dl></> : <Empty text="Выберите цикл" />}
          </div>
        </div>
      </section>
    </>
  );
}

function PolicyForm({ cooperativeId, units, run, busy }: { cooperativeId: string; units: Awaited<ReturnType<typeof getUnits>>; run: (action: () => Promise<unknown>) => void; busy: boolean }) {
  const valuationUnits = units.filter((unit) => unit.dimension === "VALUATION" && unit.status === "ACTIVE");
  const [unitId, setUnitId] = useState(valuationUnits[0]?.id ?? "");
  const [scale, setScale] = useState(valuationUnits[0]?.decimal_scale ?? 2);
  useEffect(() => { const first = valuationUnits[0]; if (!unitId && first) { setUnitId(first.id); setScale(first.decimal_scale); } }, [unitId, valuationUnits]);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    run(() => proposeClearingPolicy({ cooperative_id: cooperativeId, valuation_unit_id: unitId, decimal_scale: scale, rounding_mode: "DOWN", minimum_operation: "0.01", max_iterations: 10000, max_cycle_length: 6, dispute_window_seconds: 86400, required_approvals: 1, liquidity_order: ["A", "B", "C", "D", "E", "UNASSESSED"] }));
  };
  return <section className="action-band clearing-command"><form onSubmit={submit}><label>Единица оценки<select value={unitId} onChange={(event) => { const value = event.target.value; setUnitId(value); setScale(valuationUnits.find((item) => item.id === value)?.decimal_scale ?? 2); }} required><option value="">Выберите</option>{valuationUnits.map((unit) => <option value={unit.id} key={unit.id}>{unit.name} ({unit.code})</option>)}</select></label><label>Точность<input type="number" min="0" max="12" value={scale} readOnly /></label><label>Окно возражений<strong>24 часа</strong></label><label>Алгоритм<strong>LOCAL_NETTING 1.0.0</strong></label><button className="primary-button" type="submit" disabled={busy || !unitId}><ShieldCheck size={17} /><span>Предложить политику</span></button></form></section>;
}

function CycleForm({ cooperativeId, policy, run, busy }: { cooperativeId: string; policy: Awaited<ReturnType<typeof getClearingPolicies>>[number]; run: (action: () => Promise<unknown>) => void; busy: boolean }) {
  const now = useMemo(() => new Date(), []);
  const [code, setCode] = useState(`CLEAR-${now.toISOString().slice(0, 10)}`);
  const [start, setStart] = useState(localDate(new Date(now.getTime() - 7 * 86_400_000)));
  const [end, setEnd] = useState(localDate(now));
  return <section className="action-band clearing-command"><form onSubmit={(event) => { event.preventDefault(); run(() => createClearingCycle({ cooperative_id: cooperativeId, policy_id: policy.id, cycle_code: code, period_start: new Date(start).toISOString(), period_end: new Date(end).toISOString() })); }}><label>Код цикла<input value={code} pattern="[A-Za-z0-9._-]+" onChange={(event) => setCode(event.target.value)} required /></label><label>Начало<input type="datetime-local" value={start} onChange={(event) => setStart(event.target.value)} required /></label><label>Окончание<input type="datetime-local" value={end} onChange={(event) => setEnd(event.target.value)} required /></label><label>Политика<strong>v{policy.policy_version}</strong></label><button className="primary-button" type="submit" disabled={busy}><Plus size={17} /><span>Создать цикл</span></button></form></section>;
}

function noteFor(notes: Record<string, string>, id: string): string {
  return notes[id] ?? "";
}

function ControlSection({ principal, cycle, entries, disputes, approvals, memberName, canController, busy, run }: { principal: Principal; cycle: ClearingCycle | null; entries: Awaited<ReturnType<typeof getClearingEntries>>; disputes: ClearingDispute[]; approvals: Awaited<ReturnType<typeof getClearingApprovals>>; memberName: (id: string) => string; canController: boolean; busy: boolean; run: (action: () => Promise<unknown>) => void }) {
  const [entryId, setEntryId] = useState("");
  const [reason, setReason] = useState("AMOUNT_DISPUTED");
  const [statement, setStatement] = useState("");
  const [evidence, setEvidence] = useState<File | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const eligibleEntries = entries.filter((item) => principal.member_id && [item.debtor_member_id, item.creditor_member_id].includes(principal.member_id));
  const openDispute = async () => {
    if (!cycle || !evidence || !entryId) return;
    const evidenceId = await uploadEvidence(cycle.cooperative_id, evidence, "CLEARING_DISPUTE");
    await openClearingDispute(cycle, { entry_id: entryId, reason_code: reason, statement, evidence_ids: [evidenceId] });
    setStatement(""); setEvidence(null);
  };
  if (!cycle) return <section className="panel"><Empty text="Выберите цикл" /></section>;
  return <div className="clearing-control-grid"><section className="panel"><div className="panel-heading"><h2>Независимые подтверждения</h2><span>{approvals.length}</span></div><div className="rows">{approvals.map((item) => <div className="data-row" key={item.id}><strong>{memberName(item.member_id)}</strong><span>{formatLocalDateTime(item.approved_at)}</span><Hash value={item.result_hash} /></div>)}{!approvals.length ? <Empty text="Подтверждений пока нет" /> : null}</div></section><section className="panel"><div className="panel-heading"><h2>Возражения</h2><span>{disputes.length}</span></div>{cycle.status === "DISPUTE_WINDOW" && eligibleEntries.length ? <form className="clearing-dispute-form" onSubmit={(event) => { event.preventDefault(); run(openDispute); }}><label>Обязательство<select value={entryId} onChange={(event) => setEntryId(event.target.value)} required><option value="">Выберите</option>{eligibleEntries.map((item) => <option value={item.id} key={item.id}>{item.obligation_id.slice(0, 8)} · {exact(item.cleared_amount)}</option>)}</select></label><label>Причина<input value={reason} pattern="[A-Za-z0-9._-]+" onChange={(event) => setReason(event.target.value)} required /></label><label className="span-two">Заявление<textarea value={statement} onChange={(event) => setStatement(event.target.value)} required minLength={2} /></label><label>Доказательство<input type="file" accept="application/pdf,image/jpeg,image/png,image/webp,text/plain" onChange={(event) => setEvidence(event.target.files?.[0] ?? null)} required /></label><button className="primary-button" type="submit" disabled={busy}><Gavel size={17} /><span>Открыть спор</span></button></form> : null}<div className="rows">{disputes.map((item) => <div className="clearing-dispute" key={item.id}><div><strong>{item.reason_code}</strong><span>{memberName(item.opened_by_member_id)} · {formatLocalDateTime(item.created_at)}</span></div><p>{item.statement}</p><Status value={item.status} />{canController && item.status === "OPEN" ? <div className="dispute-decision"><input aria-label={`Решение ${item.id}`} value={noteFor(notes, item.id)} onChange={(event) => setNotes((current) => ({ ...current, [item.id]: event.target.value }))} placeholder="Мотивированное решение" /><button type="button" title="Подтвердить возражение" disabled={busy || !(noteFor(notes, item.id).length >= 2)} onClick={() => run(() => decideClearingDispute(cycle, item, "UPHOLD", noteFor(notes, item.id)))}><Gavel size={16} /></button><button type="button" title="Отклонить возражение" disabled={busy || !(noteFor(notes, item.id).length >= 2)} onClick={() => run(() => decideClearingDispute(cycle, item, "REJECT", noteFor(notes, item.id)))}><Check size={16} /></button></div> : null}</div>)}{!disputes.length ? <Empty text="Возражений нет" /> : null}</div></section></div>;
}

function WorkflowBar({ cycle, canOperator, canController, canFinalizer, busy, run }: { cycle: ClearingCycle; canOperator: boolean; canController: boolean; canFinalizer: boolean; busy: boolean; run: (action: () => Promise<unknown>) => void }) {
  const action = cycle.status === "DRAFT" && canOperator ? ["Начать сбор", Play, () => collectClearingCycle(cycle)] as const
    : cycle.status === "COLLECTING" && canOperator ? ["Зафиксировать вход", Snowflake, () => freezeClearingInput(cycle)] as const
      : cycle.status === "INPUT_FROZEN" && canOperator ? ["Рассчитать", Calculator, () => previewClearingCycle(cycle)] as const
        : cycle.status === "PREVIEWED" && canController ? ["Подтвердить расчет", ShieldCheck, () => approveClearingPreview(cycle)] as const
          : cycle.status === "DISPUTE_WINDOW" && canFinalizer ? ["Закрыть окно", LockKeyhole, () => markClearingReady(cycle)] as const
            : cycle.status === "READY_TO_FINALIZE" && canFinalizer ? ["Финализировать", FileCheck2, () => finalizeClearingCycle(cycle)] as const
              : cycle.status === "FINALIZED" && canFinalizer ? ["Сверить", BadgeCheck, () => reconcileClearingCycle(cycle)] as const
                : null;
  const ActionIcon = action?.[1];
  return <aside className="clearing-workflow"><div><span>Текущий этап</span><Status value={cycle.status} /><strong>v{cycle.version}</strong></div>{action && ActionIcon ? <button className="primary-button" type="button" disabled={busy} onClick={() => run(action[2])}><ActionIcon size={17} /><span>{action[0]}</span></button> : <span className="workflow-complete"><BadgeCheck size={17} />Нет доступных команд</span>}</aside>;
}

function Loading() { return <div className="state" role="status"><RefreshCw className="spin" size={22} />Загрузка</div>; }
function Empty({ text }: { text: string }) { return <div className="state"><FileCheck2 size={21} />{text}</div>; }
function ErrorLine({ error }: { error: unknown }) { return <div className="state error" role="alert"><AlertTriangle size={21} />{errorText(error)}</div>; }
