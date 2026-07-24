import {
  AlertTriangle,
  BadgeCheck,
  Boxes,
  Check,
  ClipboardCheck,
  FileBarChart,
  HandHeart,
  PackageCheck,
  RefreshCw,
  Scale,
  ShieldCheck,
} from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useMemo, useState } from "react";

import { AdminApiError, type Principal, type RoleCode } from "./api/admin";
import { getInventoryMembers, uploadEvidence } from "./api/inventory";
import {
  approveAllocation,
  approveFund,
  closeCampaign,
  createCampaign,
  createPledge,
  getAidApplications,
  getAllocations,
  getCampaignBalances,
  getCampaignReports,
  getCampaigns,
  getComplaints,
  getContributions,
  getDeliveries,
  getFunds,
  getPledges,
  getSolidarityControllerWorkspace,
  getSolidarityOperatorWorkspace,
  openCampaign,
  openComplaint,
  proposeAllocation,
  proposeFund,
  receiveContribution,
  recordDelivery,
  resolveComplaint,
  reviewAidApplication,
  submitAidApplication,
  verifyContribution,
  type AidApplication,
  type Allocation,
  type Campaign,
} from "./api/solidarity";
import { userErrorMessage } from "./shared/api-error";
import { formatLocalDateTime } from "./shared/date-time";
import "./solidarity.css";

type Section =
  | "campaigns"
  | "contributions"
  | "applications"
  | "allocations"
  | "complaints"
  | "reports";
type RunAction = (action: () => Promise<unknown>) => void;

const statusNames: Record<string, string> = {
  DRAFT: "Черновик",
  ACTIVE: "Активен",
  OPEN: "Открыта",
  CLOSED: "Закрыта",
  SUSPENDED: "Приостановлено",
  CANCELLED: "Отменено",
  RECEIVED: "Получен",
  VERIFIED: "Подтверждён",
  REJECTED: "Отклонён",
  FULFILLED: "Исполнено",
  SUBMITTED: "Подана",
  ELIGIBLE: "Допущена",
  ALLOCATED: "Распределено",
  PROPOSED: "Предложено",
  APPROVED: "Утверждено",
  DELIVERED: "Передано",
  RESOLVED: "Разрешена",
};

const formNames: Record<string, string> = {
  MONEY: "Деньги",
  GOODS: "Товары",
  LABOR: "Труд",
  SERVICE: "Услуга",
  LOGISTICS: "Логистика",
  INFRASTRUCTURE: "Инфраструктура",
};

function formatQuantity(value: string): string {
  const quantity = Number(value);
  return Number.isFinite(quantity)
    ? quantity.toLocaleString("ru-RU", { maximumFractionDigits: 12 })
    : value;
}

function hasRole(principal: Principal, ...roles: RoleCode[]): boolean {
  return principal.roles.some((grant) => roles.includes(grant.role));
}

function errorText(error: unknown): string {
  return userErrorMessage(error);
}

function Status({ value }: { value: string }) {
  const kind = ["ACTIVE", "OPEN", "VERIFIED", "APPROVED", "DELIVERED", "RESOLVED", "CLOSED"].includes(value)
    ? "good"
    : ["REJECTED", "CANCELLED", "SUSPENDED"].includes(value)
      ? "bad"
      : "warn";
  return <span className={`status ${kind}`}>{statusNames[value] ?? value}</span>;
}

function localInput(value: Date): string {
  const shifted = new Date(value.getTime() - value.getTimezoneOffset() * 60_000);
  return shifted.toISOString().slice(0, 16);
}

