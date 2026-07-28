import {
  AlertOctagon,
  BadgeCheck,
  Ban,
  Boxes,
  Check,
  Download,
  FileArchive,
  FileKey,
  Fingerprint,
  KeyRound,
  Link2,
  Network,
  PackageCheck,
  RadioTower,
  RefreshCw,
  Scale,
  ShieldAlert,
  ShieldCheck,
  Upload,
  UserCheck
} from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useMemo, useState } from "react";

import {
  AdminApiError,
  getRoles,
  getUsers,
  type Principal,
  type RoleAssignment,
  type RoleCode,
  type UserAccount
} from "./api/admin";
import {
  acceptNodeResponsibility,
  activateFederationNode,
  applySyncPackage,
  approveNodeContract,
  approveNodeLimit,
  changeFederationNodeStatus,
  closeOfflineEpoch,
  createNodeApplication,
  decideNodeAudit,
  downloadSyncArchive,
  exportSyncPackage,
  getFederationNodes,
  getFederationPaperForms,
  getNodeApplications,
  getNodeBonds,
  getNodeChallenges,
  getNodeContracts,
  getNodeExposures,
  getNodeIncidents,
  getNodeKeyRotations,
  getNodeLimits,
  getNodeResponsibilities,
  getOfflineEpochs,
  getSyncConflicts,
  getSyncPackages,
  getSyncReceipts,
  importSyncPackage,
  issueNodeChallenge,
  openNodeIncident,
  openOfflineEpoch,
  proposeNodeContract,
  proposeNodeLimit,
  recordNodeChallengeResponse,
  registerNodeBond,
  reserveNodeExposure,
  resolveNodeIncident,
  resolveSyncConflict,
  submitNodeApplication,
  verifyNodeIdentity,
  type FederationNode,
  type NodeApplication,
  type NodeResponsibility,
  type OfflineEpoch,
  type SyncConflict,
  type SyncPackage
} from "./api/federation";
import FederationKeyRotations from "./FederationKeyRotations";
import FederationPaperForms from "./FederationPaperForms";
import { userErrorMessage } from "./shared/api-error";
import { formatLocalDateTime } from "./shared/date-time";
import { formatDecimal } from "./shared/decimal";
import "./federation.css";

type Section = "nodes" | "onboarding" | "liability" | "offline" | "sync" | "security";
type RunAction = (action: () => Promise<unknown>) => void;

const statusNames: Record<string, string> = {
  DRAFT: "Черновик",
  APPLICATION_SUBMITTED: "Заявка подана",
  IDENTITY_VERIFIED: "Личности проверены",
  TECHNICAL_CHALLENGE: "Техническая проверка",
  AUDIT_PENDING: "Аудит",
  LIMITED: "Ограничен",
  ACTIVE: "Активен",
  SUSPENDED: "Приостановлен",
  QUARANTINED: "Карантин",
  REVOKED: "Отозван",
  PROPOSED: "Предложено",
  PENDING: "Ожидает",
  PASSED: "Пройдено",
  OPEN: "Открыт",
  CLOSED: "Закрыт",
  SIMULATED: "Смоделирован",
  CONFLICT: "Конфликт",
  APPLIED: "Применён",
  RESOLVED: "Разрешён",
  REJECTED: "Отклонён",
  STANDARD: "Стандартное",
  UNTRUSTED: "Нет доверия"
};

const responsibilityLabels: Record<string, string> = {
  OWNER_SIGNATORY: "Подписант владельца",
  TECHNICAL_CUSTODIAN: "Технический хранитель",
  SECURITY_ADMINISTRATOR: "Администратор безопасности",
  BUSINESS_OPERATOR: "Оператор деятельности",
  NODE_AUDITOR: "Аудитор узла"
};

const enrollmentRoles: Array<[string, string, RoleCode[]]> = [
  ["owner", "Подписант владельца", ["NODE_BUSINESS_OPERATOR", "NODE_REGISTRAR"]],
  ["technical", "Технический хранитель", ["NODE_TECHNICAL_CUSTODIAN"]],
  ["security", "Администратор безопасности", ["NODE_SECURITY_ADMIN", "SECURITY_ADMIN"]],
  ["business", "Оператор деятельности", ["NODE_BUSINESS_OPERATOR"]],
  ["auditor", "Аудитор узла", ["NODE_AUDITOR", "AUDITOR"]]
];

function hasRole(principal: Principal, ...roles: RoleCode[]) {
  return principal.roles.some((grant) => roles.includes(grant.role));
}

function errorText(error: unknown): string {
  return userErrorMessage(error);
}

function Status({ value }: { value: string }) {
  const good = ["ACTIVE", "PASSED", "APPLIED", "RESOLVED", "CLOSED", "STANDARD"].includes(value);
  const bad = ["REJECTED", "REVOKED", "QUARANTINED", "CONFLICT", "CRITICAL"].includes(value);
  return <span className={`status ${good ? "good" : bad ? "bad" : "warn"}`}>{statusNames[value] ?? value}</span>;
}

function Hash({ value }: { value: string | null }) {
  return <code className="federation-hash" title={value ?? undefined}>{value ?? "—"}</code>;
}

function amount(value: string | null) {
  return value === null ? "—" : formatDecimal(value, "ru-RU", { maximumFractionDigits: 12 });
}

function localDate(offsetDays: number) {
  const date = new Date(Date.now() + offsetDays * 86_400_000);
  date.setMinutes(date.getMinutes() - date.getTimezoneOffset());
  return date.toISOString().slice(0, 16);
}

