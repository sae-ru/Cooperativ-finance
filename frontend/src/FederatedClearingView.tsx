import {
  AlertTriangle,
  BadgeCheck,
  Ban,
  Check,
  CircleDot,
  FileCheck2,
  Fingerprint,
  GitMerge,
  ListRestart,
  LockKeyhole,
  Network,
  Play,
  Plus,
  RefreshCw,
  Send,
  ShieldCheck,
  TimerReset,
  Waypoints,
} from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useMemo, useState } from "react";

import { AdminApiError, type Principal, type RoleCode } from "./api/admin";
import {
  approveLocalFederatedCycle,
  collectFederatedApprovals,
  collectFederatedSnapshots,
  commitFederatedCycle,
  createFederatedClearingCycle,
  createFederatedClearingPolicy,
  createInterNodeObligation,
  getFederatedClearingCycles,
  getFederatedClearingPolicies,
  getFederatedCycleEvidence,
  getInterNodeObligations,
  prepareFederatedCycle,
  publishFederatedProposal,
  recoverFederatedCycle,
  releaseFederatedCycle,
  type FederatedArtifact,
  type FederatedClearingCycle,
} from "./api/federatedClearing";
import { useSystemStatus } from "./features/system/use-system-status";
import { userErrorMessage } from "./shared/api-error";
import { formatLocalDateTime } from "./shared/date-time";
import { decimalIsPositive, formatDecimal } from "./shared/decimal";
import "./federated-clearing.css";

type Section = "cycles" | "obligations" | "policies";

const statusNames: Record<string, string> = {
  DRAFT: "Черновик",
  COLLECTING_SNAPSHOTS: "Сбор снимков",
  PREPARING_NODES: "Блокировка узлов",
  PREPARED: "Подготовлен",
  PROPOSED: "Расчет опубликован",
  VERIFYING: "Проверка узлами",
  COMMIT_CERTIFIED: "Сертификат выпущен",
  APPLYING: "Применение",
  COMMITTED_PENDING_APPLY: "Ожидает применения",
  RECONCILED: "Сверен",
  PREPARE_EXPIRED: "Подготовка истекла",
  REJECTED: "Отклонен",
  CONFLICT: "Конфликт",
  CANCELLED: "Отменен",
  CONFIRMED: "Подтверждено",
  PARTIALLY_CLEARED: "Частично погашено",
  CLEARED: "Погашено",
  PREPARED_OBLIGATION: "Заблокировано",
  ACTIVE: "Активна",
  SUPERSEDED: "Заменена",
};

const terminalStatuses = new Set([
  "RECONCILED",
  "PREPARE_EXPIRED",
  "REJECTED",
  "CONFLICT",
  "CANCELLED",
]);
const preFinalStatuses = new Set([
  "PREPARING_NODES",
  "PREPARED",
  "PROPOSED",
  "VERIFYING",
]);

function hasRole(principal: Principal, ...roles: RoleCode[]) {
  return principal.roles.some((grant) => roles.includes(grant.role));
}

function errorText(error: unknown): string {
  return userErrorMessage(error);
}

function Status({ value }: { value: string }) {
  const good = ["ACTIVE", "RECONCILED", "CLEARED"].includes(value);
  const bad = ["REJECTED", "CONFLICT", "CANCELLED", "PREPARE_EXPIRED"].includes(value);
  const label = value === "PREPARED" ? statusNames.PREPARED : statusNames[value] ?? value;
  return <span className={`status ${good ? "good" : bad ? "bad" : "warn"}`}>{label}</span>;
}

function Hash({ value }: { value: string | null | undefined }) {
  return <code className="fc-hash" title={value ?? undefined}>{value ?? "—"}</code>;
}

function localDate(offsetDays: number) {
  const date = new Date(Date.now() + offsetDays * 86_400_000);
  date.setMinutes(date.getMinutes() - date.getTimezoneOffset());
  return date.toISOString().slice(0, 16);
}

function formatAmount(value: string) {
  return formatDecimal(value, "ru-RU", { maximumFractionDigits: 12 });
}

