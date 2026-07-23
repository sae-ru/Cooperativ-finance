import {
  AlertTriangle,
  CheckCheck,
  ClipboardCheck,
  FileWarning,
  Handshake,
  PackageCheck,
  Plus,
  RefreshCw,
  Route,
  Send,
  TimerOff,
  Trash2,
} from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useMemo, useState } from "react";

import { AdminApiError, getCooperatives, type Principal, type RoleCode } from "./api/admin";
import {
  acceptFulfillment,
  confirmDeal,
  createLogisticsOrder,
  getDeal,
  getDeals,
  getDisputes,
  getFulfillments,
  getLogisticsOrders,
  getObligations,
  markOverdue,
  openDispute,
  proposeDeal,
  resolveDispute,
  reviseDeal,
  submitFulfillment,
  transitionLogisticsOrder,
  type Deal,
  type DealDetail,
  type Fulfillment,
  type Obligation,
  type ObligationDraft,
} from "./api/exchange";
import { getInventoryMembers, getUnits, uploadEvidence } from "./api/inventory";
import { formatLocalDateTime } from "./shared/date-time";
import "./exchange.css";

type Section = "deals" | "editor" | "execution" | "logistics" | "disputes";
type DraftInput = Omit<ObligationDraft, "due_at"> & { key: number; due_at: string };