function linkedUser(role: RoleAssignment, users: UserAccount[]) {
  return users.find((item) => item.id === role.user_id);
}

function NodeRegistrationForm({
  users,
  roles,
  run
}: {
  users: UserAccount[];
  roles: RoleAssignment[];
  run: RunAction;
}) {
  const available = roles.filter((item) => item.status === "ACTIVE" && linkedUser(item, users)?.member_id);
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const partyRoles = [
      ["owner", "OWNER_SIGNATORY"],
      ["technical", "TECHNICAL_CUSTODIAN"],
      ["security", "SECURITY_ADMINISTRATOR"],
      ["business", "BUSINESS_OPERATOR"],
      ["auditor", "NODE_AUDITOR"]
    ] as const;
    const responsibleParties = partyRoles.map(([field, roleCode]) => {
      const assignment = available.find((item) => item.id === data.get(field));
      const user = assignment ? linkedUser(assignment, users) : undefined;
      if (!assignment || !user?.member_id) throw new Error("RESPONSIBLE_PARTY_REQUIRED");
      return {
        member_id: user.member_id,
        role_assignment_id: assignment.id,
        role_code: roleCode,
        capability_scope: ["TEST_EXCHANGE"],
        responsibility_scope: `${responsibilityLabels[roleCode]}: персональная ответственность за действия узла.`,
        max_exposure: String(data.get("max_exposure")),
        exposure_unit: String(data.get("unit")),
        valid_until: null
      };
    });
    const endpoint = String(data.get("endpoint"));
    run(() => createNodeApplication({
      node_code: data.get("node_code"),
      display_name: data.get("display_name"),
      owner_legal_name: data.get("owner_name"),
      owner_registration_code: data.get("owner_code"),
      owner_jurisdiction: data.get("jurisdiction"),
      owner_contact_payload: { channel: data.get("owner_contact"), verified: true },
      territory: data.get("territory"),
      purpose: data.get("purpose"),
      network_endpoints: endpoint ? [{ transport: "HTTPS", uri: endpoint }] : [],
      hardware_manifest: { platform: data.get("platform"), disk_encryption: true },
      release_manifest: { release: data.get("release"), image_verified: true },
      capabilities: ["TEST_EXCHANGE"],
      supported_protocols: ["1.0"],
      supported_policies: { federation: 1, identity: 1 },
      data_scopes: { catalog: "contract-bound", personal_data: false },
      requested_limits: { TEST_EXCHANGE: { unit: data.get("unit"), maximum: data.get("max_exposure") } },
      recovery_contacts: [{ role: "SECURITY_ADMINISTRATOR", channel: data.get("owner_contact") }],
      security_questionnaire: { backup_tested: true, dual_control: true },
      evidence_ids: [data.get("evidence_id")],
      responsible_parties: responsibleParties,
      public_key_base64: data.get("public_key"),
      certificate_valid_from: new Date(Date.now() - 60_000).toISOString(),
      certificate_valid_until: new Date(Date.now() + 365 * 86_400_000).toISOString(),
      proposed_trust_expiry: new Date(Date.now() + 365 * 86_400_000).toISOString()
    }));
  }
  return <section className="federation-command-band">
    <form className="enrollment-form" onSubmit={submit}>
      <strong><Link2 size={16} /> Заявка внешнего узла</strong>
      <label>Код узла<input name="node_code" required placeholder="REGION-01" /></label>
      <label>Название<input name="display_name" required /></label>
      <label>Владелец<input name="owner_name" required /></label>
      <label>Регистрационный код<input name="owner_code" required /></label>
      <label>Юрисдикция<input name="jurisdiction" required /></label>
      <label>Территория<input name="territory" required /></label>
      <label>Контакт владельца<input name="owner_contact" required /></label>
      <label>Endpoint<input name="endpoint" type="url" /></label>
      <label>Платформа<input name="platform" defaultValue="linux-amd64" required /></label>
      <label>Релиз<input name="release" required /></label>
      <label>Единица лимита<input name="unit" defaultValue="UNIT" required /></label>
      <label>Максимальная ответственность<input name="max_exposure" type="number" min="0" step="any" defaultValue="100" required /></label>
      <label className="wide-field">Назначение<textarea name="purpose" required /></label>
      <label className="wide-field">Ed25519 public key, base64<textarea name="public_key" required /></label>
      <label className="wide-field">ID доказательства<input name="evidence_id" type="text" required /></label>
      {enrollmentRoles.map(([field, label, allowed]) => <label key={field}>{label}<select name={field} required defaultValue=""><option value="">Выберите</option>{available.filter((item) => allowed.includes(item.role_code)).map((item) => <option value={item.id} key={item.id}>{linkedUser(item, users)?.login} · {item.role_code}</option>)}</select></label>)}
      <button className="primary-button" type="submit"><Fingerprint size={16} />Зарегистрировать</button>
    </form>
  </section>;
}