function artifactForNode(items: FederatedArtifact[], nodeCode: string) {
  return items.find((item) => item.node_code === nodeCode);
}

function PolicyForm({ run }: { run: (action: () => Promise<unknown>) => void }) {
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    run(() => createFederatedClearingPolicy({
      policy_code: String(data.get("policy_code")),
      policy_version: Number(data.get("policy_version")),
      valuation_unit: String(data.get("valuation_unit")),
      decimal_scale: Number(data.get("decimal_scale")),
      rounding_mode: String(data.get("rounding_mode")) as "DOWN" | "HALF_EVEN",
      minimum_operation: String(data.get("minimum_operation")),
      max_iterations: Number(data.get("max_iterations")),
      max_cycle_length: Number(data.get("max_cycle_length")),
      prepare_ttl_seconds: Number(data.get("prepare_ttl_seconds")),
    }));
  }
  return <form className="fc-create-form" onSubmit={submit}>
    <strong><Fingerprint size={16} /> Новая политика</strong>
    <label>Код<input name="policy_code" placeholder="REGIONAL-WEEKLY" required /></label>
    <label>Версия<input name="policy_version" type="number" min="1" defaultValue="1" required /></label>
    <label>Единица<input name="valuation_unit" defaultValue="DEMO" required /></label>
    <label>Масштаб<input name="decimal_scale" type="number" min="0" max="12" defaultValue="2" required /></label>
    <label>Округление<select name="rounding_mode" defaultValue="DOWN"><option value="DOWN">DOWN</option><option value="HALF_EVEN">HALF_EVEN</option></select></label>
    <label>Минимум<input name="minimum_operation" type="number" min="0" step="any" defaultValue="0.01" required /></label>
    <label>Итерации<input name="max_iterations" type="number" min="1" max="100000" defaultValue="10000" required /></label>
    <label>Длина цикла<input name="max_cycle_length" type="number" min="3" max="12" defaultValue="8" required /></label>
    <label>TTL prepare, сек.<input name="prepare_ttl_seconds" type="number" min="30" max="86400" defaultValue="900" required /></label>
    <button className="primary-button" type="submit"><Plus size={16} /> Создать</button>
  </form>;
}

function ObligationForm({ run }: { run: (action: () => Promise<unknown>) => void }) {
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    run(() => createInterNodeObligation({
      debtor_node_code: String(data.get("debtor")),
      creditor_node_code: String(data.get("creditor")),
      unit_code: String(data.get("unit")),
      amount: String(data.get("amount")),
      source_reference: String(data.get("reference")),
      source_event_hash: String(data.get("source_hash")),
      liquidity_class: String(data.get("liquidity_class")),
    }));
  }
  return <form className="fc-create-form" onSubmit={submit}>
    <strong><GitMerge size={16} /> Новое обязательство</strong>
    <label>Узел-дебитор<input name="debtor" required /></label>
    <label>Узел-кредитор<input name="creditor" required /></label>
    <label>Единица<input name="unit" defaultValue="DEMO" required /></label>
    <label>Сумма<input name="amount" type="number" min="0.000000000001" step="any" required /></label>
    <label>Класс<select name="liquidity_class" defaultValue="STANDARD"><option value="STANDARD">STANDARD</option><option value="PRIORITY">PRIORITY</option><option value="UNASSESSED">UNASSESSED</option></select></label>
    <label>Источник<input name="reference" required /></label>
    <label className="fc-wide">Хеш события<input name="source_hash" pattern="sha256:[0-9a-f]{64}" placeholder="sha256:..." required /></label>
    <button className="primary-button" type="submit"><Plus size={16} /> Зарегистрировать</button>
  </form>;
}

