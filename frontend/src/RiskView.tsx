import {
  AlertTriangle,
  BadgeCheck,
  Banknote,
  Check,
  FileCheck2,
  GitBranch,
  Landmark,
  Link2,
  LockKeyhole,
  Plus,
  RefreshCw,
  Scale,
  ShieldAlert,
  Unlock,
  UserCheck,
  Users,
  WalletCards,
  X,
} from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useMemo, useState } from "react";

import {
  AdminApiError,
  getCooperatives,
  type Principal,
  type RoleCode,
} from "./api/admin";
import {
  getInventoryMembers,
  uploadEvidence,
  type InventoryMember,
} from "./api/inventory";
import {
  acceptExposure,
  addShareContribution,
  approveRiskPolicy,
  assessLiabilityCase,
  decideRelatedLink,
  getExposureCommitments,
  getLiabilityCases,
  getRelatedLinks,
  getRiskPolicies,
  getShareAccounts,
  getShareContributions,
  openLiabilityCase,
  openShareAccount,
  previewExposure,
  proposeExposure,
  proposeRelatedLink,
  proposeRiskPolicy,
  releaseExposure,
  type CommitmentType,
  type ExposureCommitment,
  type ExposurePreview,
  type FaultClass,
  type LiabilityCase,
  type RelatedLink,
  type RiskPolicy,
  type ShareAccount,
  type ShareContour,
} from "./api/risk";
import { userErrorMessage } from "./shared/api-error";
import { formatLocalDateTime } from "./shared/date-time";
import "./risk.css";

type Section = "overview" | "policies" | "accounts" | "commitments" | "related" | "liability";

const evidenceAccept = "application/pdf,image/jpeg,image/png,image/webp,text/plain";

const statusNames: Record<string, string> = {
  PROPOSED: "Предложено",
  ACTIVE: "Активно",
  SUPERSEDED: "Заменено",
  REJECTED: "Отклонено",
  RELEASED: "Освобождено",
  OPEN: "Открыто",
  ASSESSED: "Оценено",
  NOT_EXECUTED: "Не исполнено",
};

const contourNames: Record<string, string> = {
  PRIMARY: "Основной пай",
  GUARANTEE: "Гарантийный контур",
  ROLE: "Ролевая ответственность",
  SOLIDARITY: "Солидарный контур",
};

const commitmentNames: Record<string, string> = {
  DIRECT_OBLIGATION: "Прямое обязательство",
  GUARANTEE: "Поручительство",
  CREDIT_LIMIT: "Кредитный лимит",
  ROLE_BOND: "Ответственность роли",
};

function hasRole(principal: Principal, ...roles: RoleCode[]): boolean {
  return principal.roles.some((item) => roles.includes(item.role));
}

function errorText(error: unknown): string {
  return userErrorMessage(error);
}

function memberName(members: InventoryMember[], id: string | null): string {
  if (!id) return "—";
  return members.find((item) => item.member_id === id)?.display_name ?? id.slice(0, 8);
}

function exact(value: string | number): string {
  const parsed = Number(value);
  return Number.isFinite(parsed)
    ? new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 12 }).format(parsed)
    : String(value);
}

function localDateInput(daysFromNow: number): string {
  const value = new Date(Date.now() + daysFromNow * 86_400_000);
  const shifted = new Date(value.getTime() - value.getTimezoneOffset() * 60_000);
  return shifted.toISOString().slice(0, 16);
}

function Status({ value }: { value: string }) {
  const kind = ["ACTIVE", "ASSESSED"].includes(value)
    ? "good"
    : ["REJECTED", "SUPERSEDED"].includes(value)
      ? "bad"
      : "warn";
  return <span className={`status ${kind}`}>{statusNames[value] ?? value}</span>;
}

function ErrorLine({ error }: { error: unknown }) {
  return <p className="form-error risk-error" role="alert">{errorText(error)}</p>;
}

function EvidenceInput({
  file,
  onChange,
  label = "Основание",
}: {
  file: File | null;
  onChange: (file: File | null) => void;
  label?: string;
}) {
  return (
    <label className="file-field">
      {label}
      <input
        type="file"
        accept={evidenceAccept}
        onChange={(event) => onChange(event.target.files?.[0] ?? null)}
        required
      />
      {file ? <small>{file.name}</small> : null}
    </label>
  );
}