function NodesSection({
  nodes,
  contracts,
  exposures,
  principal,
  run
}: {
  nodes: FederationNode[];
  contracts: Awaited<ReturnType<typeof getNodeContracts>>;
  exposures: Awaited<ReturnType<typeof getNodeExposures>>;
  principal: Principal;
  run: RunAction;
}) {
  const [rationale, setRationale] = useState("Плановое решение оператора после проверки состояния.");
  return <>
    <section className="panel"><div className="table-wrap"><table className="federation-table"><thead><tr><th>Узел</th><th>Территория</th><th>Доверие</th><th>Экспозиция</th><th>Последняя синхронизация</th><th>Действия</th></tr></thead><tbody>{nodes.map((node) => {
      const contract = contracts.find((item) => item.node_id === node.id && item.status === "ACTIVE");
      const nodeExposure = exposures.filter((item) => item.node_id === node.id);
      return <tr key={node.id}><td><strong>{node.display_name}</strong><small>{node.node_code} · v{node.version}</small></td><td>{node.territory}</td><td><Status value={node.status} /><small>{statusNames[node.trust_level] ?? node.trust_level}</small></td><td>{nodeExposure.length ? nodeExposure.map((item) => <span key={item.id}>{amount(item.current_amount)} + {amount(item.reserved_amount)} {item.unit}</span>) : "—"}</td><td>{formatLocalDateTime(node.last_sync_at)}<Hash value={node.last_checkpoint_hash} /></td><td><div className="table-actions">{node.status === "LIMITED" && hasRole(principal, "NODE_REGISTRAR", "AUDITOR", "NODE_AUDITOR") ? <button title="Активировать" onClick={() => run(() => activateFederationNode(node))}><BadgeCheck size={15} /></button> : null}{["ACTIVE", "LIMITED"].includes(node.status) && hasRole(principal, "NODE_REGISTRAR", "NODE_SECURITY_ADMIN", "SECURITY_ADMIN") ? <><button title="Приостановить" onClick={() => run(() => changeFederationNodeStatus(node, "suspend", rationale))}><Ban size={15} /></button><button title="Карантин" onClick={() => run(() => changeFederationNodeStatus(node, "quarantine", rationale))}><ShieldAlert size={15} /></button></> : null}</div><small>{contract?.contract_number ?? "Нет активного договора"}</small></td></tr>;
    })}</tbody></table></div></section>
    <section className="federation-inline-form"><label>Основание действий<input value={rationale} onChange={(event) => setRationale(event.target.value)} /></label></section>
  </>;
}

function OnboardingSection({
  principal,
  applications,
  responsibilities,
  challenges,
  users,
  roles,
  run
}: {
  principal: Principal;
  applications: NodeApplication[];
  responsibilities: NodeResponsibility[];
  challenges: Awaited<ReturnType<typeof getNodeChallenges>>;
  users: UserAccount[];
  roles: RoleAssignment[];
  run: RunAction;
}) {
  const [challengeMaterial, setChallengeMaterial] = useState<string | null>(null);
  const ownAssignments = new Set(principal.roles.map((item) => item.assignment_id));
  return <>
    {hasRole(principal, "NODE_REGISTRAR") ? <NodeRegistrationForm users={users} roles={roles} run={run} /> : null}
    <section className="panel"><div className="table-wrap"><table className="federation-table"><thead><tr><th>Заявка</th><th>Возможности</th><th>Ответственные</th><th>Проверки</th><th>Действия</th></tr></thead><tbody>{applications.map((item) => {
      const parties = responsibilities.filter((party) => party.application_id === item.id);
      const challenge = challenges.find((value) => value.application_id === item.id);
      return <tr key={item.id}><td><strong>{item.id.slice(0, 8)}</strong><small>{formatLocalDateTime(item.created_at)} · v{item.version}</small><Status value={item.status} /></td><td>{item.requested_capabilities.join(", ")}</td><td><span>{parties.filter((party) => party.status === "ACTIVE").length}/{parties.length}</span><small>{parties.map((party) => responsibilityLabels[party.role_code] ?? party.role_code).join(", ")}</small></td><td>{challenge ? <><Status value={challenge.status} /><small>{formatLocalDateTime(challenge.expires_at)}</small></> : "—"}</td><td><div className="table-actions">{item.status === "DRAFT" && parties.every((party) => party.status === "ACTIVE") && hasRole(principal, "NODE_REGISTRAR") ? <button title="Подать заявку" onClick={() => run(() => submitNodeApplication(item))}><Upload size={15} /></button> : null}{item.status === "APPLICATION_SUBMITTED" && hasRole(principal, "NODE_SECURITY_ADMIN", "SECURITY_ADMIN", "AUDITOR", "NODE_AUDITOR") ? <button title="Подтвердить личности" onClick={() => run(() => verifyNodeIdentity(item, "Юридическое лицо, полномочия и ответственные проверены."))}><UserCheck size={15} /></button> : null}{item.status === "IDENTITY_VERIFIED" && hasRole(principal, "NODE_SECURITY_ADMIN", "SECURITY_ADMIN") ? <button title="Выдать challenge" onClick={() => run(() => issueNodeChallenge(item).then((value) => { setChallengeMaterial(value.nonce); return value; }))}><KeyRound size={15} /></button> : null}{item.status === "AUDIT_PENDING" && !item.audit_decided_by_user_id && hasRole(principal, "AUDITOR", "NODE_AUDITOR") ? <><button title="Одобрить аудит" onClick={() => run(() => decideNodeAudit(item, true, "Независимый аудит завершён."))}><Check size={15} /></button><button title="Отклонить" onClick={() => run(() => decideNodeAudit(item, false, "Аудит выявил неприемлемые риски."))}><Ban size={15} /></button></> : null}</div></td></tr>;
    })}</tbody></table></div></section>
    {challengeMaterial ? <p className="federation-notice"><KeyRound size={15} />{challengeMaterial}</p> : null}
    <section className="responsibility-grid">{responsibilities.map((item) => <article key={item.id}><header><strong>{responsibilityLabels[item.role_code] ?? item.role_code}</strong><Status value={item.status} /></header><span>{item.responsibility_scope}</span><dl><dt>Лимит</dt><dd>{amount(item.max_exposure)} {item.exposure_unit}</dd><dt>Участник</dt><dd>{item.member_id}</dd></dl>{item.status === "PROPOSED" && item.member_id === principal.member_id && ownAssignments.has(item.role_assignment_id) ? <button className="compact-command" onClick={() => run(() => acceptNodeResponsibility(item))}><UserCheck size={14} />Принять</button> : null}</article>)}</section>
    {challenges.filter((item) => item.status === "ISSUED").map((item) => <ChallengeResponseForm key={item.id} challengeId={item.id} run={run} />)}
  </>;
}