function CycleForm({
  policies,
  run,
}: {
  policies: Array<{ id: string; policy_code: string; status: string }>;
  run: (action: () => Promise<unknown>) => void;
}) {
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    run(() => createFederatedClearingCycle({
      cycle_code: String(data.get("cycle_code")),
      policy_id: String(data.get("policy_id")),
      period_start: new Date(String(data.get("period_start"))).toISOString(),
      period_end: new Date(String(data.get("period_end"))).toISOString(),
      participant_node_codes: String(data.get("nodes"))
        .split(",")
        .map((item) => item.trim().toLowerCase())
        .filter(Boolean),
    }));
  }
  return <form className="fc-create-form" onSubmit={submit}>
    <strong><Waypoints size={16} /> Новый цикл</strong>
    <label>Код<input name="cycle_code" placeholder="REGION-WEEK-01" required /></label>
    <label>Политика<select name="policy_id" required defaultValue=""><option value="">Выберите</option>{policies.filter((item) => item.status === "ACTIVE").map((item) => <option value={item.id} key={item.id}>{item.policy_code}</option>)}</select></label>
    <label>Начало<input name="period_start" type="datetime-local" defaultValue={localDate(-7)} required /></label>
    <label>Конец<input name="period_end" type="datetime-local" defaultValue={localDate(0)} required /></label>
    <label className="fc-wide">Узлы через запятую<input name="nodes" placeholder="node-01, node-02, node-03" required /></label>
    <button className="primary-button" type="submit"><Plus size={16} /> Открыть цикл</button>
  </form>;
}

function WorkflowActions({
  cycle,
  evidence,
  localNodeCode,
  principal,
  pending,
  run,
}: {
  cycle: FederatedClearingCycle;
  evidence: Awaited<ReturnType<typeof getFederatedCycleEvidence>>;
  localNodeCode: string;
  principal: Principal;
  pending: boolean;
  run: (action: () => Promise<unknown>) => void;
}) {
  const operator = hasRole(principal, "CLEARING_OPERATOR", "NODE_BUSINESS_OPERATOR");
  const controller = hasRole(principal, "CLEARING_CONTROLLER");
  const finalizer = hasRole(principal, "CLEARING_FINALIZER");
  const coordinator = cycle.coordinator_node_code.toLowerCase() === localNodeCode;
  const allSnapshots = evidence.snapshots.length === cycle.participant_node_codes.length;
  const allApprovals = evidence.approvals.length === cycle.affected_node_codes.length;
  const localApproval = artifactForNode(evidence.approvals, localNodeCode);
  return <div className="fc-actions" aria-label="Действия цикла">
    {operator && coordinator && ["DRAFT", "COLLECTING_SNAPSHOTS"].includes(cycle.status)
      ? <button disabled={pending} onClick={() => run(() => collectFederatedSnapshots(cycle.id))}><RefreshCw size={16} /> Снимки</button>
      : null}
    {operator && coordinator && allSnapshots && ["DRAFT", "COLLECTING_SNAPSHOTS"].includes(cycle.status)
      ? <button disabled={pending} onClick={() => run(() => prepareFederatedCycle(cycle.id))}><LockKeyhole size={16} /> Prepare</button>
      : null}
    {operator && coordinator && cycle.status === "PREPARED"
      ? <button disabled={pending} onClick={() => run(() => publishFederatedProposal(cycle.id))}><Send size={16} /> Опубликовать</button>
      : null}
    {operator && coordinator && ["PROPOSED", "VERIFYING"].includes(cycle.status)
      ? <button disabled={pending} onClick={() => run(() => collectFederatedApprovals(cycle.id))}><RefreshCw size={16} /> Подписи</button>
      : null}
    {controller && cycle.affected_node_codes.includes(localNodeCode) && !localApproval && ["PROPOSED", "VERIFYING"].includes(cycle.status)
      ? <button className="primary-button" disabled={pending} onClick={() => run(() => approveLocalFederatedCycle(cycle.id))}><Check size={16} /> Подтвердить расчет</button>
      : null}
    {finalizer && coordinator && allApprovals && ["PROPOSED", "VERIFYING"].includes(cycle.status)
      ? <button className="danger-button" disabled={pending} onClick={() => run(() => commitFederatedCycle(cycle.id))}><FileCheck2 size={16} /> Выпустить сертификат</button>
      : null}
    {finalizer && coordinator && ["COMMIT_CERTIFIED", "APPLYING", "COMMITTED_PENDING_APPLY"].includes(cycle.status)
      ? <button className="primary-button" disabled={pending} onClick={() => run(() => recoverFederatedCycle(cycle.id))}><ListRestart size={16} /> Довести применение</button>
      : null}
    {finalizer && coordinator && preFinalStatuses.has(cycle.status)
      ? <button disabled={pending} onClick={() => run(() => releaseFederatedCycle(cycle.id))}><Ban size={16} /> Освободить</button>
      : null}
    {terminalStatuses.has(cycle.status) ? <span className="fc-terminal"><BadgeCheck size={16} /> Цикл закрыт</span> : null}
  </div>;
}