export default function RiskView({ principal }: { principal: Principal }) {
  const queryClient = useQueryClient();
  const [section, setSection] = useState<Section>("overview");
  const policies = useQuery({ queryKey: ["risk", "policies"], queryFn: getRiskPolicies });
  const accounts = useQuery({ queryKey: ["risk", "accounts"], queryFn: getShareAccounts });
  const commitments = useQuery({
    queryKey: ["risk", "commitments"],
    queryFn: getExposureCommitments,
  });
  const related = useQuery({ queryKey: ["risk", "related"], queryFn: getRelatedLinks });
  const liabilities = useQuery({
    queryKey: ["risk", "liabilities"],
    queryFn: getLiabilityCases,
  });
  const members = useQuery({ queryKey: ["inventory-members"], queryFn: getInventoryMembers });
  const cooperatives = useQuery({ queryKey: ["cooperatives"], queryFn: getCooperatives });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["risk"] });

  const queries = [policies, accounts, commitments, related, liabilities, members, cooperatives];
  if (queries.some((query) => query.isPending)) {
    return <div className="view-stack"><div className="state"><RefreshCw className="spin" size={24} />Загрузка риска</div></div>;
  }
  const failed = queries.find((query) => query.isError);
  if (failed) {
    return <div className="view-stack"><div className="state error"><AlertTriangle size={23} />{errorText(failed.error)}</div></div>;
  }

  const policyData = policies.data ?? [];
  const accountData = accounts.data ?? [];
  const commitmentData = commitments.data ?? [];
  const relatedData = related.data ?? [];
  const liabilityData = liabilities.data ?? [];
  const memberData = members.data ?? [];
  const cooperativeId = cooperatives.data?.[0]?.id ?? "";
  const activeReserved = commitmentData
    .filter((item) => item.status === "ACTIVE")
    .reduce((sum, item) => sum + Number(item.amount_reserved), 0);
  const availableShares = accountData.reduce(
    (sum, item) => sum + Number(item.balance) - Number(item.protected_amount)
      - Number(item.executed_not_settled)
      - commitmentData
        .filter((commitment) => commitment.account_id === item.id && commitment.status === "ACTIVE")
        .reduce((reserved, commitment) => reserved + Number(commitment.amount_reserved), 0),
    0,
  );
  const sections: Array<[Section, string, typeof Scale]> = [
    ["overview", "Сводка", Scale],
    ["policies", "Политики", FileCheck2],
    ["accounts", "Паи", WalletCards],
    ["commitments", "Риски", LockKeyhole],
    ["related", "Связи", GitBranch],
    ["liability", "Ответственность", ShieldAlert],
  ];

  return (
    <div className="view-stack risk-view">
      <header className="view-header">
        <div>
          <span className="eyebrow">ОГРАНИЧЕННАЯ ОТВЕТСТВЕННОСТЬ</span>
          <h1>Риск и паи</h1>
          <p>Персональное принятие риска, лимиты связанных лиц и доказуемая оценка ущерба</p>
        </div>
        <div className="section-tabs">
          {sections.map(([key, label, Icon]) => (
            <button
              type="button"
              className={section === key ? "active" : ""}
              onClick={() => setSection(key)}
              key={key}
            >
              <Icon size={15} /><span>{label}</span>
            </button>
          ))}
        </div>
      </header>

      <section className="metric-grid risk-metrics" aria-label="Сводка риска">
        <article className="metric"><WalletCards size={19} /><span>Счетов</span><strong>{accountData.length}</strong></article>
        <article className="metric"><Unlock size={19} /><span>Доступно</span><strong>{exact(availableShares)}</strong></article>
        <article className="metric"><LockKeyhole size={19} /><span>Зарезервировано</span><strong>{exact(activeReserved)}</strong></article>
        <article className="metric"><UserCheck size={19} /><span>Ожидают принятия</span><strong>{commitmentData.filter((item) => item.status === "PROPOSED").length}</strong></article>
        <article className="metric"><GitBranch size={19} /><span>Связанные лица</span><strong>{relatedData.filter((item) => item.status === "ACTIVE").length}</strong></article>
        <article className="metric"><ShieldAlert size={19} /><span>Открытые случаи</span><strong>{liabilityData.filter((item) => item.status === "OPEN").length}</strong></article>
      </section>

      {section === "overview" ? (
        <OverviewPanel
          accounts={accountData}
          commitments={commitmentData}
          liabilities={liabilityData}
          members={memberData}
        />
      ) : null}
      {section === "policies" ? (
        <PolicyPanel
          principal={principal}
          cooperativeId={cooperativeId}
          policies={policyData}
          members={memberData}
          onDone={refresh}
        />
      ) : null}
      {section === "accounts" ? (
        <AccountPanel
          principal={principal}
          policies={policyData}
          accounts={accountData}
          members={memberData}
          onDone={refresh}
        />
      ) : null}
      {section === "commitments" ? (
        <CommitmentPanel
          principal={principal}
          policies={policyData}
          accounts={accountData}
          commitments={commitmentData}
          members={memberData}
          onDone={refresh}
        />
      ) : null}
      {section === "related" ? (
        <RelatedPanel
          principal={principal}
          cooperativeId={cooperativeId}
          links={relatedData}
          members={memberData}
          onDone={refresh}
        />
      ) : null}
      {section === "liability" ? (
        <LiabilityPanel
          principal={principal}
          commitments={commitmentData}
          cases={liabilityData}
          members={memberData}
          onDone={refresh}
        />
      ) : null}
    </div>
  );
}

function OverviewPanel({
  accounts,
  commitments,
  liabilities,
  members,
}: {
  accounts: ShareAccount[];
  commitments: ExposureCommitment[];
  liabilities: LiabilityCase[];
  members: InventoryMember[];
}) {
  return (
    <>
      <section className="panel">
        <div className="panel-heading"><h2>Пай и доступная ответственность</h2><span>{accounts.length}</span></div>
        <div className="table-wrap">
          <table className="risk-table">
            <thead><tr><th>Участник</th><th>Контур</th><th>Баланс</th><th>Защищено</th><th>Активный резерв</th><th>Доступно</th><th>Статус</th></tr></thead>
            <tbody>{accounts.map((account) => {
              const reserved = commitments
                .filter((item) => item.account_id === account.id && item.status === "ACTIVE")
                .reduce((sum, item) => sum + Number(item.amount_reserved), 0);
              const available = Number(account.balance) - Number(account.protected_amount)
                - Number(account.executed_not_settled) - reserved;
              return <tr key={account.id}><td><strong>{memberName(members, account.member_id)}</strong><small>{account.denomination} · v{account.version}</small></td><td>{contourNames[account.contour] ?? account.contour}</td><td>{exact(account.balance)}</td><td>{exact(account.protected_amount)}</td><td>{exact(reserved)}</td><td><strong>{exact(available)}</strong></td><td><Status value={account.status} /></td></tr>;
            })}</tbody>
          </table>
        </div>
      </section>
      <section className="panel">
        <div className="panel-heading"><h2>Последние оценки ответственности</h2><span>{liabilities.length}</span></div>
        <div className="table-wrap">
          <table><thead><tr><th>Инцидент</th><th>Ответственный</th><th>Заявлено</th><th>Оценено</th><th>Исполнение</th><th>Статус</th></tr></thead>
            <tbody>{liabilities.length ? liabilities.slice(0, 10).map((item) => <tr key={item.id}><td><strong>{item.incident_reference}</strong><small>{formatLocalDateTime(item.created_at)}</small></td><td>{memberName(members, item.responsible_member_id)}</td><td>{exact(item.affected_amount)}</td><td>{item.assessed_loss === null ? "—" : exact(item.assessed_loss)}</td><td><Status value="NOT_EXECUTED" /></td><td><Status value={item.status} /></td></tr>) : <tr><td colSpan={6} className="empty-cell">Случаев нет</td></tr>}</tbody>
          </table>
        </div>
      </section>
    </>
  );
}