const statusNames: Record<string, string> = {
  PROPOSED: "На согласовании",
  ACTIVE: "Исполняется",
  PARTIALLY_FULFILLED: "Частично исполнено",
  FULFILLED: "Исполнено",
  OVERDUE: "Просрочено",
  DISPUTED: "Спор",
  DEFAULTED: "Дефолт",
  CLOSED: "Закрыто",
  SUBMITTED: "Предъявлено",
  ACCEPTED: "Принято",
  PARTIALLY_ACCEPTED: "Принято частично",
  REJECTED: "Отклонено",
  OFFERED: "Предложено",
  IN_TRANSIT: "В пути",
  DELIVERED: "Доставлено",
  OPEN: "Открыт",
  RESOLVED: "Разрешён",
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

function statusKind(value: string): "good" | "warn" | "bad" {
  if (["ACTIVE", "FULFILLED", "ACCEPTED", "DELIVERED"].includes(value)) return "good";
  if (["DISPUTED", "DEFAULTED", "REJECTED", "OVERDUE"].includes(value)) return "bad";
  return "warn";
}

function Status({ value }: { value: string }) {
  return <span className={`status ${statusKind(value)}`}>{statusNames[value] ?? value}</span>;
}

function shortId(value: string): string {
  return value.slice(0, 8);
}

function localInputDate(value: Date): string {
  const offset = value.getTimezoneOffset() * 60_000;
  return new Date(value.getTime() - offset).toISOString().slice(0, 16);
}

let draftKey = 0;
function emptyDraft(): DraftInput {
  return {
    key: ++draftKey,
    debtor_member_id: "",
    creditor_member_id: "",
    subject_type: "PRODUCT",
    subject_id: null,
    description: "",
    quality_criteria: "",
    fulfillment_place: "",
    due_at: localInputDate(new Date(Date.now() + 86_400_000)),
    unit_id: "",
    quantity: "",
    partial_allowed: true,
    evidence_required: true,
    confirmation_method: "Акт приёмки получателя",
    substitute_policy: "Только по новому подтверждению обеих сторон",
    valuation_source: "Без денежной оценки",
    liquidity_class: "UNASSESSED",
    clearing_allowed: false,
  };
}

function termsDrafts(detail: DealDetail): DraftInput[] {
  const values = detail.terms.obligations;
  if (!Array.isArray(values)) return [emptyDraft()];
  return values.map((value) => {
    const item = value as Record<string, unknown>;
    return {
      ...emptyDraft(),
      debtor_member_id: String(item.debtor_member_id ?? ""),
      creditor_member_id: String(item.creditor_member_id ?? ""),
      subject_type: String(item.subject_type ?? "OTHER") as ObligationDraft["subject_type"],
      subject_id: item.subject_id ? String(item.subject_id) : null,
      description: String(item.description ?? ""),
      quality_criteria: String(item.quality_criteria ?? ""),
      fulfillment_place: String(item.fulfillment_place ?? ""),
      due_at: localInputDate(new Date(String(item.due_at))),
      unit_id: String(item.unit_id ?? ""),
      quantity: String(item.quantity ?? ""),
      partial_allowed: Boolean(item.partial_allowed),
      evidence_required: Boolean(item.evidence_required),
      confirmation_method: String(item.confirmation_method ?? ""),
      substitute_policy: String(item.substitute_policy ?? ""),
      valuation_source: String(item.valuation_source ?? ""),
      liquidity_class: String(item.liquidity_class ?? "UNASSESSED"),
      clearing_allowed: Boolean(item.clearing_allowed),
    };
  });
}

export default function ExchangeView({ principal }: { principal: Principal }) {
  const queryClient = useQueryClient();
  const [section, setSection] = useState<Section>("deals");
  const [selectedDealId, setSelectedDealId] = useState("");
  const [editing, setEditing] = useState<DealDetail | null>(null);
  const deals = useQuery({ queryKey: ["exchange-deals"], queryFn: getDeals });
  const obligations = useQuery({ queryKey: ["exchange-obligations"], queryFn: getObligations });
  const logistics = useQuery({ queryKey: ["exchange-logistics"], queryFn: getLogisticsOrders });
  const disputes = useQuery({ queryKey: ["exchange-disputes"], queryFn: getDisputes });
  const members = useQuery({ queryKey: ["inventory-members"], queryFn: getInventoryMembers });
  const units = useQuery({ queryKey: ["inventory-units"], queryFn: getUnits });
  const cooperatives = useQuery({ queryKey: ["cooperatives"], queryFn: getCooperatives });
  const detail = useQuery({
    queryKey: ["exchange-deal", selectedDealId],
    queryFn: () => getDeal(selectedDealId),
    enabled: Boolean(selectedDealId),
  });
  const canAdmin = hasRole(principal, "COOPERATIVE_ADMIN");
  const canLogistics = hasRole(principal, "LOGISTICS_OPERATOR");
  const canScan = hasRole(principal, "COOPERATIVE_ADMIN", "RISK_ADMIN", "AUDITOR");
  const canResolve = hasRole(principal, "COOPERATIVE_ADMIN", "RISK_ADMIN", "AUDITOR");
  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["exchange-deals"] }),
      queryClient.invalidateQueries({ queryKey: ["exchange-obligations"] }),
      queryClient.invalidateQueries({ queryKey: ["exchange-logistics"] }),
      queryClient.invalidateQueries({ queryKey: ["exchange-disputes"] }),
      queryClient.invalidateQueries({ queryKey: ["exchange-deal"] }),
    ]);
  };
  const confirm = useMutation({ mutationFn: confirmDeal, onSuccess: refresh });
  const data = deals.data ?? [];
  const obligationData = obligations.data ?? [];
  const logisticsData = logistics.data ?? [];
  const disputeData = disputes.data ?? [];
  const loading = [deals, obligations, logistics, disputes, members, units, cooperatives].some(
    (query) => query.isPending,
  );
  const failed = [deals, obligations, logistics, disputes, members, units, cooperatives].find(
    (query) => query.isError,
  );

  if (loading) {
    return <div className="view-stack"><div className="state"><RefreshCw className="spin" size={24} />Загрузка сделок</div></div>;
  }
  if (failed) return <div className="view-stack"><div className="state error">{errorText(failed.error)}</div></div>;

  const sections: Array<[Section, string, typeof Handshake]> = [
    ["deals", "Сделки", Handshake],
    ["execution", "Исполнение", ClipboardCheck],
    ["logistics", "Логистика", Route],
    ["disputes", "Споры", FileWarning],
  ];
  if (canAdmin) sections.splice(1, 0, ["editor", editing ? "Новая версия" : "Новая сделка", Plus]);
  const selected = detail.data;
  const isCurrentParty = selected?.parties.some((item) => item.member_id === principal.member_id);
  const alreadyConfirmed = selected?.confirmations.some(
    (item) => item.member_id === principal.member_id,
  );

  return (
    <div className="view-stack exchange-view">
      <header className="view-header">
        <div><span className="eyebrow">ЛОКАЛЬНЫЙ ОБМЕН</span><h1>Сделки и обязательства</h1><p>Версия условий, личное подтверждение и фактическое исполнение</p></div>
        <div className="section-tabs">{sections.map(([key, label, Icon]) => <button className={section === key ? "active" : ""} onClick={() => setSection(key)} key={key}><Icon size={15} /><span>{label}</span></button>)}</div>
      </header>
      <section className="metric-grid exchange-metrics">
        <article className="metric"><Handshake size={19} /><span>Сделки</span><strong>{data.length}</strong></article>
        <article className="metric"><ClipboardCheck size={19} /><span>Обязательства</span><strong>{obligationData.length}</strong></article>
        <article className="metric"><TimerOff size={19} /><span>Просрочено</span><strong>{obligationData.filter((item) => item.status === "OVERDUE").length}</strong></article>
        <article className="metric"><AlertTriangle size={19} /><span>Открытые споры</span><strong>{disputeData.filter((item) => item.status === "OPEN").length}</strong></article>
      </section>

      {section === "deals" ? <>
        <section className="panel"><div className="panel-heading"><h2>Реестр сделок</h2><span>{data.length}</span></div><div className="table-wrap"><table className="exchange-table"><thead><tr><th>Статус</th><th>Сделка</th><th>Условия</th><th>Подтверждена</th><th></th></tr></thead><tbody>{data.map((deal) => <tr key={deal.id}><td><Status value={deal.status} /></td><td><strong>{deal.title}</strong><small>{shortId(deal.id)} · v{deal.version}</small></td><td><code>v{deal.terms_version} · {deal.terms_hash.slice(7, 19)}</code></td><td>{deal.confirmed_at ? formatLocalDateTime(deal.confirmed_at) : "—"}</td><td><button className="compact-command" onClick={() => setSelectedDealId(deal.id)}>Открыть</button></td></tr>)}</tbody></table></div></section>
        {selected ? <section className="panel deal-detail"><div className="panel-heading"><h2>{selected.deal.title}</h2><Status value={selected.deal.status} /></div><div className="deal-terms-strip"><div><span>Версия</span><strong>{selected.deal.terms_version}</strong></div><div><span>Хэш условий</span><code>{selected.deal.terms_hash}</code></div><div><span>Стороны</span><strong>{selected.confirmations.length} / {selected.parties.length}</strong></div><div className="deal-actions">{selected.deal.status === "PROPOSED" && isCurrentParty && !alreadyConfirmed ? <button className="primary-button" onClick={() => confirm.mutate(selected.deal)} disabled={confirm.isPending}><CheckCheck size={16} />Подтвердить</button> : null}{canAdmin && selected.deal.status === "PROPOSED" ? <button className="secondary-button" onClick={() => { setEditing(selected); setSection("editor"); }}>Новая версия</button> : null}</div></div><div className="table-wrap"><table><thead><tr><th>№</th><th>Должник</th><th>Получатель</th><th>Предмет</th><th>Количество</th><th>Срок</th><th>Статус</th></tr></thead><tbody>{selected.obligations.length ? selected.obligations.map((item) => <ObligationRow key={item.id} item={item} members={members.data ?? []} units={units.data ?? []} />) : <tr><td colSpan={7}>Обязательства появятся после подтверждения всех сторон</td></tr>}</tbody></table></div>{confirm.isError ? <p className="form-error panel-error">{errorText(confirm.error)}</p> : null}</section> : null}
      </> : null}

      {section === "editor" && canAdmin ? <DealEditor cooperativeId={cooperatives.data?.[0]?.id ?? ""} members={members.data ?? []} units={units.data ?? []} editing={editing} onDone={async () => { setEditing(null); setSection("deals"); await refresh(); }} /> : null}
      {section === "execution" ? <ExecutionPanel principal={principal} obligations={obligationData} members={members.data ?? []} units={units.data ?? []} logistics={logisticsData} onDone={refresh} /> : null}
      {section === "logistics" ? <LogisticsPanel principal={principal} obligations={obligationData} orders={logisticsData} members={members.data ?? []} units={units.data ?? []} canAdmin={canAdmin} canLogistics={canLogistics} onDone={refresh} /> : null}
      {section === "disputes" ? <DisputePanel principal={principal} cooperativeId={cooperatives.data?.[0]?.id ?? ""} obligations={obligationData} disputes={disputeData} members={members.data ?? []} canScan={canScan} canResolve={canResolve} onDone={refresh} /> : null}
    </div>
  );
}