function ChallengeResponseForm({ challengeId, run }: { challengeId: string; run: RunAction }) {
  return <section className="federation-command-band"><form onSubmit={(event) => {
    event.preventDefault(); const data = new FormData(event.currentTarget);
    run(() => recordNodeChallengeResponse(challengeId, String(data.get("nonce")), String(data.get("signature")), JSON.parse(String(data.get("payload"))) as Record<string, unknown>));
  }}><strong><KeyRound size={15} /> Ответ внешнего узла</strong><label>Nonce<input name="nonce" required /></label><label>Подпись base64<textarea name="signature" required /></label><label className="wide-field">Payload<textarea name="payload" required defaultValue='{"release_manifest":{},"capability_statement":["TEST_EXCHANGE"],"integrity_report":{"journal":"PASS"},"test_package_receipt":{"status":"PASS"}}' /></label><button className="primary-button" type="submit"><ShieldCheck size={15} />Проверить</button></form></section>;
}

function LiabilitySection({ data, principal, run }: {
  data: {
    nodes: FederationNode[];
    applications: NodeApplication[];
    contracts: Awaited<ReturnType<typeof getNodeContracts>>;
    limits: Awaited<ReturnType<typeof getNodeLimits>>;
    bonds: Awaited<ReturnType<typeof getNodeBonds>>;
    exposures: Awaited<ReturnType<typeof getNodeExposures>>;
  };
  principal: Principal;
  run: RunAction;
}) {
  const contractCandidates = data.applications.filter((item) => item.status === "AUDIT_PENDING" && item.audit_decided_by_user_id);
  const trustedNodes = data.nodes.filter((item) => ["LIMITED", "ACTIVE"].includes(item.status));
  return <>
    <section className="federation-command-band three-columns">
      {hasRole(principal, "NODE_REGISTRAR") ? <form onSubmit={(event) => {
        event.preventDefault(); const form = new FormData(event.currentTarget);
        run(() => proposeNodeContract({ application_id: form.get("application"), contract_number: form.get("number"), trust_level: "STANDARD", capabilities: ["TEST_EXCHANGE"], event_types: ["federation.test_event", "federation.paper_form_issued", "federation.paper_operation_recorded", "federation.paper_form_voided"], inbound_scope: { mode: "quarantine-then-simulate" }, outbound_scope: { mode: "explicit-export" }, federation_limits: { maximum_value: form.get("maximum") }, allowed_counterparties: [], max_offline_hours: Number(form.get("hours")), required_protocols: ["1.0"], required_policies: { federation: 1, identity: 1 }, service_levels: { incident_notice_minutes: 30 }, liability_terms: { node_bond_required: true, ordinary_member_shares_excluded: true, maximum_loss: form.get("maximum") }, valid_from: new Date(Date.now() - 60_000).toISOString(), valid_until: new Date(String(form.get("expires"))).toISOString() }));
      }}><strong><FileKey size={15} /> Договор доверия</strong><label>Заявка<select name="application" required>{contractCandidates.map((item) => <option value={item.id} key={item.id}>{item.id.slice(0, 8)}</option>)}</select></label><label>Номер<input name="number" required /></label><label>Максимум<input name="maximum" type="number" min="0" defaultValue="100" /></label><label>Offline, часов<input name="hours" type="number" min="1" max="720" defaultValue="24" /></label><label>Действует до<input name="expires" type="datetime-local" defaultValue={localDate(180)} required /></label><button className="primary-button" type="submit"><Scale size={15} />Предложить</button></form> : null}
      {hasRole(principal, "NODE_REGISTRAR") ? <form onSubmit={(event) => {
        event.preventDefault(); const form = new FormData(event.currentTarget); const nodeId = String(form.get("node"));
        run(() => proposeNodeLimit(nodeId, { capability: "TEST_EXCHANGE", unit: form.get("unit"), max_package_value: form.get("package"), max_unsettled_obligations: form.get("unsettled"), max_external_rights: "0", max_clearing_position: "0", max_offline_hours: Number(form.get("hours")), allowed_critical_resources: [], required_confirmations: 2 }));
      }}><strong><Boxes size={15} /> Двусторонний лимит</strong><label>Узел<select name="node">{trustedNodes.map((item) => <option value={item.id} key={item.id}>{item.node_code}</option>)}</select></label><label>Единица<input name="unit" defaultValue="UNIT" /></label><label>Пакет<input name="package" type="number" min="0" defaultValue="100" /></label><label>Незакрыто<input name="unsettled" type="number" min="0" defaultValue="100" /></label><label>Offline, часов<input name="hours" type="number" min="1" max="720" defaultValue="24" /></label><button className="primary-button" type="submit"><Scale size={15} />Предложить</button></form> : null}
      {hasRole(principal, "NODE_REGISTRAR", "NODE_SECURITY_ADMIN", "SECURITY_ADMIN") ? <form onSubmit={(event) => {
        event.preventDefault(); const form = new FormData(event.currentTarget); const nodeId = String(form.get("node"));
        run(() => registerNodeBond(nodeId, { reference: form.get("reference"), amount: form.get("amount"), protected_amount: form.get("protected"), maximum_loss: form.get("loss"), unit: form.get("unit"), capability_scope: ["TEST_EXCHANGE"], evidence_ids: [form.get("evidence")], valid_from: new Date(Date.now() - 60_000).toISOString(), valid_until: new Date(String(form.get("expires"))).toISOString() }));
      }}><strong><ShieldCheck size={15} /> Залог узла</strong><label>Узел<select name="node">{trustedNodes.map((item) => <option value={item.id} key={item.id}>{item.node_code}</option>)}</select></label><label>Референс<input name="reference" required /></label><label>Сумма<input name="amount" type="number" min="0.000001" defaultValue="120" /></label><label>Защищено<input name="protected" type="number" min="0" defaultValue="20" /></label><label>Максимальный убыток<input name="loss" type="number" min="0.000001" defaultValue="100" /></label><label>Единица<input name="unit" defaultValue="UNIT" /></label><label>ID доказательства<input name="evidence" required /></label><label>Действует до<input name="expires" type="datetime-local" defaultValue={localDate(180)} required /></label><button className="primary-button" type="submit"><ShieldCheck size={15} />Зарегистрировать</button></form> : null}
    </section>
    <section className="panel"><div className="table-wrap"><table className="federation-table"><thead><tr><th>Договор</th><th>Узел</th><th>Уровень</th><th>Срок</th><th>Ответственность</th><th>Решение</th></tr></thead><tbody>{data.contracts.map((item) => <tr key={item.id}><td><strong>{item.contract_number}</strong><Hash value={item.terms_hash} /></td><td>{data.nodes.find((node) => node.id === item.node_id)?.node_code}</td><td><Status value={item.status} /><small>{item.trust_level}</small></td><td>{formatLocalDateTime(item.valid_until)}</td><td>{item.liability_terms.ordinary_member_shares_excluded ? "Паи участников исключены" : "Требует проверки"}</td><td>{item.status === "DRAFT" && hasRole(principal, "AUDITOR", "NODE_AUDITOR") ? <button className="compact-command" onClick={() => run(() => approveNodeContract(item))}><Check size={14} />Одобрить</button> : null}</td></tr>)}</tbody></table></div></section>
    <section className="liability-grid"><article><header><strong>Лимиты</strong><span>{data.limits.length}</span></header>{data.limits.map((item) => <div key={item.id}><span>{item.capability} · {amount(item.max_unsettled_obligations)} {item.unit}</span><Status value={item.status} />{item.status === "DRAFT" && hasRole(principal, "NODE_SECURITY_ADMIN", "SECURITY_ADMIN", "AUDITOR", "NODE_AUDITOR") ? <button title="Одобрить" onClick={() => run(() => approveNodeLimit(item))}><Check size={14} /></button> : null}</div>)}</article><article><header><strong>Залоги</strong><span>{data.bonds.length}</span></header>{data.bonds.map((item) => <div key={item.id}><span>{item.reference} · {amount(item.maximum_loss)}/{amount(item.amount)} {item.unit}</span><Status value={item.status} /></div>)}</article><article><header><strong>Экспозиция</strong><span>{data.exposures.length}</span></header>{data.exposures.map((item) => <div key={item.id}><span>{item.capability} · {amount(item.current_amount)} + {amount(item.reserved_amount)} {item.unit}</span><Status value="ACTIVE" /></div>)}</article></section>
    {hasRole(principal, "NODE_REGISTRAR", "NODE_SECURITY_ADMIN", "NODE_BUSINESS_OPERATOR") ? <section className="federation-inline-form"><form onSubmit={(event) => { event.preventDefault(); const form = new FormData(event.currentTarget); run(() => reserveNodeExposure(String(form.get("node")), { capability: "TEST_EXCHANGE", unit: form.get("unit"), delta: form.get("delta"), reference: form.get("reference") })); }}><label>Узел<select name="node">{trustedNodes.map((item) => <option value={item.id} key={item.id}>{item.node_code}</option>)}</select></label><label>Единица<input name="unit" defaultValue="UNIT" /></label><label>Резерв<input name="delta" type="number" min="0.000001" step="any" /></label><label>Референс<input name="reference" required /></label><button className="primary-button" type="submit"><Scale size={15} />Резервировать</button></form></section> : null}
  </>;
}