function PolicyPanel({
  principal,
  cooperativeId,
  policies,
  members,
  onDone,
}: {
  principal: Principal;
  cooperativeId: string;
  policies: RiskPolicy[];
  members: InventoryMember[];
  onDone: () => Promise<unknown>;
}) {
  const canPropose = hasRole(principal, "COOPERATIVE_ADMIN");
  const canApprove = hasRole(principal, "RISK_ADMIN", "AUDITOR");
  const [denomination, setDenomination] = useState("SHARE");
  const [memberLimit, setMemberLimit] = useState("1000");
  const [relatedLimit, setRelatedLimit] = useState("2000");
  const [depth, setDepth] = useState("3");
  const [protectedRule, setProtectedRule] = useState("Защищенная часть пая не участвует в покрытии.");
  const [relatedRule, setRelatedRule] = useState("Связанные участники используют общий лимит.");
  const [reference, setReference] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [approvalFiles, setApprovalFiles] = useState<Record<string, File | null>>({});
  const propose = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error("EVIDENCE_REQUIRED");
      const evidenceId = await uploadEvidence(cooperativeId, file, "RISK_POLICY_PROPOSAL");
      return proposeRiskPolicy({
        cooperative_id: cooperativeId,
        denomination,
        max_member_exposure: memberLimit,
        max_related_exposure: relatedLimit,
        max_guarantee_chain_depth: Number(depth),
        protected_amount_rule: protectedRule,
        related_party_rule: relatedRule,
        approval_reference: reference,
        evidence_ids: [evidenceId],
      });
    },
    onSuccess: async () => {
      setReference("");
      setFile(null);
      await onDone();
    },
  });
  const approve = useMutation({
    mutationFn: async (policy: RiskPolicy) => {
      const evidence = approvalFiles[policy.id];
      if (!evidence) throw new Error("EVIDENCE_REQUIRED");
      const evidenceId = await uploadEvidence(policy.cooperative_id, evidence, "RISK_POLICY_APPROVAL");
      return approveRiskPolicy(policy, [evidenceId]);
    },
    onSuccess: async (_data, policy) => {
      setApprovalFiles((current) => ({ ...current, [policy.id]: null }));
      await onDone();
    },
  });
  return (
    <>
      {canPropose ? <section className="panel risk-command">
        <div className="panel-heading"><h2>Новая политика лимитов</h2><span>двойной контроль</span></div>
        <form className="risk-form policy-form" onSubmit={(event) => { event.preventDefault(); propose.mutate(); }}>
          <label>Единица пая<input value={denomination} pattern="[A-Za-z0-9._-]+" onChange={(event) => setDenomination(event.target.value)} required /></label>
          <label>Лимит участника<input inputMode="decimal" value={memberLimit} onChange={(event) => setMemberLimit(event.target.value)} required /></label>
          <label>Лимит группы<input inputMode="decimal" value={relatedLimit} onChange={(event) => setRelatedLimit(event.target.value)} required /></label>
          <label>Глубина поручительств<input type="number" min={1} max={20} value={depth} onChange={(event) => setDepth(event.target.value)} required /></label>
          <label className="span-two">Защищенная часть<input value={protectedRule} onChange={(event) => setProtectedRule(event.target.value)} required /></label>
          <label className="span-two">Связанные лица<input value={relatedRule} onChange={(event) => setRelatedRule(event.target.value)} required /></label>
          <label>Решение органа<input value={reference} onChange={(event) => setReference(event.target.value)} required /></label>
          <EvidenceInput file={file} onChange={setFile} />
          <button className="primary-button" disabled={propose.isPending}><Plus size={16} />Предложить</button>
        </form>
        {propose.isError ? <ErrorLine error={propose.error} /> : null}
      </section> : null}
      <section className="panel">
        <div className="panel-heading"><h2>Политики</h2><span>{policies.length}</span></div>
        <div className="table-wrap">
          <table className="risk-table">
            <thead><tr><th>Версия</th><th>Лимит участника</th><th>Лимит группы</th><th>Цепочка</th><th>Инициатор</th><th>Статус</th><th>Решение</th></tr></thead>
            <tbody>{policies.map((policy) => {
              const independent = principal.member_id !== policy.proposed_by_member_id;
              return <tr key={policy.id}><td><strong>{policy.denomination} · v{policy.policy_version}</strong><small>{policy.terms_hash.slice(0, 22)}</small></td><td>{exact(policy.max_member_exposure)}</td><td>{exact(policy.max_related_exposure)}</td><td>{policy.max_guarantee_chain_depth}</td><td>{memberName(members, policy.proposed_by_member_id)}</td><td><Status value={policy.status} /></td><td>{canApprove && policy.status === "PROPOSED" && independent ? <div className="inline-decision"><input aria-label="Основание утверждения" type="file" accept={evidenceAccept} onChange={(event) => setApprovalFiles((current) => ({ ...current, [policy.id]: event.target.files?.[0] ?? null }))} /><button className="compact-command" disabled={!approvalFiles[policy.id] || approve.isPending} onClick={() => approve.mutate(policy)}><Check size={14} />Утвердить</button></div> : <span className="muted-value">{policy.approved_at ? formatLocalDateTime(policy.approved_at) : "—"}</span>}</td></tr>;
            })}</tbody>
          </table>
        </div>
        {approve.isError ? <ErrorLine error={approve.error} /> : null}
      </section>
    </>
  );
}