function ObligationRow({ item, members, units }: { item: Obligation; members: Array<{ member_id: string; display_name: string }>; units: Array<{ id: string; symbol: string }> }) {
  const member = (id: string) => members.find((value) => value.member_id === id)?.display_name ?? shortId(id);
  const unit = units.find((value) => value.id === item.unit_id)?.symbol ?? "";
  return <tr><td>{item.sequence_no}</td><td><strong>{member(item.debtor_member_id)}</strong></td><td><strong>{member(item.creditor_member_id)}</strong></td><td>{item.description}<small>{item.quality_criteria}</small></td><td><strong>{item.quantity_fulfilled} / {item.quantity_total} {unit}</strong><small>предъявлено {item.quantity_submitted} · зачтено {item.quantity_cleared}</small></td><td>{formatLocalDateTime(item.due_at)}</td><td><Status value={item.status} /></td></tr>;
}

function DealEditor({ cooperativeId, members, units, editing, onDone }: { cooperativeId: string; members: Array<{ member_id: string; display_name: string }>; units: Array<{ id: string; symbol: string; name: string }>; editing: DealDetail | null; onDone: () => Promise<void> }) {
  const [title, setTitle] = useState(editing?.deal.title ?? "");
  const [drafts, setDrafts] = useState<DraftInput[]>(editing ? termsDrafts(editing) : [emptyDraft()]);
  useEffect(() => { setTitle(editing?.deal.title ?? ""); setDrafts(editing ? termsDrafts(editing) : [emptyDraft()]); }, [editing]);
  const mutation = useMutation({ mutationFn: () => {
    const obligations: ObligationDraft[] = drafts.map(({ key: _key, due_at, ...item }) => ({ ...item, subject_id: item.subject_id || null, due_at: new Date(due_at).toISOString() }));
    return editing ? reviseDeal(editing.deal, { title, obligations }) : proposeDeal({ cooperative_id: cooperativeId, title, obligations });
  }, onSuccess: onDone });
  const update = (key: number, patch: Partial<DraftInput>) => setDrafts((items) => items.map((item) => item.key === key ? { ...item, ...patch } : item));
  return <section className="panel deal-editor"><div className="panel-heading"><h2>{editing ? `Новая версия условий v${editing.deal.terms_version + 1}` : "Предложить сделку"}</h2><span>{drafts.length} обязательств</span></div><form onSubmit={(event) => { event.preventDefault(); mutation.mutate(); }}><label className="deal-title">Название сделки<input value={title} onChange={(event) => setTitle(event.target.value)} required /></label>{drafts.map((draft, index) => <fieldset key={draft.key}><legend>Обязательство {index + 1}</legend><button type="button" className="icon-button remove-draft" title="Удалить обязательство" disabled={drafts.length === 1} onClick={() => setDrafts((items) => items.filter((item) => item.key !== draft.key))}><Trash2 size={15} /></button><label>Должник<select value={draft.debtor_member_id} onChange={(event) => update(draft.key, { debtor_member_id: event.target.value })} required><option value="">Выберите</option>{members.map((item) => <option key={item.member_id} value={item.member_id}>{item.display_name}</option>)}</select></label><label>Получатель<select value={draft.creditor_member_id} onChange={(event) => update(draft.key, { creditor_member_id: event.target.value })} required><option value="">Выберите</option>{members.map((item) => <option key={item.member_id} value={item.member_id}>{item.display_name}</option>)}</select></label><label>Тип<select value={draft.subject_type} onChange={(event) => update(draft.key, { subject_type: event.target.value as ObligationDraft["subject_type"] })}><option value="PRODUCT">Товар</option><option value="SERVICE">Услуга</option><option value="LOGISTICS">Логистика</option><option value="OTHER">Иное</option></select></label><label>Единица<select value={draft.unit_id} onChange={(event) => update(draft.key, { unit_id: event.target.value })} required><option value="">Выберите</option>{units.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.symbol}</option>)}</select></label><label>Количество<input inputMode="decimal" value={draft.quantity} onChange={(event) => update(draft.key, { quantity: event.target.value })} required /></label><label>Срок<input type="datetime-local" value={draft.due_at} onChange={(event) => update(draft.key, { due_at: event.target.value })} required /></label><label className="span-two">Предмет<input value={draft.description} onChange={(event) => update(draft.key, { description: event.target.value })} required /></label><label className="span-two">Критерии качества<input value={draft.quality_criteria} onChange={(event) => update(draft.key, { quality_criteria: event.target.value })} required /></label><label className="span-two">Место исполнения<input value={draft.fulfillment_place} onChange={(event) => update(draft.key, { fulfillment_place: event.target.value })} required /></label><label>Подтверждение<input value={draft.confirmation_method} onChange={(event) => update(draft.key, { confirmation_method: event.target.value })} required /></label><label>Источник оценки<input value={draft.valuation_source} onChange={(event) => update(draft.key, { valuation_source: event.target.value })} required /></label><label className="span-two">Замена предмета<input value={draft.substitute_policy} onChange={(event) => update(draft.key, { substitute_policy: event.target.value })} required /></label><label className="check-field"><input type="checkbox" checked={draft.partial_allowed} onChange={(event) => update(draft.key, { partial_allowed: event.target.checked })} />Частичное исполнение</label><label className="check-field"><input type="checkbox" checked={draft.evidence_required} onChange={(event) => update(draft.key, { evidence_required: event.target.checked })} />Доказательства обязательны</label><label className="check-field"><input type="checkbox" checked={draft.clearing_allowed} onChange={(event) => update(draft.key, { clearing_allowed: event.target.checked })} />Разрешить клиринг</label></fieldset>)}<div className="editor-actions"><button type="button" className="secondary-button" disabled={drafts.length >= 20} onClick={() => setDrafts((items) => [...items, emptyDraft()])}><Plus size={16} />Добавить обязательство</button><button className="primary-button" disabled={mutation.isPending}><Send size={16} />{editing ? "Создать версию" : "Предложить"}</button></div></form>{mutation.isError ? <p className="form-error panel-error">{errorText(mutation.error)}</p> : null}</section>;
}