function OfflineSection({ nodes, epochs, forms, principal, run }: { nodes: FederationNode[]; epochs: OfflineEpoch[]; forms: Awaited<ReturnType<typeof getFederationPaperForms>>; principal: Principal; run: RunAction }) {
  const active = nodes.filter((item) => ["ACTIVE", "LIMITED"].includes(item.status));
  return <>
    {hasRole(principal, "NODE_REGISTRAR", "NODE_SECURITY_ADMIN", "SECURITY_ADMIN") ? <section className="federation-command-band"><form onSubmit={(event) => { event.preventDefault(); const form = new FormData(event.currentTarget); const hours = Number(form.get("hours")); run(() => openOfflineEpoch(String(form.get("node")), { base_checkpoint_hash: null, allowed_event_types: ["federation.test_event", "federation.paper_form_issued", "federation.paper_operation_recorded", "federation.paper_form_voided"], limits: { maximum_events: Number(form.get("events")), maximum_value: form.get("value"), unit: form.get("unit") }, protocol_version: "1.0", policy_versions: { federation: 1, identity: 1 }, emergency_contacts: [{ role: "SECURITY_ADMINISTRATOR", channel: form.get("contact") }], closure_rules: { dual_review: true, physical_reconciliation: true }, starts_at: new Date(Date.now() - 60_000).toISOString(), expires_at: new Date(Date.now() + hours * 3_600_000).toISOString() })); }}><strong><RadioTower size={15} /> Offline-эпоха</strong><label>Узел<select name="node">{active.map((item) => <option value={item.id} key={item.id}>{item.node_code}</option>)}</select></label><label>Часов<input name="hours" type="number" min="1" max="720" defaultValue="12" /></label><label>Событий<input name="events" type="number" min="1" defaultValue="100" /></label><label>Стоимость<input name="value" type="number" min="0" defaultValue="100" /></label><label>Единица<input name="unit" defaultValue="UNIT" /></label><label>Аварийный контакт<input name="contact" required /></label><button className="primary-button" type="submit"><RadioTower size={15} />Открыть</button></form></section> : null}
    <section className="epoch-grid">{epochs.map((item) => <article key={item.id}><header><div><strong>{nodes.find((node) => node.id === item.external_node_id)?.node_code ?? item.external_node_id}</strong><span>{item.protocol_version} · v{item.version}</span></div><Status value={item.status} /></header><dl><dt>Начало</dt><dd>{formatLocalDateTime(item.starts_at)}</dd><dt>Истечение</dt><dd>{formatLocalDateTime(item.expires_at)}</dd><dt>События</dt><dd>{item.allowed_event_types.join(", ")}</dd><dt>Policy hash</dt><dd><Hash value={item.policy_hash} /></dd></dl>{item.status === "OPEN" && hasRole(principal, "NODE_REGISTRAR", "AUDITOR", "NODE_AUDITOR") ? <button className="compact-command" onClick={() => run(() => closeOfflineEpoch(item))}><PackageCheck size={14} />Закрыть и сверить</button> : null}</article>)}</section>
    <FederationPaperForms nodes={nodes} epochs={epochs} forms={forms} principal={principal} run={run} />
  </>;
}