function AccountPanel({
  principal,
  policies,
  accounts,
  members,
  onDone,
}: {
  principal: Principal;
  policies: RiskPolicy[];
  accounts: ShareAccount[];
  members: InventoryMember[];
  onDone: () => Promise<unknown>;
}) {
  const canOperate = hasRole(principal, "COOPERATIVE_ADMIN", "RISK_ADMIN");
  const activePolicies = policies.filter((item) => item.status === "ACTIVE");
  const [policyId, setPolicyId] = useState(activePolicies[0]?.id ?? "");
  const [memberId, setMemberId] = useState("");
  const [contour, setContour] = useState<ShareContour>("GUARANTEE");
  const [balance, setBalance] = useState("");
  const [protectedAmount, setProtectedAmount] = useState("0");
  const [reference, setReference] = useState("");
  const [openFile, setOpenFile] = useState<File | null>(null);
  const [selectedId, setSelectedId] = useState(accounts[0]?.id ?? "");
  const [contribution, setContribution] = useState("");
  const [contributionRef, setContributionRef] = useState("");
  const [contributionFile, setContributionFile] = useState<File | null>(null);
  const selected = accounts.find((item) => item.id === selectedId);
  const contributions = useQuery({
    queryKey: ["risk", "contributions", selectedId],
    queryFn: () => getShareContributions(selectedId),
    enabled: Boolean(selectedId),
  });
  const open = useMutation({
    mutationFn: async () => {
      const policy = activePolicies.find((item) => item.id === policyId);
      if (!openFile || !policy) throw new Error("EVIDENCE_REQUIRED");
      const evidenceId = await uploadEvidence(policy.cooperative_id, openFile, "RISK_SHARE_ACCOUNT_OPEN");
      return openShareAccount({
        policy_id: policy.id,
        member_id: memberId,
        contour,
        opening_balance: balance,
        protected_amount: protectedAmount,
        source_reference: reference,
        evidence_ids: [evidenceId],
      });
    },
    onSuccess: async () => {
      setBalance("");
      setReference("");
      setOpenFile(null);
      await onDone();
    },
  });
  const add = useMutation({
    mutationFn: async () => {
      if (!selected || !contributionFile) throw new Error("EVIDENCE_REQUIRED");
      const evidenceId = await uploadEvidence(selected.cooperative_id, contributionFile, "RISK_SHARE_CONTRIBUTION");
      return addShareContribution(selected, {
        amount: contribution,
        source_reference: contributionRef,
        evidence_ids: [evidenceId],
      });
    },
    onSuccess: async () => {
      setContribution("");
      setContributionRef("");
      setContributionFile(null);
      await onDone();
    },
  });
  return (
    <>
      {canOperate ? <section className="panel risk-command">
        <div className="panel-heading"><h2>Открыть счёт пая</h2><span>по активной политике</span></div>
        <form className="risk-form" onSubmit={(event) => { event.preventDefault(); open.mutate(); }}>
          <label>Политика<select value={policyId} onChange={(event) => setPolicyId(event.target.value)} required><option value="">Выберите</option>{activePolicies.map((item) => <option key={item.id} value={item.id}>{item.denomination} · v{item.policy_version}</option>)}</select></label>
          <label>Участник<select value={memberId} onChange={(event) => setMemberId(event.target.value)} required><option value="">Выберите</option>{members.map((item) => <option key={item.member_id} value={item.member_id}>{item.display_name}</option>)}</select></label>
          <label>Контур<select value={contour} onChange={(event) => setContour(event.target.value as ShareContour)}><option value="GUARANTEE">Гарантийный</option><option value="ROLE">Ролевой</option><option value="PRIMARY">Основной</option><option value="SOLIDARITY">Солидарный</option></select></label>
          <label>Начальный баланс<input inputMode="decimal" value={balance} onChange={(event) => setBalance(event.target.value)} required /></label>
          <label>Защищено<input inputMode="decimal" value={protectedAmount} onChange={(event) => setProtectedAmount(event.target.value)} required /></label>
          <label>Источник<input value={reference} onChange={(event) => setReference(event.target.value)} required /></label>
          <EvidenceInput file={openFile} onChange={setOpenFile} />
          <button className="primary-button" disabled={open.isPending}><Plus size={16} />Открыть</button>
        </form>
        {open.isError ? <ErrorLine error={open.error} /> : null}
      </section> : null}
      <section className="panel">
        <div className="panel-heading"><h2>Счета паёв</h2><span>{accounts.length}</span></div>
        <div className="table-wrap"><table className="risk-table"><thead><tr><th>Участник</th><th>Контур</th><th>Единица</th><th>Баланс</th><th>Защищено</th><th>Не урегулировано</th><th>Статус</th></tr></thead><tbody>{accounts.map((account) => <tr className={selectedId === account.id ? "selected-row" : ""} key={account.id} onClick={() => setSelectedId(account.id)}><td><strong>{memberName(members, account.member_id)}</strong><small>{account.id}</small></td><td>{contourNames[account.contour] ?? account.contour}</td><td>{account.denomination}</td><td>{exact(account.balance)}</td><td>{exact(account.protected_amount)}</td><td>{exact(account.executed_not_settled)}</td><td><Status value={account.status} /></td></tr>)}</tbody></table></div>
      </section>
      {selected ? <section className="panel">
        <div className="panel-heading"><h2>Взносы · {memberName(members, selected.member_id)}</h2><span>{contributions.data?.length ?? 0}</span></div>
        {canOperate ? <form className="risk-inline-form" onSubmit={(event) => { event.preventDefault(); add.mutate(); }}><label>Сумма<input inputMode="decimal" value={contribution} onChange={(event) => setContribution(event.target.value)} required /></label><label>Источник<input value={contributionRef} onChange={(event) => setContributionRef(event.target.value)} required /></label><EvidenceInput file={contributionFile} onChange={setContributionFile} /><button className="primary-button" disabled={add.isPending}><Banknote size={16} />Внести</button></form> : null}
        {contributions.isPending ? <div className="state">Загрузка</div> : <div className="table-wrap"><table><thead><tr><th>Дата</th><th>Сумма</th><th>Тип</th><th>Источник</th><th>Событие</th></tr></thead><tbody>{contributions.data?.map((item) => <tr key={item.id}><td>{formatLocalDateTime(item.created_at)}</td><td><strong>{exact(item.amount)}</strong></td><td>{item.entry_type}</td><td>{item.source_reference}</td><td><code>{item.event_id.slice(0, 12)}</code></td></tr>)}</tbody></table></div>}
        {add.isError ? <ErrorLine error={add.error} /> : null}
      </section> : null}
    </>
  );
}