function CampaignCommands({
  principal,
  campaigns,
  funds,
  cooperativeId,
  run,
}: {
  principal: Principal;
  campaigns: Campaign[];
  funds: Awaited<ReturnType<typeof getFunds>>;
  cooperativeId: string;
  run: RunAction;
}) {
  const [fundCode, setFundCode] = useState("");
  const [fundName, setFundName] = useState("");
  const [fundPurpose, setFundPurpose] = useState("");
  const activeFunds = funds.filter((item) => item.status === "ACTIVE");
  const [fundId, setFundId] = useState("");
  const [campaignCode, setCampaignCode] = useState("");
  const [title, setTitle] = useState("");
  const [purpose, setPurpose] = useState("");
  const [form, setForm] = useState("GOODS");
  const [startsAt, setStartsAt] = useState(localInput(new Date(Date.now() - 3_600_000)));
  const [endsAt, setEndsAt] = useState(localInput(new Date(Date.now() + 30 * 86_400_000)));
  const [reconciliation, setReconciliation] = useState(
    "Все подтверждённые поступления и выдачи сверены.",
  );
  useEffect(() => {
    if (!fundId && activeFunds[0]) setFundId(activeFunds[0].id);
  }, [activeFunds, fundId]);
  if (!hasRole(principal, "SOLIDARITY_OPERATOR", "SOLIDARITY_CONTROLLER")) return null;

  return (
    <section className="solidarity-command-band">
      {hasRole(principal, "SOLIDARITY_OPERATOR") ? (
        <div className="solidarity-command-grid">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              run(() =>
                proposeFund({
                  cooperative_id: cooperativeId,
                  fund_code: fundCode,
                  name: fundName,
                  purpose: fundPurpose,
                  residue_rule: "RETAIN_IN_FUND",
                  admin_expense_limit: "0",
                  terms: {
                    no_debt: true,
                    no_reputation_benefit: true,
                    no_voting_or_priority_benefit: true,
                  },
                }),
              );
            }}
          >
            <strong>Новый фонд</strong>
            <label>Код<input value={fundCode} onChange={(event) => setFundCode(event.target.value.toUpperCase())} pattern="[A-Za-z0-9._-]+" required /></label>
            <label>Название<input value={fundName} onChange={(event) => setFundName(event.target.value)} required /></label>
            <label className="wide-field">Назначение<input value={fundPurpose} onChange={(event) => setFundPurpose(event.target.value)} required /></label>
            <button className="primary-button" type="submit"><HandHeart size={16} /><span>Предложить фонд</span></button>
          </form>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              run(() =>
                createCampaign({
                  fund_id: fundId,
                  campaign_code: campaignCode,
                  title,
                  public_purpose: purpose,
                  eligibility_policy: { manual_independent_review: true },
                  accepted_forms: [form],
                  starts_at: new Date(startsAt).toISOString(),
                  ends_at: new Date(endsAt).toISOString(),
                }),
              );
            }}
          >
            <strong>Новая кампания</strong>
            <label>Фонд<select value={fundId} onChange={(event) => setFundId(event.target.value)} required>{activeFunds.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label>
            <label>Код<input value={campaignCode} onChange={(event) => setCampaignCode(event.target.value.toUpperCase())} pattern="[A-Za-z0-9._-]+" required /></label>
            <label>Название<input value={title} onChange={(event) => setTitle(event.target.value)} required /></label>
            <label>Форма<select value={form} onChange={(event) => setForm(event.target.value)}>{Object.entries(formNames).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
            <label>Начало<input type="datetime-local" value={startsAt} onChange={(event) => setStartsAt(event.target.value)} required /></label>
            <label>Окончание<input type="datetime-local" value={endsAt} onChange={(event) => setEndsAt(event.target.value)} required /></label>
            <label className="wide-field">Публичная цель<input value={purpose} onChange={(event) => setPurpose(event.target.value)} required /></label>
            <button className="primary-button" type="submit"><ClipboardCheck size={16} /><span>Создать кампанию</span></button>
          </form>
        </div>
      ) : null}
      {hasRole(principal, "SOLIDARITY_CONTROLLER") ? (
        <div className="solidarity-review-row">
          <label>Комментарий закрытия<input value={reconciliation} onChange={(event) => setReconciliation(event.target.value)} /></label>
          <span>Черновики фонда: {funds.filter((item) => item.status === "DRAFT").length}</span>
          <span>Черновики кампаний: {campaigns.filter((item) => item.status === "DRAFT").length}</span>
        </div>
      ) : null}
      <div className="solidarity-inline-actions">
        {hasRole(principal, "SOLIDARITY_CONTROLLER")
          ? funds.filter((item) => item.status === "DRAFT").map((item) => <button className="compact-command" onClick={() => run(() => approveFund(item))} key={item.id}><Check size={14} /><span>Утвердить фонд {item.fund_code}</span></button>)
          : null}
        {hasRole(principal, "SOLIDARITY_CONTROLLER")
          ? campaigns.filter((item) => item.status === "DRAFT").map((item) => <button className="compact-command" onClick={() => run(() => openCampaign(item))} key={item.id}><Check size={14} /><span>Открыть {item.campaign_code}</span></button>)
          : null}
        {hasRole(principal, "SOLIDARITY_CONTROLLER")
          ? campaigns.filter((item) => item.status === "OPEN").map((item) => <button className="compact-command" onClick={() => run(() => closeCampaign(item, reconciliation))} key={item.id}><BadgeCheck size={14} /><span>Закрыть {item.campaign_code}</span></button>)
          : null}
      </div>
    </section>
  );
}