function SyncSection({ nodes, epochs, packages, conflicts, receipts, principal, run }: { nodes: FederationNode[]; epochs: OfflineEpoch[]; packages: SyncPackage[]; conflicts: SyncConflict[]; receipts: Awaited<ReturnType<typeof getSyncReceipts>>; principal: Principal; run: RunAction }) {
  const [archive, setArchive] = useState<File | null>(null);
  async function download(item: SyncPackage) {
    const blob = await downloadSyncArchive(item.id); const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = `sync-package-${item.id}.zip`; link.click(); URL.revokeObjectURL(url);
  }
  return <>
    <section className="federation-command-band two-columns">
      {hasRole(principal, "NODE_REGISTRAR", "NODE_SECURITY_ADMIN", "SECURITY_ADMIN") ? <form onSubmit={(event) => { event.preventDefault(); const form = new FormData(event.currentTarget); run(() => exportSyncPackage({ peer_node_id: form.get("node"), sequence_after: Number(form.get("sequence")), maximum_events: Number(form.get("events")), expiry_hours: Number(form.get("hours")), epoch_id: form.get("epoch") || null })); }}><strong><Download size={15} /> Экспорт пакета</strong><label>Узел<select name="node">{nodes.filter((item) => ["ACTIVE", "LIMITED"].includes(item.status)).map((item) => <option value={item.id} key={item.id}>{item.node_code}</option>)}</select></label><label>После sequence<input name="sequence" type="number" min="0" defaultValue="0" /></label><label>Событий<input name="events" type="number" min="1" max="10000" defaultValue="100" /></label><label>Срок, часов<input name="hours" type="number" min="1" max="168" defaultValue="24" /></label><label>Offline-эпоха<select name="epoch"><option value="">Online</option>{epochs.filter((item) => item.status === "OPEN").map((item) => <option value={item.id} key={item.id}>{item.id.slice(0, 8)}</option>)}</select></label><button className="primary-button" type="submit"><FileArchive size={15} />Собрать</button></form> : null}
      {hasRole(principal, "NODE_REGISTRAR", "NODE_SECURITY_ADMIN", "SECURITY_ADMIN") ? <form onSubmit={(event) => { event.preventDefault(); if (archive) run(() => importSyncPackage(archive)); }}><strong><Upload size={15} /> Импорт пакета</strong><label className="wide-field">ZIP-архив<input type="file" accept=".zip,application/zip" onChange={(event) => setArchive(event.target.files?.[0] ?? null)} required /></label><button className="primary-button" type="submit" disabled={!archive}><ShieldCheck size={15} />Проверить и смоделировать</button></form> : null}
    </section>
    <section className="panel"><div className="table-wrap"><table className="federation-table"><thead><tr><th>Пакет</th><th>Маршрут</th><th>Sequence</th><th>События</th><th>Проверка</th><th>Действия</th></tr></thead><tbody>{[...packages].sort((a, b) => b.created_at.localeCompare(a.created_at)).map((item) => <tr key={item.id}><td><strong>{item.id.slice(0, 8)}</strong><small>{item.direction} · {formatLocalDateTime(item.created_at)}</small><Hash value={item.archive_hash} /></td><td>{item.source_node_code}<small>→ {item.target_node_code}</small></td><td>{item.sequence_first}–{item.sequence_last}</td><td>{item.event_count}</td><td><Status value={item.status} /><small>{item.rejection_code ?? (item.simulation_summary ? `${String(item.simulation_summary.ready_events ?? 0)} ready · ${String(item.simulation_summary.conflicts ?? 0)} conflicts` : "")}</small></td><td><div className="table-actions">{item.direction === "OUTBOUND" ? <button title="Скачать" onClick={() => run(() => download(item))}><Download size={15} /></button> : null}{item.direction === "INBOUND" && item.status === "SIMULATED" && hasRole(principal, "NODE_REGISTRAR", "AUDITOR", "NODE_AUDITOR") ? <button title="Применить" onClick={() => run(() => applySyncPackage(item))}><PackageCheck size={15} /></button> : null}</div></td></tr>)}</tbody></table></div></section>
    <section className="conflict-grid">{conflicts.map((item) => <article key={item.id}><header><div><strong>{item.conflict_class}</strong><span>{item.affected_object_type}</span></div><Status value={item.status} /></header><Hash value={item.local_event_hash} /><Hash value={item.remote_event_hash} />{item.status !== "RESOLVED" && hasRole(principal, "AUDITOR", "NODE_AUDITOR", "NODE_SECURITY_ADMIN", "SECURITY_ADMIN") ? <div className="conflict-actions"><button onClick={() => run(() => resolveSyncConflict(item, "KEEP_LOCAL", "Сохраняется ранее подтверждённая ветвь."))}>Оставить локальную</button><button onClick={() => run(() => resolveSyncConflict(item, "ACCEPT_REMOTE", "Удалённая ветвь подтверждена независимой проверкой."))}>Принять удалённую</button><button onClick={() => run(() => resolveSyncConflict(item, "REJECT_PACKAGE", "Пакет отклонён комиссией."))}>Отклонить пакет</button></div> : <p>{item.decision ?? ""} · {item.rationale ?? ""}</p>}</article>)}</section>
    <section className="panel"><div className="panel-heading"><h2>Подписанные квитанции</h2><span>{receipts.length}</span></div><div className="rows">{receipts.map((item) => <div className="data-row" key={item.id}><strong>{item.package_id.slice(0, 8)}</strong><Hash value={item.receipt_hash} /><time>{formatLocalDateTime(item.created_at)}</time></div>)}</div></section>
  </>;
}