function ExecutionPanel({ principal, obligations, members, units, logistics, onDone }: { principal: Principal; obligations: Obligation[]; members: Array<{ member_id: string; display_name: string }>; units: Array<{ id: string; symbol: string }>; logistics: Awaited<ReturnType<typeof getLogisticsOrders>>; onDone: () => Promise<void> }) {
  const [selectedId, setSelectedId] = useState(obligations[0]?.id ?? "");
  const selected = obligations.find((item) => item.id === selectedId);
  const operable = selected
    ? ["ACTIVE", "PARTIALLY_FULFILLED", "OVERDUE"].includes(selected.status)
    : false;
  const fulfillments = useQuery({ queryKey: ["exchange-fulfillments", selectedId], queryFn: () => getFulfillments(selectedId), enabled: Boolean(selectedId) });
  const [quantity, setQuantity] = useState(""); const [quality, setQuality] = useState(""); const [location, setLocation] = useState(""); const [logisticsId, setLogisticsId] = useState(""); const [submitFile, setSubmitFile] = useState<File | null>(null);
  const [acceptId, setAcceptId] = useState(""); const [accepted, setAccepted] = useState(""); const [qualityStatus, setQualityStatus] = useState(""); const [notes, setNotes] = useState(""); const [acceptFile, setAcceptFile] = useState<File | null>(null);
  const submit = useMutation({ mutationFn: async () => { if (!selected || !submitFile) throw new Error("selection"); const evidenceId = await uploadEvidence(selected.cooperative_id, submitFile, "FULFILLMENT_ACT"); return submitFulfillment(selected, { quantity, quality_claim: quality, location_text: location, performed_at: new Date().toISOString(), logistics_order_id: logisticsId || null, evidence_ids: [evidenceId] }); }, onSuccess: async () => { setQuantity(""); setSubmitFile(null); await fulfillments.refetch(); await onDone(); } });
  const pending = (fulfillments.data ?? []).filter((item) => item.status === "SUBMITTED");
  const acceptance = useMutation({ mutationFn: async () => { const fulfillment = pending.find((item) => item.id === acceptId); if (!selected || !fulfillment || !acceptFile) throw new Error("selection"); const evidenceId = await uploadEvidence(selected.cooperative_id, acceptFile, "ACCEPTANCE_ACT"); return acceptFulfillment(selected, fulfillment, { accepted_quantity: accepted, quality_status: qualityStatus, notes, evidence_ids: [evidenceId] }); }, onSuccess: async () => { setAcceptId(""); setAcceptFile(null); await fulfillments.refetch(); await onDone(); } });
  return <><section className="panel"><div className="panel-heading"><h2>Обязательства</h2><span>{obligations.length}</span></div><div className="exchange-selector"><label>Обязательство<select value={selectedId} onChange={(event) => setSelectedId(event.target.value)}><option value="">Выберите</option>{obligations.map((item) => <option key={item.id} value={item.id}>{shortId(item.id)} · {item.description}</option>)}</select></label></div><div className="table-wrap"><table><thead><tr><th>№</th><th>Должник</th><th>Получатель</th><th>Предмет</th><th>Количество</th><th>Срок</th><th>Статус</th></tr></thead><tbody>{selected ? <ObligationRow item={selected} members={members} units={units} /> : null}</tbody></table></div></section>{selected && operable && principal.member_id === selected.debtor_member_id ? <section className="panel exchange-command"><div className="panel-heading"><h2>Предъявить исполнение</h2><PackageCheck size={18} /></div><form onSubmit={(event) => { event.preventDefault(); submit.mutate(); }}><label>Количество<input value={quantity} onChange={(event) => setQuantity(event.target.value)} required /></label><label>Доставка<select value={logisticsId} onChange={(event) => setLogisticsId(event.target.value)}><option value="">Без заказа</option>{logistics.filter((item) => item.obligation_id === selected.id && item.status === "DELIVERED").map((item) => <option key={item.id} value={item.id}>{shortId(item.id)} · {item.quantity}</option>)}</select></label><label className="span-two">Качество<input value={quality} onChange={(event) => setQuality(event.target.value)} required /></label><label className="span-two">Место<input value={location} onChange={(event) => setLocation(event.target.value)} required /></label><label className="file-field span-two">Акт исполнения<input type="file" onChange={(event) => setSubmitFile(event.target.files?.[0] ?? null)} required /></label><button className="primary-button" disabled={submit.isPending}><Send size={16} />Предъявить</button></form>{submit.isError ? <p className="form-error panel-error">{errorText(submit.error)}</p> : null}</section> : null}{selected && operable && principal.member_id === selected.creditor_member_id ? <section className="panel exchange-command"><div className="panel-heading"><h2>Принять исполнение</h2><CheckCheck size={18} /></div><form onSubmit={(event) => { event.preventDefault(); acceptance.mutate(); }}><label className="span-two">Предъявление<select value={acceptId} onChange={(event) => setAcceptId(event.target.value)} required><option value="">Выберите</option>{pending.map((item) => <option value={item.id} key={item.id}>{item.quantity} · {formatLocalDateTime(item.performed_at)}</option>)}</select></label><label>Принято<input value={accepted} onChange={(event) => setAccepted(event.target.value)} required /></label><label>Оценка качества<input value={qualityStatus} onChange={(event) => setQualityStatus(event.target.value)} required /></label><label className="span-two">Примечание<input value={notes} onChange={(event) => setNotes(event.target.value)} required /></label><label className="file-field span-two">Акт приёмки<input type="file" onChange={(event) => setAcceptFile(event.target.files?.[0] ?? null)} required /></label><button className="primary-button" disabled={acceptance.isPending}><CheckCheck size={16} />Зафиксировать</button></form>{acceptance.isError ? <p className="form-error panel-error">{errorText(acceptance.error)}</p> : null}</section> : null}<section className="panel"><div className="panel-heading"><h2>Предъявления</h2><span>{fulfillments.data?.length ?? 0}</span></div><div className="table-wrap"><table><thead><tr><th>Статус</th><th>Количество</th><th>Качество</th><th>Время</th></tr></thead><tbody>{(fulfillments.data ?? []).map((item) => <tr key={item.id}><td><Status value={item.status} /></td><td><strong>{item.accepted_quantity} / {item.quantity}</strong></td><td>{item.quality_claim}</td><td>{formatLocalDateTime(item.performed_at)}</td></tr>)}</tbody></table></div></section></>;
}