export default function FederatedClearingView({ principal }: { principal: Principal }) {
  const queryClient = useQueryClient();
  const system = useSystemStatus();
  const policies = useQuery({ queryKey: ["federated-clearing-policies"], queryFn: getFederatedClearingPolicies });
  const obligations = useQuery({ queryKey: ["inter-node-obligations"], queryFn: getInterNodeObligations });
  const cycles = useQuery({ queryKey: ["federated-clearing-cycles"], queryFn: getFederatedClearingCycles });
  const [section, setSection] = useState<Section>("cycles");
  const [selectedId, setSelectedId] = useState("");
  const [createMode, setCreateMode] = useState<"policy" | "obligation" | "cycle" | null>(null);

  useEffect(() => {
    if (!cycles.data?.length) return;
    if (!cycles.data.some((item) => item.id === selectedId)) setSelectedId(cycles.data[0]!.id);
  }, [cycles.data, selectedId]);

  const evidence = useQuery({
    queryKey: ["federated-clearing-evidence", selectedId],
    queryFn: () => getFederatedCycleEvidence(selectedId),
    enabled: Boolean(selectedId),
  });
  const selected = cycles.data?.find((item) => item.id === selectedId);
  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["federated-clearing-policies"] }),
      queryClient.invalidateQueries({ queryKey: ["inter-node-obligations"] }),
      queryClient.invalidateQueries({ queryKey: ["federated-clearing-cycles"] }),
      queryClient.invalidateQueries({ queryKey: ["federated-clearing-evidence"] }),
      queryClient.invalidateQueries({ queryKey: ["operations-snapshot"] }),
    ]);
  };
  const command = useMutation({
    mutationFn: (action: () => Promise<unknown>) => action(),
    onSuccess: async () => {
      setCreateMode(null);
      await refresh();
    },
  });
  const run = (action: () => Promise<unknown>) => command.mutate(action);
  const localNodeCode = system.data?.node.code.toLowerCase() ?? "";
  const counts = useMemo(() => ({
    pending: cycles.data?.filter((item) => ["COMMIT_CERTIFIED", "APPLYING", "COMMITTED_PENDING_APPLY"].includes(item.status)).length ?? 0,
    active: cycles.data?.filter((item) => !terminalStatuses.has(item.status)).length ?? 0,
    prepared: obligations.data?.filter((item) => item.status === "PREPARED").length ?? 0,
    unsettled: obligations.data?.filter((item) => decimalIsPositive(item.outstanding_amount)).length ?? 0,
  }), [cycles.data, obligations.data]);

  if (policies.isPending || obligations.isPending || cycles.isPending || system.isPending) {
    return <div className="state" role="status"><RefreshCw className="spin" size={24} /><span>Загрузка</span></div>;
  }
  const queryError = policies.error ?? obligations.error ?? cycles.error ?? system.error;
  if (queryError) return <div className="state error" role="alert"><AlertTriangle size={24} /><strong>{errorText(queryError)}</strong></div>;

  const canPolicy = hasRole(principal, "CLEARING_FINALIZER", "RISK_ADMIN");
  const canOperate = hasRole(principal, "CLEARING_OPERATOR", "NODE_BUSINESS_OPERATOR");
  return <div className="view-stack fc-view">
    <header className="view-header">
      <div><span className="eyebrow">FEDERATED_NETTING / 1.0.0</span><h1>Межузловой клиринг</h1><p>{localNodeCode} · {cycles.data?.length ?? 0} циклов</p></div>
      <button className="icon-button" title="Обновить" onClick={() => void refresh()}><RefreshCw size={18} /></button>
    </header>

    <section className="fc-metrics" aria-label="Сводка межузлового клиринга">
      <div><Network size={17} /><span>Активные циклы</span><strong>{counts.active}</strong></div>
      <div><TimerReset size={17} /><span>Ожидают применения</span><strong>{counts.pending}</strong></div>
      <div><LockKeyhole size={17} /><span>Заблокированы</span><strong>{counts.prepared}</strong></div>
      <div><GitMerge size={17} /><span>Открытые обязательства</span><strong>{counts.unsettled}</strong></div>
    </section>

    <div className="fc-toolbar">
      <div className="segmented" role="tablist" aria-label="Раздел межузлового клиринга">
        <button className={section === "cycles" ? "active" : ""} onClick={() => setSection("cycles")}>Циклы</button>
        <button className={section === "obligations" ? "active" : ""} onClick={() => setSection("obligations")}>Обязательства</button>
        <button className={section === "policies" ? "active" : ""} onClick={() => setSection("policies")}>Политики</button>
      </div>
      <div className="fc-create-buttons">
        {canOperate ? <button title="Новое обязательство" onClick={() => setCreateMode(createMode === "obligation" ? null : "obligation")}><GitMerge size={16} /><span>Обязательство</span></button> : null}
        {canOperate ? <button title="Новый цикл" onClick={() => setCreateMode(createMode === "cycle" ? null : "cycle")}><Waypoints size={16} /><span>Цикл</span></button> : null}
        {canPolicy ? <button title="Новая политика" onClick={() => setCreateMode(createMode === "policy" ? null : "policy")}><Fingerprint size={16} /><span>Политика</span></button> : null}
      </div>
    </div>

    {createMode ? <section className="fc-command-band">
      {createMode === "policy" ? <PolicyForm run={run} /> : createMode === "obligation" ? <ObligationForm run={run} /> : <CycleForm policies={policies.data ?? []} run={run} />}
    </section> : null}
    {command.isError ? <p className="fc-error" role="alert">{errorText(command.error)}</p> : null}

    {section === "cycles" ? <section className="panel fc-cycle-workspace">
      <div className="fc-cycle-list">
        {(cycles.data ?? []).map((cycle) => <button className={cycle.id === selectedId ? "active" : ""} onClick={() => setSelectedId(cycle.id)} key={cycle.id}>
          <span><strong>{cycle.cycle_code}</strong><small>{cycle.coordinator_node_code} · v{cycle.version}</small></span><Status value={cycle.status} />
        </button>)}
        {!cycles.data?.length ? <div className="state"><CircleDot size={20} /><span>Циклов нет</span></div> : null}
      </div>
      <div className="fc-cycle-detail">
        {!selected || evidence.isPending ? <div className="state"><RefreshCw className="spin" size={22} /><span>Загрузка цикла</span></div> : evidence.isError || !evidence.data ? <div className="state error"><AlertTriangle size={22} /><strong>{errorText(evidence.error)}</strong></div> : <>
          <div className="fc-cycle-title"><div><span>{selected.coordinator_node_code}</span><h2>{selected.cycle_code}</h2></div><Status value={selected.status} /></div>
          {evidence.data.certificate ? <div className="fc-finality"><ShieldCheck size={18} /><div><strong>Экономическая финальность</strong><span>Сертификат зафиксирован. Допустимо только повторное идемпотентное применение.</span></div><Hash value={selected.certificate_hash} /></div> : null}
          <WorkflowActions cycle={selected} evidence={evidence.data} localNodeCode={localNodeCode} principal={principal} pending={command.isPending} run={run} />
          <div className="fc-integrity">
            <div><span>Вход</span><Hash value={selected.input_hash} /></div>
            <div><span>Результат</span><Hash value={selected.result_hash} /></div>
            <div><span>Сертификат</span><Hash value={selected.certificate_hash} /></div>
            <div><span>Период</span><strong>{formatLocalDateTime(selected.period_start)}<br />{formatLocalDateTime(selected.period_end)}</strong></div>
          </div>
          <div className="table-wrap"><table className="fc-node-table"><thead><tr><th>Узел</th><th>Снимок</th><th>Prepare</th><th>Подпись</th><th>Применение</th></tr></thead><tbody>{selected.participant_node_codes.map((node) => {
            const snapshot = artifactForNode(evidence.data.snapshots, node);
            const prepare = artifactForNode(evidence.data.prepare_receipts, node);
            const approval = artifactForNode(evidence.data.approvals, node);
            const apply = artifactForNode(evidence.data.apply_receipts, node);
            return <tr key={node}><td><strong>{node}</strong>{node === selected.coordinator_node_code ? <small>координатор</small> : null}</td><td><EvidenceCell artifact={snapshot} /></td><td><EvidenceCell artifact={prepare} /></td><td><EvidenceCell artifact={approval} /></td><td><EvidenceCell artifact={apply} /></td></tr>;
          })}</tbody></table></div>
          <div className="fc-artifacts">
            <ArtifactBlock label="Предложение" artifact={evidence.data.proposal} />
            <ArtifactBlock label="Commit certificate" artifact={evidence.data.certificate} />
            <ArtifactBlock label="Доказательство сверки" artifact={evidence.data.proof} />
          </div>
        </>}
      </div>
    </section> : section === "obligations" ? <section className="panel"><div className="panel-heading"><h2>Межузловые обязательства</h2><span>{obligations.data?.length ?? 0}</span></div><div className="table-wrap"><table className="fc-obligation-table"><thead><tr><th>Стороны</th><th>Единица</th><th>Исходно</th><th>Остаток</th><th>Статус</th><th>Источник</th><th>Prepare до</th></tr></thead><tbody>{obligations.data?.map((item) => <tr key={item.id}><td><strong>{item.debtor_node_code}</strong><small>→ {item.creditor_node_code}</small></td><td>{item.unit_code}</td><td>{formatAmount(item.original_amount)}</td><td>{formatAmount(item.outstanding_amount)}</td><td><Status value={item.status === "PREPARED" ? "PREPARED_OBLIGATION" : item.status} /></td><td><span>{item.source_reference}</span><Hash value={item.source_event_hash} /></td><td>{item.prepared_until ? formatLocalDateTime(item.prepared_until) : "—"}</td></tr>)}</tbody></table></div></section> : <section className="panel"><div className="panel-heading"><h2>Политики межузлового расчета</h2><span>{policies.data?.length ?? 0}</span></div><div className="table-wrap"><table className="fc-policy-table"><thead><tr><th>Политика</th><th>Алгоритм</th><th>Единица</th><th>Пределы</th><th>TTL</th><th>Хеш</th><th>Статус</th></tr></thead><tbody>{policies.data?.map((item) => <tr key={item.id}><td><strong>{item.policy_code}</strong><small>v{item.policy_version}</small></td><td>{item.algorithm_id}<small>{item.algorithm_version}</small></td><td>{item.valuation_unit}</td><td>{item.max_cycle_length} узлов · {item.max_iterations} итераций</td><td>{item.prepare_ttl_seconds} сек.</td><td><Hash value={item.policy_hash} /></td><td><Status value={item.status} /></td></tr>)}</tbody></table></div></section>}
  </div>;
}

function EvidenceCell({ artifact }: { artifact: FederatedArtifact | undefined }) {
  return artifact ? <span className="fc-evidence-ok"><BadgeCheck size={15} /><Hash value={artifact.hash} /></span> : <span className="fc-evidence-wait"><CircleDot size={14} /> ожидается</span>;
}

function ArtifactBlock({ label, artifact }: { label: string; artifact: FederatedArtifact | null }) {
  return <details><summary>{artifact ? <BadgeCheck size={15} /> : <Play size={15} />}{label}</summary>{artifact ? <><Hash value={artifact.hash} /><pre>{JSON.stringify(artifact.payload, null, 2)}</pre></> : <p>Еще не сформировано</p>}</details>;
}