function CommitmentPanel({
  principal,
  policies,
  accounts,
  commitments,
  members,
  onDone,
}: {
  principal: Principal;
  policies: RiskPolicy[];
  accounts: ShareAccount[];
  commitments: ExposureCommitment[];
  members: InventoryMember[];
  onDone: () => Promise<unknown>;
}) {
  const canOperate = hasRole(principal, "RISK_ADMIN", "AUDITOR");
  const exposableAccounts = accounts.filter((item) => ["GUARANTEE", "ROLE"].includes(item.contour) && item.status === "ACTIVE");
  const [accountId, setAccountId] = useState(exposableAccounts[0]?.id ?? "");
  const account = accounts.find((item) => item.id === accountId);
  const matchingPolicies = policies.filter((item) => item.status === "ACTIVE" && item.denomination === account?.denomination);
  const [policyId, setPolicyId] = useState("");
  const [type, setType] = useState<CommitmentType>("DIRECT_OBLIGATION");
  const [riskType, setRiskType] = useState("DELIVERY");
  const [riskId, setRiskId] = useState<string>(() => crypto.randomUUID());
  const [debtor, setDebtor] = useState("");
  const [beneficiary, setBeneficiary] = useState("");
  const [roleAssignment, setRoleAssignment] = useState("");
  const [amount, setAmount] = useState("");
  const [maxLoss, setMaxLoss] = useState("");
  const [ratio, setRatio] = useState("1");
  const [startsAt, setStartsAt] = useState(() => localDateInput(0));
  const [expiresAt, setExpiresAt] = useState(() => localDateInput(30));
  const [releaseCondition, setReleaseCondition] = useState("Подтвержденное исполнение обязательства.");
  const [triggerConditions, setTriggerConditions] = useState("Документально подтвержденный неисполненный ущерб.");
  const [exclusions, setExclusions] = useState("Защищенная часть пая и форс-мажор.");
  const [preview, setPreview] = useState<ExposurePreview | null>(null);
  const [releaseFiles, setReleaseFiles] = useState<Record<string, File | null>>({});
  const [releaseReasons, setReleaseReasons] = useState<Record<string, string>>({});
  const payload = () => ({
    account_id: accountId,
    policy_id: policyId || matchingPolicies[0]?.id || "",
    commitment_type: type,
    risk_type: riskType,
    risk_id: riskId,
    debtor_member_id: debtor || null,
    beneficiary_member_id: beneficiary || null,
    role_assignment_id: roleAssignment || null,
    amount_reserved: amount,
    max_loss: maxLoss,
    coverage_ratio: ratio,
    starts_at: new Date(startsAt).toISOString(),
    expires_at: new Date(expiresAt).toISOString(),
    release_condition: releaseCondition,
    trigger_conditions: triggerConditions,
    exclusions,
  });
  const calculate = useMutation({
    mutationFn: () => previewExposure({
      account_id: accountId,
      policy_id: policyId || matchingPolicies[0]?.id || "",
      commitment_type: type,
      amount_reserved: amount,
      max_loss: maxLoss,
    }),
    onSuccess: setPreview,
  });
  const propose = useMutation({
    mutationFn: () => proposeExposure(payload()),
    onSuccess: async () => {
      setPreview(null);
      setRiskId(crypto.randomUUID());
      setAmount("");
      setMaxLoss("");
      await onDone();
    },
  });
  const accept = useMutation({ mutationFn: acceptExposure, onSuccess: onDone });
  const release = useMutation({
    mutationFn: async (item: ExposureCommitment) => {
      const file = releaseFiles[item.id];
      const reason = releaseReasons[item.id] ?? "";
      if (!file) throw new Error("EVIDENCE_REQUIRED");
      const evidenceId = await uploadEvidence(item.cooperative_id, file, "RISK_COMMITMENT_RELEASE");
      return releaseExposure(item, reason, [evidenceId]);
    },
    onSuccess: onDone,
  });
  const types = account?.contour === "ROLE"
    ? [["ROLE_BOND", "Ответственность роли"]]
    : [["DIRECT_OBLIGATION", "Прямое обязательство"], ["GUARANTEE", "Поручительство"], ["CREDIT_LIMIT", "Кредитный лимит"]];
  return (
    <>
      {canOperate ? <section className="panel risk-command">
        <div className="panel-heading"><h2>Новый резерв ответственности</h2><span>принятие владельцем пая</span></div>
        <form className="risk-form commitment-form" onSubmit={(event) => { event.preventDefault(); propose.mutate(); }}>
          <label>Счёт<select value={accountId} onChange={(event) => { const next = accounts.find((item) => item.id === event.target.value); setAccountId(event.target.value); setPolicyId(""); setType(next?.contour === "ROLE" ? "ROLE_BOND" : "DIRECT_OBLIGATION"); setPreview(null); }} required><option value="">Выберите</option>{exposableAccounts.map((item) => <option key={item.id} value={item.id}>{memberName(members, item.member_id)} · {contourNames[item.contour]}</option>)}</select></label>
          <label>Политика<select value={policyId || matchingPolicies[0]?.id || ""} onChange={(event) => { setPolicyId(event.target.value); setPreview(null); }} required><option value="">Выберите</option>{matchingPolicies.map((item) => <option key={item.id} value={item.id}>{item.denomination} · v{item.policy_version}</option>)}</select></label>
          <label>Тип<select value={type} onChange={(event) => { setType(event.target.value as CommitmentType); setPreview(null); }}>{types.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label>Код риска<input value={riskType} pattern="[A-Za-z0-9._-]+" onChange={(event) => setRiskType(event.target.value)} required /></label>
          <label className="span-two">ID риска<input value={riskId} onChange={(event) => setRiskId(event.target.value)} required /></label>
          <label>Должник<select value={debtor} onChange={(event) => setDebtor(event.target.value)} required={type === "GUARANTEE"}><option value="">Владелец счёта</option>{members.map((item) => <option key={item.member_id} value={item.member_id}>{item.display_name}</option>)}</select></label>
          <label>Бенефициар<select value={beneficiary} onChange={(event) => setBeneficiary(event.target.value)} required={type === "GUARANTEE"}><option value="">Не указан</option>{members.map((item) => <option key={item.member_id} value={item.member_id}>{item.display_name}</option>)}</select></label>
          {type === "ROLE_BOND" ? <label className="span-two">ID назначения роли<input value={roleAssignment} onChange={(event) => setRoleAssignment(event.target.value)} required /></label> : null}
          <label>Резерв<input inputMode="decimal" value={amount} onChange={(event) => { setAmount(event.target.value); setPreview(null); }} required /></label>
          <label>Максимальный ущерб<input inputMode="decimal" value={maxLoss} onChange={(event) => { setMaxLoss(event.target.value); setPreview(null); }} required /></label>
          <label>Доля покрытия<input inputMode="decimal" value={ratio} onChange={(event) => setRatio(event.target.value)} required /></label>
          <label>Начало<input type="datetime-local" value={startsAt} onChange={(event) => setStartsAt(event.target.value)} required /></label>
          <label>Окончание<input type="datetime-local" value={expiresAt} onChange={(event) => setExpiresAt(event.target.value)} required /></label>
          <label className="span-two">Условие освобождения<input value={releaseCondition} onChange={(event) => setReleaseCondition(event.target.value)} required /></label>
          <label className="span-two">Условия срабатывания<input value={triggerConditions} onChange={(event) => setTriggerConditions(event.target.value)} required /></label>
          <label className="span-two">Исключения<input value={exclusions} onChange={(event) => setExclusions(event.target.value)} required /></label>
          <div className="risk-form-actions"><button type="button" className="secondary-button" disabled={calculate.isPending} onClick={() => calculate.mutate()}><Scale size={16} />Рассчитать</button><button className="primary-button" disabled={!preview?.allowed || propose.isPending}><LockKeyhole size={16} />Предложить</button></div>
        </form>
        {preview ? <div className={`exposure-preview ${preview.allowed ? "allowed" : "denied"}`}><div><span>Доступно до</span><strong>{exact(preview.account_available_before)}</strong></div><div><span>Доступно после</span><strong>{exact(preview.account_available_after)}</strong></div><div><span>Участник после</span><strong>{exact(preview.member_exposure_after)} / {exact(preview.max_member_exposure)}</strong></div><div><span>Группа после</span><strong>{exact(preview.related_exposure_after)} / {exact(preview.max_related_exposure)}</strong></div><Status value={preview.allowed ? "ACTIVE" : "REJECTED"} />{preview.reason_code ? <code>{preview.reason_code}</code> : null}</div> : null}
        {calculate.isError ? <ErrorLine error={calculate.error} /> : null}
        {propose.isError ? <ErrorLine error={propose.error} /> : null}
      </section> : null}
      <section className="panel">
        <div className="panel-heading"><h2>Резервы и поручительства</h2><span>{commitments.length}</span></div>
        <div className="table-wrap"><table className="risk-table commitment-table"><thead><tr><th>Владелец</th><th>Тип и риск</th><th>Резерв</th><th>Ущерб</th><th>Срок</th><th>Условия</th><th>Статус</th><th>Действие</th></tr></thead><tbody>{commitments.map((item) => {
          const isOwner = principal.member_id === item.owner_member_id;
          return <tr key={item.id}><td><strong>{memberName(members, item.owner_member_id)}</strong><small>{item.terms_hash.slice(0, 21)} · v{item.version}</small></td><td>{commitmentNames[item.commitment_type] ?? item.commitment_type}<small>{item.risk_type} · {item.risk_id.slice(0, 8)}</small></td><td><strong>{exact(item.amount_reserved)}</strong></td><td>{exact(item.max_loss)}<small>{exact(item.coverage_ratio)} доля</small></td><td>{formatLocalDateTime(item.expires_at)}</td><td><details><summary>Условия</summary><p>{item.trigger_conditions}</p><p>{item.exclusions}</p></details></td><td><Status value={item.status} /></td><td>{item.status === "PROPOSED" && isOwner ? <button className="compact-command" disabled={accept.isPending} onClick={() => accept.mutate(item)}><BadgeCheck size={14} />Принять</button> : canOperate && item.status === "ACTIVE" ? <div className="inline-decision"><input aria-label="Основание освобождения" placeholder="Причина" value={releaseReasons[item.id] ?? ""} onChange={(event) => setReleaseReasons((current) => ({ ...current, [item.id]: event.target.value }))} /><input aria-label="Файл освобождения" type="file" accept={evidenceAccept} onChange={(event) => setReleaseFiles((current) => ({ ...current, [item.id]: event.target.files?.[0] ?? null }))} /><button className="compact-command" disabled={!releaseFiles[item.id] || (releaseReasons[item.id]?.length ?? 0) < 2 || release.isPending} onClick={() => release.mutate(item)}><Unlock size={14} />Освободить</button></div> : <span className="muted-value">—</span>}</td></tr>;
        })}</tbody></table></div>
        {accept.isError ? <ErrorLine error={accept.error} /> : null}
        {release.isError ? <ErrorLine error={release.error} /> : null}
      </section>
    </>
  );
}

function RelatedPanel({
  principal,
  cooperativeId,
  links,
  members,
  onDone,
}: {
  principal: Principal;
  cooperativeId: string;
  links: RelatedLink[];
  members: InventoryMember[];
  onDone: () => Promise<unknown>;
}) {
  const canPropose = hasRole(principal, "RISK_ADMIN");
  const canDecide = hasRole(principal, "RISK_ADMIN", "AUDITOR");
  const [memberA, setMemberA] = useState("");
  const [memberB, setMemberB] = useState("");
  const [relation, setRelation] = useState<"HOUSEHOLD" | "CONTROL" | "RELATED">("RELATED");
  const [statement, setStatement] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [decisionFiles, setDecisionFiles] = useState<Record<string, File | null>>({});
  const [decisionNotes, setDecisionNotes] = useState<Record<string, string>>({});
  const propose = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error("EVIDENCE_REQUIRED");
      const evidenceId = await uploadEvidence(cooperativeId, file, "RISK_RELATED_PARTY");
      return proposeRelatedLink({ cooperative_id: cooperativeId, member_a_id: memberA, member_b_id: memberB, relation_type: relation, source_statement: statement, evidence_ids: [evidenceId] });
    },
    onSuccess: async () => { setStatement(""); setFile(null); await onDone(); },
  });
  const decide = useMutation({
    mutationFn: async ({ link, approve }: { link: RelatedLink; approve: boolean }) => {
      const evidence = decisionFiles[link.id];
      if (!evidence) throw new Error("EVIDENCE_REQUIRED");
      const evidenceId = await uploadEvidence(link.cooperative_id, evidence, "RISK_RELATED_PARTY_DECISION");
      return decideRelatedLink(link, approve, decisionNotes[link.id] ?? "", [evidenceId]);
    },
    onSuccess: onDone,
  });
  return (
    <>
      {canPropose ? <section className="panel risk-command">
        <div className="panel-heading"><h2>Связанные участники</h2><span>единый лимит группы</span></div>
        <form className="risk-form" onSubmit={(event) => { event.preventDefault(); propose.mutate(); }}>
          <label>Участник A<select value={memberA} onChange={(event) => setMemberA(event.target.value)} required><option value="">Выберите</option>{members.map((item) => <option key={item.member_id} value={item.member_id}>{item.display_name}</option>)}</select></label>
          <label>Участник B<select value={memberB} onChange={(event) => setMemberB(event.target.value)} required><option value="">Выберите</option>{members.filter((item) => item.member_id !== memberA).map((item) => <option key={item.member_id} value={item.member_id}>{item.display_name}</option>)}</select></label>
          <label>Связь<select value={relation} onChange={(event) => setRelation(event.target.value as typeof relation)}><option value="HOUSEHOLD">Домохозяйство</option><option value="CONTROL">Контроль</option><option value="RELATED">Иная связанность</option></select></label>
          <label className="span-two">Основание<input value={statement} onChange={(event) => setStatement(event.target.value)} required /></label>
          <EvidenceInput file={file} onChange={setFile} />
          <button className="primary-button" disabled={propose.isPending}><Link2 size={16} />Предложить</button>
        </form>
        {propose.isError ? <ErrorLine error={propose.error} /> : null}
      </section> : null}
      <section className="panel">
        <div className="panel-heading"><h2>Реестр связанности</h2><span>{links.length}</span></div>
        <div className="table-wrap"><table className="risk-table"><thead><tr><th>Участник A</th><th>Участник B</th><th>Тип</th><th>Основание</th><th>Статус</th><th>Независимое решение</th></tr></thead><tbody>{links.map((link) => {
          const independent = principal.member_id !== link.proposed_by_member_id && principal.member_id !== link.member_a_id && principal.member_id !== link.member_b_id;
          return <tr key={link.id}><td>{memberName(members, link.member_a_id)}</td><td>{memberName(members, link.member_b_id)}</td><td>{link.relation_type}</td><td>{link.source_statement}<small>{formatLocalDateTime(link.created_at)}</small></td><td><Status value={link.status} /></td><td>{canDecide && independent && link.status === "PROPOSED" ? <div className="inline-decision"><input aria-label="Мотивировка решения" placeholder="Мотивировка" value={decisionNotes[link.id] ?? ""} onChange={(event) => setDecisionNotes((current) => ({ ...current, [link.id]: event.target.value }))} /><input aria-label="Основание решения" type="file" accept={evidenceAccept} onChange={(event) => setDecisionFiles((current) => ({ ...current, [link.id]: event.target.files?.[0] ?? null }))} /><div><button className="icon-button" title="Утвердить связь" disabled={!decisionFiles[link.id] || (decisionNotes[link.id]?.length ?? 0) < 2 || decide.isPending} onClick={() => decide.mutate({ link, approve: true })}><Check size={14} /></button><button className="icon-button danger" title="Отклонить связь" disabled={!decisionFiles[link.id] || (decisionNotes[link.id]?.length ?? 0) < 2 || decide.isPending} onClick={() => decide.mutate({ link, approve: false })}><X size={14} /></button></div></div> : <span className="muted-value">{link.decided_at ? formatLocalDateTime(link.decided_at) : "—"}</span>}</td></tr>;
        })}</tbody></table></div>
        {decide.isError ? <ErrorLine error={decide.error} /> : null}
      </section>
    </>
  );
}