function ContributionCommands({
  principal,
  campaign,
  cooperativeId,
  pledges,
  run,
}: {
  principal: Principal;
  campaign: Campaign | null;
  cooperativeId: string;
  pledges: Awaited<ReturnType<typeof getPledges>>;
  run: RunAction;
}) {
  const [quantity, setQuantity] = useState("1");
  const [unit, setUnit] = useState("KG");
  const [description, setDescription] = useState("");
  const [expiry, setExpiry] = useState(localInput(new Date(Date.now() + 7 * 86_400_000)));
  const [pledgeId, setPledgeId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  if (!campaign || campaign.status !== "OPEN" || !principal.member_id) return null;
  const form = campaign.accepted_forms[0] ?? "GOODS";
  return (
    <section className="solidarity-command-band solidarity-two-forms">
      <form onSubmit={(event) => { event.preventDefault(); run(() => createPledge(campaign.id, { donor_member_id: principal.member_id!, contribution_form: form, unit_code: unit, quantity, description, expires_at: new Date(expiry).toISOString() })); }}>
        <strong>Обещание</strong>
        <label>Количество<input type="number" min="0.000000000001" step="any" value={quantity} onChange={(event) => setQuantity(event.target.value)} required /></label>
        <label>Единица<input value={unit} onChange={(event) => setUnit(event.target.value.toUpperCase())} required /></label>
        <label>Срок<input type="datetime-local" value={expiry} onChange={(event) => setExpiry(event.target.value)} required /></label>
        <label className="wide-field">Описание<input value={description} onChange={(event) => setDescription(event.target.value)} required /></label>
        <button className="secondary-button"><HandHeart size={15} /><span>Зафиксировать обещание</span></button>
      </form>
      <form onSubmit={(event) => { event.preventDefault(); if (!file) return; run(async () => { const evidenceId = await uploadEvidence(cooperativeId, file, "SOLIDARITY_AID"); await receiveContribution({ campaign_id: campaign.id, pledge_id: pledgeId || null, donor_member_id: principal.member_id!, contribution_form: form, unit_code: unit, quantity, description, evidence_ids: [evidenceId] }); setFile(null); }); }}>
        <strong>Фактический взнос</strong>
        <label>Обещание<select value={pledgeId} onChange={(event) => setPledgeId(event.target.value)}><option value="">Без обещания</option>{pledges.filter((item) => item.status === "ACTIVE" && item.donor_member_id === principal.member_id).map((item) => <option value={item.id} key={item.id}>{formatQuantity(item.quantity)} {item.unit_code}</option>)}</select></label>
        <label>Количество<input type="number" min="0.000000000001" step="any" value={quantity} onChange={(event) => setQuantity(event.target.value)} required /></label>
        <label>Подтверждение<input type="file" accept="application/pdf,image/jpeg,image/png,image/webp,text/plain" onChange={(event) => setFile(event.target.files?.[0] ?? null)} required /></label>
        <label className="wide-field">Описание<input value={description} onChange={(event) => setDescription(event.target.value)} required /></label>
        <button className="primary-button"><PackageCheck size={15} /><span>Принять взнос</span></button>
      </form>
    </section>
  );
}

function ApplicationCommands({
  principal,
  campaign,
  applications,
  run,
}: {
  principal: Principal;
  campaign: Campaign | null;
  applications: AidApplication[];
  run: RunAction;
}) {
  const [quantity, setQuantity] = useState("1");
  const [unit, setUnit] = useState("KG");
  const [need, setNeed] = useState("BASIC_FOOD");
  const [applicationId, setApplicationId] = useState("");
  const [allocationQuantity, setAllocationQuantity] = useState("1");
  const [summary, setSummary] = useState("");
  const [rationale, setRationale] = useState("");
  const eligible = applications.filter((item) => item.status === "ELIGIBLE");
  useEffect(() => {
    if (!applicationId && eligible[0]) setApplicationId(eligible[0].id);
  }, [applicationId, eligible]);
  if (!campaign || campaign.status !== "OPEN") return null;
  return (
    <section className="solidarity-command-band solidarity-two-forms">
      {principal.member_id ? (
        <form onSubmit={(event) => { event.preventDefault(); run(() => submitAidApplication(campaign.id, { recipient_member_id: principal.member_id!, need_category: need, requested_form: campaign.accepted_forms[0] ?? "GOODS", requested_unit_code: unit, requested_quantity: quantity, privacy_scope: "RESTRICTED", evidence_ids: [] })); }}>
          <strong>Заявка на помощь</strong>
          <label>Потребность<select value={need} onChange={(event) => setNeed(event.target.value)}><option value="BASIC_FOOD">Продукты</option><option value="MEDICAL">Медицина</option><option value="SHELTER">Жильё</option><option value="TRANSPORT">Транспорт</option><option value="CARE">Уход</option><option value="OTHER">Другое</option></select></label>
          <label>Количество<input type="number" min="0.000000000001" step="any" value={quantity} onChange={(event) => setQuantity(event.target.value)} required /></label>
          <label>Единица<input value={unit} onChange={(event) => setUnit(event.target.value.toUpperCase())} required /></label>
          <button className="primary-button"><ClipboardCheck size={15} /><span>Подать заявку</span></button>
        </form>
      ) : null}
      {hasRole(principal, "SOLIDARITY_OPERATOR") ? (
        <form onSubmit={(event) => { event.preventDefault(); run(() => proposeAllocation(applicationId, { quantity: allocationQuantity, public_summary: summary, rationale })); }}>
          <strong>Предложение распределения</strong>
          <label>Заявка<select value={applicationId} onChange={(event) => setApplicationId(event.target.value)} required>{eligible.map((item) => <option value={item.id} key={item.id}>{formatQuantity(item.requested_quantity)} {item.requested_unit_code} · {item.need_category}</option>)}</select></label>
          <label>Количество<input type="number" min="0.000000000001" step="any" value={allocationQuantity} onChange={(event) => setAllocationQuantity(event.target.value)} required /></label>
          <label>Публичное описание<input value={summary} onChange={(event) => setSummary(event.target.value)} required /></label>
          <label className="wide-field">Основание<input value={rationale} onChange={(event) => setRationale(event.target.value)} required /></label>
          <button className="secondary-button"><Scale size={15} /><span>Предложить</span></button>
        </form>
      ) : null}
    </section>
  );
}

function AllocationCommands({
  principal,
  cooperativeId,
  allocations,
  run,
}: {
  principal: Principal;
  cooperativeId: string;
  allocations: Allocation[];
  run: RunAction;
}) {
  const [conflict, setConflict] = useState("Конфликт интересов отсутствует.");
  const [allocationId, setAllocationId] = useState("");
  const [acknowledgement, setAcknowledgement] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const deliverable = allocations.filter((item) => item.status === "APPROVED");
  useEffect(() => {
    if (!allocationId && deliverable[0]) setAllocationId(deliverable[0].id);
  }, [allocationId, deliverable]);
  const selected = deliverable.find((item) => item.id === allocationId);
  return (
    <section className="solidarity-command-band">
      {hasRole(principal, "SOLIDARITY_CONTROLLER") ? <label className="solidarity-conflict">Декларация независимости<input value={conflict} onChange={(event) => setConflict(event.target.value)} /></label> : null}
      {principal.member_id && deliverable.length ? (
        <form className="solidarity-delivery-form" onSubmit={(event) => { event.preventDefault(); if (!selected || !file) return; run(async () => { const evidenceId = await uploadEvidence(cooperativeId, file, "SOLIDARITY_AID"); await recordDelivery(selected, { attestor_kind: selected.recipient_member_id === principal.member_id ? "RECIPIENT" : "WITNESS", acknowledgement, evidence_ids: [evidenceId] }); setFile(null); setAcknowledgement(""); }); }}>
          <label>Распределение<select value={allocationId} onChange={(event) => setAllocationId(event.target.value)}>{deliverable.map((item) => <option value={item.id} key={item.id}>{formatQuantity(item.quantity)} {item.unit_code} · {item.public_summary}</option>)}</select></label>
          <label>Подтверждение<input type="file" onChange={(event) => setFile(event.target.files?.[0] ?? null)} required /></label>
          <label>Акт передачи<input value={acknowledgement} onChange={(event) => setAcknowledgement(event.target.value)} required /></label>
          <button className="primary-button"><PackageCheck size={15} /><span>Подтвердить передачу</span></button>
        </form>
      ) : null}
    </section>
  );
}

function ComplaintCommands({
  campaign,
  allocations,
  run,
}: {
  campaign: Campaign | null;
  allocations: Allocation[];
  run: RunAction;
}) {
  const [allocationId, setAllocationId] = useState("");
  const [category, setCategory] = useState("ALLOCATION");
  const [summary, setSummary] = useState("");
  if (!campaign) return null;
  return (
    <section className="solidarity-command-band">
      <form className="solidarity-complaint-form" onSubmit={(event) => { event.preventDefault(); run(() => openComplaint(campaign.id, { allocation_id: allocationId || null, contribution_id: null, category, summary, privacy_scope: "RESTRICTED", evidence_ids: [] })); }}>
        <label>Распределение<select value={allocationId} onChange={(event) => setAllocationId(event.target.value)}><option value="">Кампания в целом</option>{allocations.map((item) => <option value={item.id} key={item.id}>{item.public_summary}</option>)}</select></label>
        <label>Категория<select value={category} onChange={(event) => setCategory(event.target.value)}><option value="ELIGIBILITY">Допуск</option><option value="ALLOCATION">Распределение</option><option value="DELIVERY">Передача</option><option value="CONTRIBUTION">Взнос</option><option value="PRIVACY">Приватность</option><option value="OTHER">Другое</option></select></label>
        <label>Суть жалобы<input value={summary} onChange={(event) => setSummary(event.target.value)} required /></label>
        <button className="secondary-button"><AlertTriangle size={15} /><span>Открыть жалобу</span></button>
      </form>
    </section>
  );
}

export default function SolidarityView({ principal }: { principal: Principal }) {
  const queryClient = useQueryClient();
  const [section, setSection] = useState<Section>("campaigns");
  const [campaignId, setCampaignId] = useState("");
  const members = useQuery({ queryKey: ["solidarity", "members"], queryFn: getInventoryMembers });
  const funds = useQuery({ queryKey: ["solidarity", "funds"], queryFn: getFunds });
  const campaigns = useQuery({ queryKey: ["solidarity", "campaigns"], queryFn: getCampaigns });
  const pledges = useQuery({ queryKey: ["solidarity", "pledges", campaignId], queryFn: () => getPledges(campaignId || undefined) });
  const contributions = useQuery({ queryKey: ["solidarity", "contributions", campaignId], queryFn: () => getContributions(campaignId || undefined) });
  const applications = useQuery({ queryKey: ["solidarity", "applications", campaignId], queryFn: () => getAidApplications(campaignId || undefined) });
  const allocations = useQuery({ queryKey: ["solidarity", "allocations", campaignId], queryFn: () => getAllocations(campaignId || undefined) });
  const deliveries = useQuery({ queryKey: ["solidarity", "deliveries", campaignId], queryFn: () => getDeliveries(campaignId || undefined) });
  const complaints = useQuery({ queryKey: ["solidarity", "complaints", campaignId], queryFn: () => getComplaints(campaignId || undefined) });
  const reports = useQuery({ queryKey: ["solidarity", "reports", campaignId], queryFn: () => getCampaignReports(campaignId || undefined) });
  const balances = useQuery({ queryKey: ["solidarity", "balances", campaignId], queryFn: () => getCampaignBalances(campaignId), enabled: Boolean(campaignId) });
  const operatorWorkspace = useQuery({ queryKey: ["solidarity", "workspace-operator"], queryFn: getSolidarityOperatorWorkspace, enabled: hasRole(principal, "SOLIDARITY_OPERATOR") });
  const controllerWorkspace = useQuery({ queryKey: ["solidarity", "workspace-controller"], queryFn: getSolidarityControllerWorkspace, enabled: hasRole(principal, "SOLIDARITY_CONTROLLER") });
  const mutation = useMutation({
    mutationFn: (action: () => Promise<unknown>) => action(),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["solidarity"] }),
  });
  const run: RunAction = (action) => mutation.mutate(action);
  useEffect(() => {
    if (!campaignId && campaigns.data?.[0]) setCampaignId(campaigns.data[0].id);
  }, [campaignId, campaigns.data]);
  const selectedCampaign = campaigns.data?.find((item) => item.id === campaignId) ?? null;
  const cooperativeId = selectedCampaign?.cooperative_id
    ?? members.data?.find((item) => item.member_id === principal.member_id)?.cooperative_id
    ?? principal.roles.find((item) => item.cooperative_id)?.cooperative_id
    ?? "";
  const memberName = (id: string) => members.data?.find((item) => item.member_id === id)?.display_name ?? id.slice(0, 8);
  const failed = [members, funds, campaigns, pledges, contributions, applications, allocations, deliveries, complaints, reports].find((query) => query.isError);
  const pending = funds.isPending || campaigns.isPending || members.isPending;
  const queue = (operatorWorkspace.data?.eligible_applications.length ?? 0)
    + (controllerWorkspace.data?.received_contributions.length ?? 0)
    + (controllerWorkspace.data?.submitted_applications.length ?? 0)
    + (controllerWorkspace.data?.proposed_allocations.length ?? 0)
    + (controllerWorkspace.data?.open_complaints.length ?? 0);
  const sections: Array<[Section, string, typeof HandHeart]> = [
    ["campaigns", "Кампании", HandHeart],
    ["contributions", "Взносы", Boxes],
    ["applications", "Заявки", ClipboardCheck],
    ["allocations", "Распределения", Scale],
    ["complaints", "Жалобы", AlertTriangle],
    ["reports", "Отчёты", FileBarChart],
  ];
  const campaignApplications = applications.data ?? [];
  const campaignAllocations = allocations.data ?? [];

  if (pending) return <div className="view-stack"><div className="state"><RefreshCw className="spin" size={24} />Загрузка фонда</div></div>;
  if (failed) return <div className="view-stack"><div className="state error" role="alert"><AlertTriangle size={22} />{errorText(failed.error)}</div></div>;

  return (
    <div className="view-stack solidarity-view">
      <header className="view-header">
        <div><span className="eyebrow">ДОБРОВОЛЬНАЯ ПОМОЩЬ</span><h1>Солидарный фонд</h1><p>{selectedCampaign ? `${selectedCampaign.campaign_code} · ${selectedCampaign.title}` : "Кампаний нет"}</p></div>
        <div className="section-tabs">{sections.map(([key, label, Icon]) => <button type="button" className={section === key ? "active" : ""} onClick={() => setSection(key)} key={key}><Icon size={15} /><span>{label}</span></button>)}</div>
      </header>
      <section className="solidarity-scope-strip">
        <label>Кампания<select value={campaignId} onChange={(event) => setCampaignId(event.target.value)}><option value="">Все кампании</option>{campaigns.data?.map((item) => <option value={item.id} key={item.id}>{item.campaign_code} · {item.title}</option>)}</select></label>
        <div><span>Статус</span>{selectedCampaign ? <Status value={selectedCampaign.status} /> : <strong>Все</strong>}</div>
        <div><span>Доступно</span><strong>{balances.data?.reduce((sum, item) => sum + Number(item.available), 0).toLocaleString("ru-RU") ?? "—"}</strong></div>
      </section>
      <section className="metric-grid solidarity-metrics" aria-label="Сводка">
        <article className="metric"><HandHeart size={18} /><span>Кампании</span><strong>{campaigns.data?.length ?? 0}</strong></article>
        <article className="metric"><PackageCheck size={18} /><span>Подтверждённые взносы</span><strong>{contributions.data?.filter((item) => item.status === "VERIFIED").length ?? 0}</strong></article>
        <article className="metric"><ClipboardCheck size={18} /><span>Заявки</span><strong>{campaignApplications.length}</strong></article>
        <article className="metric"><Scale size={18} /><span>Выдано</span><strong>{campaignAllocations.filter((item) => item.status === "DELIVERED").length}</strong></article>
        <article className="metric"><ShieldCheck size={18} /><span>Моя очередь</span><strong>{queue}</strong></article>
      </section>
      {mutation.isError ? <p className="solidarity-error" role="alert">{errorText(mutation.error)}</p> : null}

      {section === "campaigns" ? <>
        <CampaignCommands principal={principal} campaigns={campaigns.data ?? []} funds={funds.data ?? []} cooperativeId={cooperativeId} run={run} />
        <section className="panel"><div className="panel-heading"><h2>Кампании</h2><span>{campaigns.data?.length ?? 0}</span></div><div className="table-wrap"><table className="solidarity-table"><thead><tr><th>Кампания</th><th>Период</th><th>Формы</th><th>Правило остатка</th><th>Статус</th></tr></thead><tbody>{campaigns.data?.map((item) => <tr key={item.id}><td><strong>{item.campaign_code} · {item.title}</strong><small>{item.public_purpose}</small></td><td>{formatLocalDateTime(item.starts_at)}<small>{formatLocalDateTime(item.ends_at)}</small></td><td>{item.accepted_forms.map((value) => formNames[value] ?? value).join(", ")}</td><td>{item.residue_rule}</td><td><Status value={item.status} /></td></tr>)}</tbody></table></div></section>
        <section className="panel"><div className="panel-heading"><h2>Проверенный баланс</h2><span>{balances.data?.length ?? 0}</span></div><div className="table-wrap"><table><thead><tr><th>Форма</th><th>Единица</th><th>Подтверждено</th><th>Зарезервировано / выдано</th><th>Доступно</th></tr></thead><tbody>{balances.data?.map((item) => <tr key={`${item.contribution_form}:${item.unit_code}`}><td>{formNames[item.contribution_form] ?? item.contribution_form}</td><td>{item.unit_code}</td><td>{formatQuantity(item.verified)}</td><td>{formatQuantity(item.reserved_or_delivered)}</td><td><strong>{formatQuantity(item.available)}</strong></td></tr>)}</tbody></table></div></section>
      </> : null}

      {section === "contributions" ? <>
        <ContributionCommands principal={principal} campaign={selectedCampaign} cooperativeId={cooperativeId} pledges={pledges.data ?? []} run={run} />
        <section className="panel"><div className="panel-heading"><h2>Взносы</h2><span>{contributions.data?.length ?? 0}</span></div><div className="table-wrap"><table className="solidarity-table"><thead><tr><th>Участник</th><th>Факт</th><th>Описание</th><th>Получен</th><th>Статус</th><th></th></tr></thead><tbody>{contributions.data?.map((item) => <tr key={item.id}><td>{memberName(item.donor_member_id)}</td><td><strong>{formatQuantity(item.quantity)} {item.unit_code}</strong><small>{formNames[item.contribution_form] ?? item.contribution_form}</small></td><td>{item.description}</td><td>{formatLocalDateTime(item.received_at)}</td><td><Status value={item.status} /></td><td>{item.status === "RECEIVED" && hasRole(principal, "SOLIDARITY_CONTROLLER") ? <span className="table-actions"><button title="Подтвердить" onClick={() => run(() => verifyContribution(item, true, "Факт, количество и документ проверены."))}><Check size={15} /></button><button title="Отклонить" onClick={() => run(() => verifyContribution(item, false, "Факт не прошёл независимую проверку."))}><AlertTriangle size={15} /></button></span> : null}</td></tr>)}</tbody></table></div></section>
        <section className="panel"><div className="panel-heading"><h2>Обещания, не включённые в баланс</h2><span>{pledges.data?.length ?? 0}</span></div><div className="rows">{pledges.data?.map((item) => <div className="data-row" key={item.id}><strong>{formatQuantity(item.quantity)} {item.unit_code} · {memberName(item.donor_member_id)}</strong><span>{item.description}</span><Status value={item.status} /></div>)}</div></section>
      </> : null}

      {section === "applications" ? <>
        <ApplicationCommands principal={principal} campaign={selectedCampaign} applications={campaignApplications} run={run} />
        <section className="panel"><div className="panel-heading"><h2>Заявки</h2><span>{campaignApplications.length}</span></div><div className="table-wrap"><table className="solidarity-table"><thead><tr><th>Получатель</th><th>Потребность</th><th>Запрос</th><th>Приватность</th><th>Статус</th><th></th></tr></thead><tbody>{campaignApplications.map((item) => <tr key={item.id}><td>{memberName(item.recipient_member_id)}</td><td>{item.need_category}</td><td><strong>{formatQuantity(item.requested_quantity)} {item.requested_unit_code}</strong><small>{formNames[item.requested_form] ?? item.requested_form}</small></td><td>{item.privacy_scope === "RESTRICTED" ? "Ограничено" : "Участник и сотрудники"}</td><td><Status value={item.status} /></td><td>{item.status === "SUBMITTED" && hasRole(principal, "SOLIDARITY_CONTROLLER") ? <span className="table-actions"><button title="Допустить" onClick={() => run(() => reviewAidApplication(item, true, "Критерии кампании подтверждены."))}><Check size={15} /></button><button title="Отклонить" onClick={() => run(() => reviewAidApplication(item, false, "Критерии кампании не подтверждены."))}><AlertTriangle size={15} /></button></span> : null}</td></tr>)}</tbody></table></div></section>
      </> : null}

      {section === "allocations" ? <>
        <AllocationCommands principal={principal} cooperativeId={cooperativeId} allocations={campaignAllocations} run={run} />
        <section className="panel"><div className="panel-heading"><h2>Распределения</h2><span>{campaignAllocations.length}</span></div><div className="table-wrap"><table className="solidarity-table"><thead><tr><th>Получатель</th><th>Количество</th><th>Публичное описание</th><th>Хеш условий</th><th>Статус</th><th></th></tr></thead><tbody>{campaignAllocations.map((item) => <tr key={item.id}><td>{memberName(item.recipient_member_id)}</td><td><strong>{formatQuantity(item.quantity)} {item.unit_code}</strong></td><td>{item.public_summary}<small>{item.rationale}</small></td><td><code>{item.allocation_hash.slice(0, 20)}…</code></td><td><Status value={item.status} /></td><td>{item.status === "PROPOSED" && hasRole(principal, "SOLIDARITY_CONTROLLER") ? <span className="table-actions"><button title="Утвердить" onClick={() => run(() => approveAllocation(item, true, "Конфликт интересов отсутствует."))}><Check size={15} /></button><button title="Отклонить" onClick={() => run(() => approveAllocation(item, false, "Распределение требует пересмотра."))}><AlertTriangle size={15} /></button></span> : null}</td></tr>)}</tbody></table></div></section>
        <section className="panel"><div className="panel-heading"><h2>Передачи</h2><span>{deliveries.data?.length ?? 0}</span></div><div className="rows">{deliveries.data?.map((item) => <div className="data-row" key={item.id}><strong>{memberName(item.recipient_member_id)}</strong><span>{item.acknowledgement}</span><time>{formatLocalDateTime(item.delivered_at)}</time></div>)}</div></section>
      </> : null}

      {section === "complaints" ? <>
        <ComplaintCommands campaign={selectedCampaign} allocations={campaignAllocations} run={run} />
        <section className="panel"><div className="panel-heading"><h2>Жалобы</h2><span>{complaints.data?.length ?? 0}</span></div><div className="table-wrap"><table className="solidarity-table"><thead><tr><th>Заявитель</th><th>Категория</th><th>Содержание</th><th>Открыта</th><th>Статус</th><th></th></tr></thead><tbody>{complaints.data?.map((item) => <tr key={item.id}><td>{memberName(item.complainant_member_id)}</td><td>{item.category}</td><td>{item.summary}<small>{item.resolution_note}</small></td><td>{formatLocalDateTime(item.opened_at)}</td><td><Status value={item.status} /></td><td>{item.status === "OPEN" && (hasRole(principal, "SOLIDARITY_CONTROLLER") || hasRole(principal, "AUDITOR")) ? <span className="table-actions"><button title="Восстановить распределение" onClick={() => run(() => resolveComplaint(item, { accepted: true, resolution_action: item.allocation_id ? "RESTORE_ALLOCATION" : "NOTE_ONLY", resolution_note: "Жалоба проверена независимым ответственным лицом." }))}><Check size={15} /></button><button title="Отклонить жалобу" onClick={() => run(() => resolveComplaint(item, { accepted: false, resolution_action: "NOTE_ONLY", resolution_note: "Основания жалобы не подтверждены." }))}><AlertTriangle size={15} /></button></span> : null}</td></tr>)}</tbody></table></div></section>
      </> : null}

      {section === "reports" ? <section className="panel"><div className="panel-heading"><h2>Агрегированные отчёты</h2><span>{reports.data?.length ?? 0}</span></div><div className="solidarity-report-list">{reports.data?.map((report) => <article key={report.id}><header><div><strong>{campaigns.data?.find((item) => item.id === report.campaign_id)?.title ?? report.campaign_id}</strong><span>{formatLocalDateTime(report.generated_at)}</span></div><code>{report.report_hash}</code></header><dl><dt>Взносы</dt><dd>{report.contribution_count}</dd><dt>Распределения</dt><dd>{report.allocation_count}</dd><dt>Передачи</dt><dd>{report.delivery_count}</dd><dt>Жалобы</dt><dd>{report.complaint_count}</dd></dl><div className="table-wrap"><table><thead><tr><th>Форма</th><th>Единица</th><th>Подтверждено</th><th>Выдано</th><th>Остаток</th></tr></thead><tbody>{report.bucket_totals.map((item) => <tr key={`${item.contribution_form}:${item.unit_code}`}><td>{formNames[item.contribution_form] ?? item.contribution_form}</td><td>{item.unit_code}</td><td>{formatQuantity(item.verified)}</td><td>{formatQuantity(item.delivered)}</td><td>{formatQuantity(item.residue)}</td></tr>)}</tbody></table></div></article>)}</div></section> : null}
    </div>
  );
}