function SecuritySection({ nodes, incidents, rotations, principal, run }: { nodes: FederationNode[]; incidents: Awaited<ReturnType<typeof getNodeIncidents>>; rotations: Awaited<ReturnType<typeof getNodeKeyRotations>>; principal: Principal; run: RunAction }) {
  return <>
    {hasRole(principal, "NODE_SECURITY_ADMIN", "SECURITY_ADMIN") ? <section className="federation-command-band"><form onSubmit={(event) => { event.preventDefault(); const form = new FormData(event.currentTarget); const evidence = String(form.get("evidence")); run(() => openNodeIncident(String(form.get("node")), { incident_type: form.get("type"), severity: form.get("severity"), earliest_compromise_at: null, description: form.get("description"), evidence_ids: evidence ? [evidence] : [] })); }}><strong><AlertOctagon size={15} /> Инцидент узла</strong><label>Узел<select name="node">{nodes.map((item) => <option value={item.id} key={item.id}>{item.node_code}</option>)}</select></label><label>Тип<select name="type"><option value="INTEGRITY_FAILURE">Нарушение целостности</option><option value="KEY_COMPROMISE">Компрометация ключа</option><option value="CUSTODY_FAILURE">Нарушение хранения</option></select></label><label>Тяжесть<select name="severity"><option value="MEDIUM">Средняя</option><option value="HIGH">Высокая</option><option value="CRITICAL">Критическая</option></select></label><label>ID доказательства<input name="evidence" /></label><label className="wide-field">Описание<textarea name="description" required /></label><button className="primary-button" type="submit"><ShieldAlert size={15} />Изолировать узел</button></form></section> : null}
    <FederationKeyRotations nodes={nodes} rotations={rotations} principal={principal} run={run} />
    <section className="incident-list">{incidents.map((item) => <article key={item.id}><header><div><strong>{item.incident_type}</strong><span>{nodes.find((node) => node.id === item.node_id)?.node_code}</span></div><Status value={item.status} /></header><p>{item.description}</p><footer><span>{item.severity} · {formatLocalDateTime(item.created_at)}</span>{item.status !== "RESOLVED" && hasRole(principal, "NODE_SECURITY_ADMIN", "SECURITY_ADMIN", "AUDITOR", "NODE_AUDITOR") ? <button className="compact-command" onClick={() => run(() => resolveNodeIncident(item, "Корректирующие действия и целостность проверены."))}><ShieldCheck size={14} />Закрыть</button> : null}</footer></article>)}</section>
  </>;
}