function LiabilityPanel({
  principal,
  commitments,
  cases,
  members,
  onDone,
}: {
  principal: Principal;
  commitments: ExposureCommitment[];
  cases: LiabilityCase[];
  members: InventoryMember[];
  onDone: () => Promise<unknown>;
}) {
  const canOperate = hasRole(principal, "RISK_ADMIN", "AUDITOR");
  const eligible = commitments.filter((item) => ["ACTIVE", "RELEASED"].includes(item.status) && item.owner_member_id !== principal.member_id);
  const [commitmentId, setCommitmentId] = useState(eligible[0]?.id ?? "");
  const [incident, setIncident] = useState("");
  const [affected, setAffected] = useState("");
  const [facts, setFacts] = useState("");
  const [causalGraph, setCausalGraph] = useState('{"cause":"","effect":""}');
  const [openFile, setOpenFile] = useState<File | null>(null);
  const [assessmentFiles, setAssessmentFiles] = useState<Record<string, File | null>>({});
  const [losses, setLosses] = useState<Record<string, string>>({});
  const [rationales, setRationales] = useState<Record<string, string>>({});
  const [faults, setFaults] = useState<Record<string, FaultClass>>({});
  const [appeals, setAppeals] = useState<Record<string, string>>({});
  const open = useMutation({
    mutationFn: async () => {
      if (!openFile) throw new Error("EVIDENCE_REQUIRED");
      const selectedCommitment = commitments.find((item) => item.id === commitmentId);
      if (!selectedCommitment) throw new Error("COMMITMENT_REQUIRED");
      const evidenceId = await uploadEvidence(selectedCommitment.cooperative_id, openFile, "RISK_LIABILITY_CASE");
      return openLiabilityCase({ commitment_id: commitmentId, incident_reference: incident, affected_amount: affected, facts, causal_graph: JSON.parse(causalGraph) as Record<string, unknown>, evidence_ids: [evidenceId] });
    },
    onSuccess: async () => { setIncident(""); setAffected(""); setFacts(""); setOpenFile(null); await onDone(); },
  });
  const assess = useMutation({
    mutationFn: async (item: LiabilityCase) => {
      const evidence = assessmentFiles[item.id];
      if (!evidence) throw new Error("EVIDENCE_REQUIRED");
      const evidenceId = await uploadEvidence(item.cooperative_id, evidence, "RISK_LIABILITY_ASSESSMENT");
      return assessLiabilityCase(item, {
        fault_class: faults[item.id] ?? "NEGLIGENCE",
        assessed_loss: losses[item.id] ?? "",
        rationale: rationales[item.id] ?? "",
        appeal_until: new Date(appeals[item.id] ?? localDateInput(14)).toISOString(),
        evidence_ids: [evidenceId],
      });
    },
    onSuccess: onDone,
  });
  return (
    <>
      {canOperate ? <section className="panel risk-command">
        <div className="panel-heading"><h2>Открыть случай ответственности</h2><span>без списания пая</span></div>
        <form className="risk-form liability-form" onSubmit={(event) => { event.preventDefault(); open.mutate(); }}>
          <label className="span-two">Резерв<select value={commitmentId} onChange={(event) => setCommitmentId(event.target.value)} required><option value="">Выберите</option>{eligible.map((item) => <option key={item.id} value={item.id}>{memberName(members, item.owner_member_id)} · {commitmentNames[item.commitment_type]} · {exact(item.max_loss)}</option>)}</select></label>
          <label>Код инцидента<input value={incident} pattern="[A-Za-z0-9._-]+" onChange={(event) => setIncident(event.target.value)} required /></label>
          <label>Затронутая сумма<input inputMode="decimal" value={affected} onChange={(event) => setAffected(event.target.value)} required /></label>
          <label className="span-two">Факты<textarea value={facts} onChange={(event) => setFacts(event.target.value)} required /></label>
          <label className="span-two">Причинная схема JSON<textarea value={causalGraph} onChange={(event) => setCausalGraph(event.target.value)} required /></label>
          <EvidenceInput file={openFile} onChange={setOpenFile} />
          <button className="primary-button" disabled={open.isPending}><ShieldAlert size={16} />Открыть</button>
        </form>
        {open.isError ? <ErrorLine error={open.error} /> : null}
      </section> : null}
      <section className="panel">
        <div className="panel-heading"><h2>Случаи ответственности</h2><span>{cases.length}</span></div>
        <div className="table-wrap"><table className="risk-table liability-table"><thead><tr><th>Инцидент</th><th>Ответственный</th><th>Затронуто</th><th>Факты</th><th>Оценка</th><th>Статус</th><th>Независимое решение</th></tr></thead><tbody>{cases.map((item) => {
          const independent = principal.member_id !== item.opened_by_member_id && principal.member_id !== item.responsible_member_id;
          return <tr key={item.id}><td><strong>{item.incident_reference}</strong><small>{formatLocalDateTime(item.created_at)}</small></td><td>{memberName(members, item.responsible_member_id)}</td><td>{exact(item.affected_amount)}</td><td><details><summary>Материалы</summary><p>{item.facts}</p><pre>{JSON.stringify(item.causal_graph, null, 2)}</pre></details></td><td>{item.assessed_loss === null ? "—" : <><strong>{exact(item.assessed_loss)}</strong><small>{item.fault_class}</small></>}</td><td><Status value={item.status} /><small>исполнение: NOT_EXECUTED</small></td><td>{canOperate && independent && item.status === "OPEN" ? <div className="assessment-grid"><select aria-label="Класс вины" value={faults[item.id] ?? "NEGLIGENCE"} onChange={(event) => setFaults((current) => ({ ...current, [item.id]: event.target.value as FaultClass }))}><option value="FORCE_MAJEURE">Форс-мажор</option><option value="GOOD_FAITH_ERROR">Добросовестная ошибка</option><option value="NEGLIGENCE">Неосторожность</option><option value="GROSS_NEGLIGENCE">Грубая неосторожность</option><option value="INTENT">Умысел</option><option value="COLLUSION">Сговор</option></select><input aria-label="Оцененный ущерб" placeholder="Ущерб" inputMode="decimal" value={losses[item.id] ?? ""} onChange={(event) => setLosses((current) => ({ ...current, [item.id]: event.target.value }))} /><input aria-label="Мотивировка оценки" placeholder="Мотивировка" value={rationales[item.id] ?? ""} onChange={(event) => setRationales((current) => ({ ...current, [item.id]: event.target.value }))} /><input aria-label="Срок обжалования" type="datetime-local" value={appeals[item.id] ?? localDateInput(14)} onChange={(event) => setAppeals((current) => ({ ...current, [item.id]: event.target.value }))} /><input aria-label="Основание оценки" type="file" accept={evidenceAccept} onChange={(event) => setAssessmentFiles((current) => ({ ...current, [item.id]: event.target.files?.[0] ?? null }))} /><button className="compact-command" disabled={!assessmentFiles[item.id] || !(losses[item.id]?.length) || (rationales[item.id]?.length ?? 0) < 2 || assess.isPending} onClick={() => assess.mutate(item)}><FileCheck2 size={14} />Оценить</button></div> : <span className="muted-value">{item.appeal_until ? `Обжалование до ${formatLocalDateTime(item.appeal_until)}` : "—"}</span>}</td></tr>;
        })}</tbody></table></div>
        {assess.isError ? <ErrorLine error={assess.error} /> : null}
      </section>
    </>
  );
}