function LogisticsPanel({ principal, obligations, orders, members, units, canAdmin, canLogistics, onDone }: { principal: Principal; obligations: Obligation[]; orders: Awaited<ReturnType<typeof getLogisticsOrders>>; members: Array<{ member_id: string; display_name: string }>; units: Array<{ id: string; symbol: string }>; canAdmin: boolean; canLogistics: boolean; onDone: () => Promise<void> }) {
  const [obligationId, setObligationId] = useState(""); const [carrier, setCarrier] = useState(""); const [quantity, setQuantity] = useState(""); const [origin, setOrigin] = useState(""); const [destination, setDestination] = useState(""); const [pickup, setPickup] = useState(""); const [delivery, setDelivery] = useState(""); const [files, setFiles] = useState<Record<string, File | null>>({});
  const selected = obligations.find((item) => item.id === obligationId);
  const offer = useMutation({ mutationFn: () => { if (!selected) throw new Error("selection"); return createLogisticsOrder(selected, { carrier_member_id: carrier, quantity, origin_text: origin, destination_text: destination, pickup_due_at: new Date(pickup).toISOString(), delivery_due_at: new Date(delivery).toISOString() }); }, onSuccess: onDone });
  const transition = useMutation({ mutationFn: async ({ order, action }: { order: (typeof orders)[number]; action: "accept" | "pickup" | "deliver" }) => { const file = files[order.id]; const evidenceIds = action === "accept" ? [] : file ? [await uploadEvidence(order.cooperative_id, file, `LOGISTICS_${action.toUpperCase()}_ACT`)] : []; return transitionLogisticsOrder(order, action, evidenceIds); }, onSuccess: onDone });
  const nextAction = (status: string) => status === "OFFERED" ? "accept" : status === "ACCEPTED" ? "pickup" : status === "IN_TRANSIT" ? "deliver" : null;
  return <>{canAdmin ? <section className="panel exchange-command"><div className="panel-heading"><h2>Предложить перевозку</h2><Route size={18} /></div><form onSubmit={(event) => { event.preventDefault(); offer.mutate(); }}><label className="span-two">Обязательство<select value={obligationId} onChange={(event) => setObligationId(event.target.value)} required><option value="">Выберите</option>{obligations.filter((item) => !["FULFILLED", "DISPUTED", "DEFAULTED"].includes(item.status)).map((item) => <option value={item.id} key={item.id}>{shortId(item.id)} · {item.description}</option>)}</select></label><label>Перевозчик<select value={carrier} onChange={(event) => setCarrier(event.target.value)} required><option value="">Выберите</option>{members.map((item) => <option value={item.member_id} key={item.member_id}>{item.display_name}</option>)}</select></label><label>Количество<input value={quantity} onChange={(event) => setQuantity(event.target.value)} required /></label><label>Откуда<input value={origin} onChange={(event) => setOrigin(event.target.value)} required /></label><label>Куда<input value={destination} onChange={(event) => setDestination(event.target.value)} required /></label><label>Забрать до<input type="datetime-local" value={pickup} onChange={(event) => setPickup(event.target.value)} required /></label><label>Доставить до<input type="datetime-local" value={delivery} onChange={(event) => setDelivery(event.target.value)} required /></label><button className="primary-button" disabled={offer.isPending}><Send size={16} />Предложить</button></form>{offer.isError ? <p className="form-error panel-error">{errorText(offer.error)}</p> : null}</section> : null}<section className="panel"><div className="panel-heading"><h2>Заказы логистики</h2><span>{orders.length}</span></div><div className="table-wrap"><table className="logistics-table"><thead><tr><th>Статус</th><th>Маршрут</th><th>Перевозчик</th><th>Количество</th><th>Срок</th><th>Действие</th></tr></thead><tbody>{orders.map((order) => { const action = nextAction(order.status); const own = principal.member_id === order.carrier_member_id && canLogistics; return <tr key={order.id}><td><Status value={order.status} /></td><td><strong>{order.origin_text} → {order.destination_text}</strong><small>{shortId(order.id)}</small></td><td>{members.find((item) => item.member_id === order.carrier_member_id)?.display_name ?? shortId(order.carrier_member_id)}</td><td>{order.quantity} {units.find((item) => item.id === order.unit_id)?.symbol}</td><td>{formatLocalDateTime(order.delivery_due_at)}</td><td>{own && action ? <div className="logistics-action">{action !== "accept" ? <input aria-label={`Акт ${shortId(order.id)}`} type="file" onChange={(event) => setFiles((value) => ({ ...value, [order.id]: event.target.files?.[0] ?? null }))} /> : null}<button className="compact-command" disabled={transition.isPending || (action !== "accept" && !files[order.id])} onClick={() => transition.mutate({ order, action })}>{action === "accept" ? "Принять" : action === "pickup" ? "Забрать" : "Доставить"}</button></div> : "—"}</td></tr>; })}</tbody></table></div>{transition.isError ? <p className="form-error panel-error">{errorText(transition.error)}</p> : null}</section></>;
}