export default function FederationView({ principal }: { principal: Principal }) {
  const client = useQueryClient();
  const [section, setSection] = useState<Section>("nodes");
  const nodes = useQuery({ queryKey: ["federation", "nodes"], queryFn: getFederationNodes });
  const applications = useQuery({ queryKey: ["federation", "applications"], queryFn: getNodeApplications });
  const responsibilities = useQuery({ queryKey: ["federation", "responsibilities"], queryFn: getNodeResponsibilities });
  const challenges = useQuery({ queryKey: ["federation", "challenges"], queryFn: getNodeChallenges });
  const contracts = useQuery({ queryKey: ["federation", "contracts"], queryFn: getNodeContracts });
  const limits = useQuery({ queryKey: ["federation", "limits"], queryFn: getNodeLimits });
  const bonds = useQuery({ queryKey: ["federation", "bonds"], queryFn: getNodeBonds });
  const exposures = useQuery({ queryKey: ["federation", "exposures"], queryFn: getNodeExposures });
  const epochs = useQuery({ queryKey: ["federation", "epochs"], queryFn: getOfflineEpochs });
  const forms = useQuery({ queryKey: ["federation", "paper-forms"], queryFn: getFederationPaperForms });
  const packages = useQuery({ queryKey: ["federation", "packages"], queryFn: getSyncPackages });
  const conflicts = useQuery({ queryKey: ["federation", "conflicts"], queryFn: getSyncConflicts });
  const receipts = useQuery({ queryKey: ["federation", "receipts"], queryFn: getSyncReceipts });
  const incidents = useQuery({ queryKey: ["federation", "incidents"], queryFn: getNodeIncidents });
  const rotations = useQuery({ queryKey: ["federation", "key-rotations"], queryFn: getNodeKeyRotations });
  const users = useQuery({ queryKey: ["users"], queryFn: getUsers });
  const roles = useQuery({ queryKey: ["roles"], queryFn: getRoles });
  const mutation = useMutation({
    mutationFn: (action: () => Promise<unknown>) => action(),
    onSuccess: () => client.invalidateQueries({ queryKey: ["federation"] })
  });
  const run: RunAction = (action) => mutation.mutate(action);
  const queries = [nodes, applications, responsibilities, challenges, contracts, limits, bonds, exposures, epochs, forms, packages, conflicts, receipts, incidents, rotations, users, roles];
  const loading = queries.some((item) => item.isLoading);
  const failure = queries.find((item) => item.error)?.error ?? mutation.error;
  const tabs = [
    ["nodes", "Узлы", Network],
    ["onboarding", "Подключение", Fingerprint],
    ["liability", "Лимиты", Scale],
    ["offline", "Offline", RadioTower],
    ["sync", "Пакеты", FileArchive],
    ["security", "Безопасность", ShieldAlert]
  ] as const;
  const metrics = useMemo(() => ({
    active: nodes.data?.filter((item) => item.status === "ACTIVE").length ?? 0,
    openEpochs: epochs.data?.filter((item) => item.status === "OPEN").length ?? 0,
    conflicts: conflicts.data?.filter((item) => item.status !== "RESOLVED").length ?? 0,
    quarantined: nodes.data?.filter((item) => item.status === "QUARANTINED").length ?? 0
  }), [nodes.data, epochs.data, conflicts.data]);

  return <div className="view-stack federation-view">
    <header className="view-header"><div><span className="eyebrow">Межузловое доверие</span><h1>Узлы и offline</h1><p>{metrics.active} активных · {metrics.openEpochs} offline-окон · {metrics.conflicts} открытых конфликтов</p></div><div className="section-tabs">{tabs.map(([key, label, Icon]) => <button className={section === key ? "active" : ""} onClick={() => setSection(key)} key={key}><Icon size={16} /><span>{label}</span></button>)}</div></header>
    <section className="metric-grid federation-metrics"><div className="metric"><Network /><span>Активные<strong>{metrics.active}</strong></span></div><div className="metric"><RadioTower /><span>Offline<strong>{metrics.openEpochs}</strong></span></div><div className="metric"><AlertOctagon /><span>Конфликты<strong>{metrics.conflicts}</strong></span></div><div className="metric"><ShieldAlert /><span>Карантин<strong>{metrics.quarantined}</strong></span></div></section>
    {failure ? <p className="federation-error" role="alert">{errorText(failure)}</p> : null}
    {loading ? <div className="state"><RefreshCw className="spin" /><span>Загрузка</span></div> : null}
    {!loading && section === "nodes" ? <NodesSection nodes={nodes.data ?? []} contracts={contracts.data ?? []} exposures={exposures.data ?? []} principal={principal} run={run} /> : null}
    {!loading && section === "onboarding" ? <OnboardingSection principal={principal} applications={applications.data ?? []} responsibilities={responsibilities.data ?? []} challenges={challenges.data ?? []} users={users.data ?? []} roles={roles.data ?? []} run={run} /> : null}
    {!loading && section === "liability" ? <LiabilitySection data={{ nodes: nodes.data ?? [], applications: applications.data ?? [], contracts: contracts.data ?? [], limits: limits.data ?? [], bonds: bonds.data ?? [], exposures: exposures.data ?? [] }} principal={principal} run={run} /> : null}
    {!loading && section === "offline" ? <OfflineSection nodes={nodes.data ?? []} epochs={epochs.data ?? []} forms={forms.data ?? []} principal={principal} run={run} /> : null}
    {!loading && section === "sync" ? <SyncSection nodes={nodes.data ?? []} epochs={epochs.data ?? []} packages={packages.data ?? []} conflicts={conflicts.data ?? []} receipts={receipts.data ?? []} principal={principal} run={run} /> : null}
    {!loading && section === "security" ? <SecuritySection nodes={nodes.data ?? []} incidents={incidents.data ?? []} rotations={rotations.data ?? []} principal={principal} run={run} /> : null}
  </div>;
}