function DisputePanel({
  principal,
  cooperativeId,
  obligations,
  disputes,
  members,
  canScan,
  canResolve,
  onDone,
}: {
  principal: Principal;
  cooperativeId: string;
  obligations: Obligation[];
  disputes: Awaited<ReturnType<typeof getDisputes>>;
  members: Array<{ member_id: string; display_name: string }>;
  canScan: boolean;
  canResolve: boolean;
  onDone: () => Promise<void>;
}) {
  const partyObligations = obligations.filter(
    (item) =>
      principal.member_id === item.debtor_member_id ||
      principal.member_id === item.creditor_member_id,
  );
  const openCases = disputes.filter((item) => {
    const obligation = obligations.find((value) => value.id === item.obligation_id);
    return item.status === "OPEN" &&
      principal.member_id !== item.opened_by_member_id &&
      principal.member_id !== obligation?.debtor_member_id &&
      principal.member_id !== obligation?.creditor_member_id;
  });
  const [obligationId, setObligationId] = useState("");
  const [reason, setReason] = useState("QUALITY_OR_QUANTITY");
  const [statement, setStatement] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [resolutionId, setResolutionId] = useState("");
  const [resolutionAction, setResolutionAction] = useState<
    "REJECT_CLAIM" | "CONTINUE_PERFORMANCE" | "DEFAULT_OBLIGATION" | "CLOSE_OBLIGATION"
  >("CONTINUE_PERFORMANCE");
  const [resolutionNotes, setResolutionNotes] = useState("");
  const [resolutionFile, setResolutionFile] = useState<File | null>(null);
  const selected = obligations.find((item) => item.id === obligationId);
  const selectedCase = openCases.find((item) => item.id === resolutionId);
  const dispute = useMutation({
    mutationFn: async () => {
      if (!selected || !file) throw new Error("selection");
      const evidenceId = await uploadEvidence(
        selected.cooperative_id,
        file,
        "DISPUTE_STATEMENT",
      );
      return openDispute(selected, {
        fulfillment_id: null,
        reason_code: reason,
        statement,
        evidence_ids: [evidenceId],
      });
    },
    onSuccess: async () => {
      setObligationId("");
      setStatement("");
      setFile(null);
      await onDone();
    },
  });
  const resolution = useMutation({
    mutationFn: async () => {
      if (!selectedCase || !resolutionFile) throw new Error("selection");
      const obligation = obligations.find(
        (item) => item.id === selectedCase.obligation_id,
      );
      if (!obligation) throw new Error("selection");
      const evidenceId = await uploadEvidence(
        obligation.cooperative_id,
        resolutionFile,
        "DISPUTE_RESOLUTION",
      );
      return resolveDispute(selectedCase, {
        resolution_action: resolutionAction,
        resolution_notes: resolutionNotes,
        evidence_ids: [evidenceId],
      });
    },
    onSuccess: async () => {
      setResolutionId("");
      setResolutionNotes("");
      setResolutionFile(null);
      await onDone();
    },
  });
  const overdue = useMutation({
    mutationFn: () => markOverdue(cooperativeId, new Date().toISOString()),
    onSuccess: onDone,
  });

  return (
    <>
      {partyObligations.length ? (
        <section className="panel exchange-command">
          <div className="panel-heading">
            <h2>Открыть спор</h2>
            <FileWarning size={18} />
          </div>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              dispute.mutate();
            }}
          >
            <label className="span-two">
              Обязательство
              <select
                value={obligationId}
                onChange={(event) => setObligationId(event.target.value)}
                required
              >
                <option value="">Выберите</option>
                {partyObligations
                  .filter((item) => item.status !== "DISPUTED")
                  .map((item) => (
                    <option key={item.id} value={item.id}>
                      {shortId(item.id)} · {item.description}
                    </option>
                  ))}
              </select>
            </label>
            <label>
              Код причины
              <input
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                required
              />
            </label>
            <label className="span-two">
              Заявление
              <input
                value={statement}
                onChange={(event) => setStatement(event.target.value)}
                required
              />
            </label>
            <label className="file-field span-two">
              Доказательство
              <input
                type="file"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                required
              />
            </label>
            <button className="primary-button" disabled={dispute.isPending}>
              <FileWarning size={16} />
              Открыть спор
            </button>
          </form>
          {dispute.isError ? (
            <p className="form-error panel-error">{errorText(dispute.error)}</p>
          ) : null}
        </section>
      ) : null}
      {canResolve && openCases.length ? (
        <section className="panel exchange-command">
          <div className="panel-heading">
            <h2>Решение по спору</h2>
            <ClipboardCheck size={18} />
          </div>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              resolution.mutate();
            }}
          >
            <label className="span-two">
              Спор
              <select
                value={resolutionId}
                onChange={(event) => setResolutionId(event.target.value)}
                required
              >
                <option value="">Выберите</option>
                {openCases.map((item) => (
                  <option key={item.id} value={item.id}>
                    {shortId(item.id)} · {item.reason_code}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Решение
              <select
                value={resolutionAction}
                onChange={(event) =>
                  setResolutionAction(
                    event.target.value as typeof resolutionAction,
                  )
                }
              >
                <option value="CONTINUE_PERFORMANCE">Продолжить исполнение</option>
                <option value="REJECT_CLAIM">Отклонить претензию</option>
                <option value="DEFAULT_OBLIGATION">Признать дефолт</option>
                <option value="CLOSE_OBLIGATION">Закрыть обязательство</option>
              </select>
            </label>
            <label className="span-two">
              Мотивировка
              <input
                value={resolutionNotes}
                onChange={(event) => setResolutionNotes(event.target.value)}
                required
              />
            </label>
            <label className="file-field span-two">
              Документ решения
              <input
                type="file"
                onChange={(event) =>
                  setResolutionFile(event.target.files?.[0] ?? null)
                }
              />
            </label>
            <button
              className="primary-button"
              disabled={resolution.isPending || !resolutionFile}
            >
              <CheckCheck size={16} />
              Зафиксировать решение
            </button>
          </form>
          {resolution.isError ? (
            <p className="form-error panel-error">{errorText(resolution.error)}</p>
          ) : null}
        </section>
      ) : null}
      {canScan ? (
        <section className="overdue-band">
          <div>
            <strong>Проверка сроков</strong>
            <span>
              {
                obligations.filter(
                  (item) =>
                    ["ACTIVE", "PARTIALLY_FULFILLED"].includes(item.status) &&
                    new Date(item.due_at) < new Date(),
                ).length
              }{" "}
              кандидатов
            </span>
          </div>
          <button
            className="secondary-button"
            disabled={overdue.isPending}
            onClick={() => overdue.mutate()}
          >
            <TimerOff size={16} />
            Зафиксировать просрочку
          </button>
          {overdue.isError ? (
            <span className="form-error">{errorText(overdue.error)}</span>
          ) : null}
        </section>
      ) : null}
      <section className="panel">
        <div className="panel-heading">
          <h2>Реестр споров</h2>
          <span>{disputes.length}</span>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Статус</th>
                <th>Причина</th>
                <th>Заявитель</th>
                <th>Заявление</th>
                <th>Решение</th>
                <th>Время</th>
              </tr>
            </thead>
            <tbody>
              {disputes.map((item) => (
                <tr key={item.id}>
                  <td>
                    <Status value={item.status} />
                  </td>
                  <td>
                    <strong>{item.reason_code}</strong>
                    <small>{shortId(item.obligation_id)}</small>
                  </td>
                  <td>
                    {members.find(
                      (value) => value.member_id === item.opened_by_member_id,
                    )?.display_name ?? shortId(item.opened_by_member_id)}
                  </td>
                  <td>{item.statement}</td>
                  <td>
                    {item.resolution_action ? (
                      <>
                        <strong>{item.resolution_action}</strong>
                        <small>{item.resolution_notes}</small>
                      </>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td>
                    {formatLocalDateTime(item.resolved_at ?? item.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
