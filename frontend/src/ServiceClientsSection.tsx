import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Ban,
  Check,
  CircleOff,
  Copy,
  KeyRound,
  Pencil,
  Plus,
  RefreshCw,
  RotateCw,
  ShieldCheck,
  X,
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  type Cooperative,
  type Principal,
  type ServiceClient,
  type ServiceClientConfig,
  type ServiceClientDecision,
  type ServiceClientOperation,
  type ServiceClientRequest,
  type ServiceScope,
  decideServiceClientRequest,
  getSecurityState,
  getServiceClientRequests,
  getServiceClients,
  requestServiceClientChange,
  revokeServiceClient,
  suspendServiceClient,
  verifyTotpStepUp,
} from "./api/admin";
import { userErrorMessage } from "./shared/api-error";
import { formatLocalDateTime } from "./shared/date-time";

const SCOPES: ServiceScope[] = ["catalog:read", "clearing:accounting:read"];
const SCOPE_MESSAGE_KEYS: Record<ServiceScope, string> = {
  "catalog:read": "admin.integrations.scope.catalogRead",
  "clearing:accounting:read": "admin.integrations.scope.clearingAccountingRead",
};

type PrivilegedAction =
  | { kind: "approve"; request: ServiceClientRequest }
  | { kind: "reject"; request: ServiceClientRequest }
  | { kind: "suspend"; client: ServiceClient }
  | { kind: "revoke"; client: ServiceClient };

type ServiceCommandResult = { event_id: string; object_id: string; replayed: boolean };

function permanentRole(principal: Principal, roles: string[], cooperativeId?: string): boolean {
  return principal.roles.some((grant) =>
    grant.source !== "BREAK_GLASS"
    && roles.includes(grant.role)
    && (grant.cooperative_id === null || grant.cooperative_id === cooperativeId),
  );
}

function localInputValue(value: string | Date): string {
  const date = value instanceof Date ? value : new Date(value);
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function initialExpiry(): string {
  const value = new Date();
  value.setUTCDate(value.getUTCDate() + 180);
  value.setUTCHours(12, 0, 0, 0);
  return localInputValue(value);
}

function splitNetworks(value: string): string[] {
  return value
    .split(/[\n,;]/u)
    .map((item) => item.trim())
    .filter(Boolean);
}

function shortId(value: string): string {
  return value.slice(0, 8);
}

function Status({ value }: { value: string }) {
  const { t } = useTranslation();
  return <span className={`status status-${value.toLowerCase()}`}>{t(`admin.integrations.status.${value}`, { defaultValue: value })}</span>;
}

function ScopeList({ scopes }: { scopes: ServiceScope[] }) {
  const { t } = useTranslation();
  return <div className="service-scope-list">{scopes.map((scope) => <span key={scope}>{t(SCOPE_MESSAGE_KEYS[scope])}</span>)}</div>;
}

export default function ServiceClientsSection({
  principal,
  cooperatives,
}: {
  principal: Principal;
  cooperatives: Cooperative[];
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const permanentManager = principal.roles.some((grant) =>
    grant.source !== "BREAK_GLASS"
    && ["COOPERATIVE_ADMIN", "SECURITY_ADMIN"].includes(grant.role),
  );
  const permanentSecurity = principal.roles.some((grant) =>
    grant.source !== "BREAK_GLASS" && grant.role === "SECURITY_ADMIN",
  );
  const manageableCooperatives = useMemo(
    () => cooperatives.filter((cooperative) =>
      cooperative.status === "ACTIVE"
      && permanentRole(principal, ["COOPERATIVE_ADMIN", "SECURITY_ADMIN"], cooperative.id),
    ),
    [cooperatives, principal],
  );

  const clients = useQuery({ queryKey: ["service-clients"], queryFn: getServiceClients });
  const requests = useQuery({ queryKey: ["service-client-requests"], queryFn: getServiceClientRequests });
  const security = useQuery({
    queryKey: ["security-state"],
    queryFn: getSecurityState,
    enabled: permanentSecurity,
  });

  const [editing, setEditing] = useState<ServiceClient | null>(null);
  const [ownerId, setOwnerId] = useState(manageableCooperatives[0]?.id ?? "");
  const [displayName, setDisplayName] = useState("");
  const [contactName, setContactName] = useState("");
  const [contactEmail, setContactEmail] = useState("");
  const [scopes, setScopes] = useState<ServiceScope[]>(["catalog:read"]);
  const [networks, setNetworks] = useState("");
  const [rateLimit, setRateLimit] = useState("60");
  const [expiresAt, setExpiresAt] = useState(initialExpiry);
  const [message, setMessage] = useState("");
  const [privilegedAction, setPrivilegedAction] = useState<PrivilegedAction | null>(null);
  const [totpCode, setTotpCode] = useState("");
  const [credential, setCredential] = useState<ServiceClientDecision | null>(null);
  const [copied, setCopied] = useState<"id" | "secret" | null>(null);

  useEffect(() => {
    if (!ownerId && manageableCooperatives[0]) setOwnerId(manageableCooperatives[0].id);
  }, [manageableCooperatives, ownerId]);

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["service-clients"] }),
      queryClient.invalidateQueries({ queryKey: ["service-client-requests"] }),
      queryClient.invalidateQueries({ queryKey: ["security-state"] }),
    ]);
  }

  function resetForm() {
    setEditing(null);
    setOwnerId(manageableCooperatives[0]?.id ?? "");
    setDisplayName("");
    setContactName("");
    setContactEmail("");
    setScopes(["catalog:read"]);
    setNetworks("");
    setRateLimit("60");
    setExpiresAt(initialExpiry());
  }

  const change = useMutation({
    mutationFn: (payload: Parameters<typeof requestServiceClientChange>[0]) => requestServiceClientChange(payload),
    onSuccess: async () => {
      setMessage(t("admin.integrations.requestSent"));
      resetForm();
      await refresh();
    },
  });

  const privileged = useMutation<ServiceClientDecision | ServiceCommandResult>({
    mutationFn: async (): Promise<ServiceClientDecision | ServiceCommandResult> => {
      if (!privilegedAction) throw new Error("missing privileged action");
      if (!security.data?.step_up_active) await verifyTotpStepUp(totpCode);
      const action = privilegedAction;
      if (action.kind === "approve" || action.kind === "reject") {
        return decideServiceClientRequest(
          action.request,
          action.kind === "approve",
          action.kind === "approve" ? "INDEPENDENT_REVIEW" : "SECURITY_REJECTED",
        );
      }
      if (action.kind === "suspend") {
        return suspendServiceClient(action.client, "SECURITY_SUSPENDED");
      }
      return revokeServiceClient(action.client, "SECURITY_REVOKED");
    },
    onSuccess: async (result) => {
      if ("credential_secret" in result && result.credential_secret) setCredential(result);
      setMessage(t("admin.integrations.actionCompleted"));
      setPrivilegedAction(null);
      setTotpCode("");
      await refresh();
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const config: ServiceClientConfig = {
      display_name: displayName,
      technical_contact_name: contactName,
      technical_contact_email: contactEmail,
      scopes,
      network_allowlist: splitNetworks(networks),
      rate_limit_per_minute: Number(rateLimit),
      expires_at: new Date(expiresAt).toISOString(),
    };
    change.mutate({
      owner_cooperative_id: ownerId,
      operation: editing ? "UPDATE" : "CREATE",
      service_client_id: editing?.id,
      config,
      expected_client_version: editing?.version,
      reason_code: editing ? "ADMIN_SETTINGS_UPDATE" : "ADMIN_INTEGRATION_REQUEST",
    });
  }

  function startEdit(client: ServiceClient) {
    setEditing(client);
    setOwnerId(client.owner_cooperative_id);
    setDisplayName(client.display_name);
    setContactName(client.technical_contact_name);
    setContactEmail(client.technical_contact_email);
    setScopes(client.scopes);
    setNetworks(client.network_allowlist.join("\n"));
    setRateLimit(String(client.rate_limit_per_minute));
    setExpiresAt(localInputValue(client.expires_at));
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function quickRequest(client: ServiceClient, operation: Extract<ServiceClientOperation, "ROTATE" | "REACTIVATE">) {
    change.mutate({
      owner_cooperative_id: client.owner_cooperative_id,
      operation,
      service_client_id: client.id,
      expected_client_version: client.version,
      reason_code: operation === "ROTATE" ? "ADMIN_SECRET_ROTATION" : "ADMIN_REACTIVATION",
    });
  }

  async function copyValue(kind: "id" | "secret", value: string) {
    await navigator.clipboard.writeText(value);
    setCopied(kind);
    window.setTimeout(() => setCopied(null), 1400);
  }

  const allClients = clients.data ?? [];
  const allRequests = requests.data ?? [];
  const pendingRequests = allRequests.filter(
    (item) => item.status === "PENDING" && new Date(item.expires_at).getTime() > Date.now(),
  );
  const activeCount = allClients.filter((item) => item.effective_status === "ACTIVE").length;
  const needsTotp = permanentSecurity && !security.data?.step_up_active;
  const canSubmitPrivileged = !needsTotp || /^[0-9]{6}$/u.test(totpCode);
  const currentError = clients.error ?? requests.error ?? security.error ?? change.error ?? privileged.error;

  if (clients.isPending || requests.isPending) {
    return <div className="state" role="status"><RefreshCw className="spin" size={22} />{t("admin.integrations.loading")}</div>;
  }
  if (clients.isError || requests.isError) {
    return <p className="form-error" role="alert">{userErrorMessage(clients.error ?? requests.error)}</p>;
  }

  return <div className="service-client-workspace">
    {permanentManager && manageableCooperatives.length ? <section className="panel service-client-editor">
      <div className="panel-heading"><div><h2>{editing ? t("admin.integrations.editTitle") : t("admin.integrations.createTitle")}</h2><small>{t("admin.integrations.createHint")}</small></div>{editing ? <button className="icon-button" type="button" title={t("common.cancel")} aria-label={t("common.cancel")} onClick={resetForm}><X size={17} /></button> : null}</div>
      <form onSubmit={submit}>
        <label>{t("admin.integrations.owner")}<select value={ownerId} onChange={(event) => setOwnerId(event.target.value)} disabled={Boolean(editing)} required><option value="">{t("admin.integrations.chooseOwner")}</option>{manageableCooperatives.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <label>{t("admin.integrations.name")}<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} minLength={2} maxLength={200} required /></label>
        <label>{t("admin.integrations.contactName")}<input value={contactName} onChange={(event) => setContactName(event.target.value)} minLength={2} maxLength={200} required /></label>
        <label>{t("admin.integrations.contactEmail")}<input type="email" value={contactEmail} onChange={(event) => setContactEmail(event.target.value)} required /></label>
        <fieldset className="service-scope-picker"><legend>{t("admin.integrations.permissions")}</legend>{SCOPES.map((scope) => <label key={scope}><input type="checkbox" checked={scopes.includes(scope)} onChange={(event) => setScopes((current) => event.target.checked ? [...current, scope] : current.filter((item) => item !== scope))} />{t(SCOPE_MESSAGE_KEYS[scope])}</label>)}</fieldset>
        <label className="service-networks">{t("admin.integrations.networks")}<textarea value={networks} onChange={(event) => setNetworks(event.target.value)} rows={2} required /><small>{t("admin.integrations.networkHint")}</small></label>
        <label>{t("admin.integrations.rateLimit")}<input type="number" min={1} max={6000} value={rateLimit} onChange={(event) => setRateLimit(event.target.value)} required /></label>
        <label>{t("admin.integrations.expiresAt")}<input type="datetime-local" value={expiresAt} onChange={(event) => setExpiresAt(event.target.value)} required /></label>
        <button className="primary-button" type="submit" disabled={change.isPending || !scopes.length || !splitNetworks(networks).length}><Plus size={17} /><span>{change.isPending ? t("admin.integrations.sending") : t("admin.integrations.sendForApproval")}</span></button>
      </form>
    </section> : null}

    <section className="service-client-summary" aria-label={t("admin.integrations.summary")}>
      <div><span>{t("admin.integrations.active")}</span><strong>{activeCount}</strong></div>
      <div><span>{t("admin.integrations.pending")}</span><strong>{pendingRequests.length}</strong></div>
      <div><span>{t("admin.integrations.suspended")}</span><strong>{allClients.filter((item) => item.effective_status === "SUSPENDED").length}</strong></div>
      <div><span>{t("admin.integrations.total")}</span><strong>{allClients.length}</strong></div>
    </section>

    {message ? <p className="form-success" role="status"><Check size={16} />{message}</p> : null}
    {currentError ? <p className="form-error" role="alert">{userErrorMessage(currentError)}</p> : null}

    <section className="panel service-request-panel">
      <div className="panel-heading"><div><h2>{t("admin.integrations.requests")}</h2><small>{t("admin.integrations.independentHint")}</small></div><span>{pendingRequests.length}</span></div>
      {pendingRequests.length ? <div className="table-wrap"><table><thead><tr><th>{t("admin.integrations.operation")}</th><th>{t("admin.integrations.integration")}</th><th>{t("admin.integrations.owner")}</th><th>{t("admin.integrations.requested")}</th><th>{t("admin.integrations.decision")}</th></tr></thead><tbody>{pendingRequests.map((item) => {
        const client = allClients.find((candidate) => candidate.id === item.service_client_id);
        const config = item.proposed_config;
        const owner = cooperatives.find((candidate) => candidate.id === item.owner_cooperative_id);
        const isOwnRequest = item.requested_by_user_id === principal.user_id;
        const canReview = permanentRole(principal, ["SECURITY_ADMIN"], item.owner_cooperative_id) && !isOwnRequest && new Date(item.expires_at).getTime() > Date.now();
        return <tr key={item.id}><td><strong>{t(`admin.integrations.operation.${item.operation}`)}</strong><small>{t("admin.integrations.validUntil", { date: formatLocalDateTime(item.expires_at) })}</small></td><td><strong data-i18n-ignore="true">{config?.display_name ?? client?.display_name ?? shortId(item.id)}</strong>{config?.scopes ? <ScopeList scopes={config.scopes} /> : null}</td><td>{owner?.name ?? shortId(item.owner_cooperative_id)}</td><td><code>{isOwnRequest ? t("admin.integrations.you") : shortId(item.requested_by_user_id)}</code><small>{formatLocalDateTime(item.created_at)}</small></td><td>{canReview ? <div className="service-decision-actions"><button className="compact-command approve" onClick={() => setPrivilegedAction({ kind: "approve", request: item })}><Check size={15} />{t("admin.integrations.approve")}</button><button className="icon-button danger" title={t("admin.integrations.reject")} aria-label={t("admin.integrations.reject")} onClick={() => setPrivilegedAction({ kind: "reject", request: item })}><X size={16} /></button></div> : <span className="independent-note"><AlertTriangle size={14} />{isOwnRequest ? t("admin.integrations.anotherReviewer") : t("admin.integrations.waitingReviewer")}</span>}</td></tr>;
      })}</tbody></table></div> : <div className="empty-state">{t("admin.integrations.noRequests")}</div>}
    </section>

    <section className="panel service-client-list">
      <div className="panel-heading"><div><h2>{t("admin.integrations.register")}</h2><small>{t("admin.integrations.registerHint")}</small></div><span>{allClients.length}</span></div>
      {allClients.length ? <div className="table-wrap"><table><thead><tr><th>{t("admin.integrations.integration")}</th><th>{t("admin.integrations.owner")}</th><th>{t("admin.integrations.permissions")}</th><th>{t("admin.integrations.networkAccess")}</th><th>{t("admin.integrations.validity")}</th><th>{t("admin.integrations.actions")}</th></tr></thead><tbody>{allClients.map((item) => {
        const canManageClient = permanentRole(principal, ["COOPERATIVE_ADMIN", "SECURITY_ADMIN"], item.owner_cooperative_id);
        const canProtectClient = permanentRole(principal, ["SECURITY_ADMIN"], item.owner_cooperative_id);
        return <tr key={item.id}><td><strong data-i18n-ignore="true">{item.display_name}</strong><code>{item.client_code}</code><small data-i18n-ignore="true">{item.technical_contact_name} · {item.technical_contact_email}</small></td><td>{cooperatives.find((candidate) => candidate.id === item.owner_cooperative_id)?.name ?? shortId(item.owner_cooperative_id)}</td><td><ScopeList scopes={item.scopes} /></td><td><strong>{t("admin.integrations.requestsPerMinute", { count: item.rate_limit_per_minute })}</strong><small data-i18n-ignore="true">{item.network_allowlist.join(", ")}</small></td><td><Status value={item.effective_status} /><small>{formatLocalDateTime(item.expires_at)}</small></td><td><div className="service-client-actions">{canManageClient && item.effective_status === "ACTIVE" ? <><button className="icon-button" title={t("admin.integrations.edit")} aria-label={t("admin.integrations.edit")} onClick={() => startEdit(item)}><Pencil size={15} /></button><button className="icon-button" title={t("admin.integrations.rotate")} aria-label={t("admin.integrations.rotate")} disabled={change.isPending} onClick={() => quickRequest(item, "ROTATE")}><RotateCw size={15} /></button></> : null}{canManageClient && item.effective_status === "SUSPENDED" ? <button className="compact-command" disabled={change.isPending} onClick={() => quickRequest(item, "REACTIVATE")}><ShieldCheck size={15} />{t("admin.integrations.reactivate")}</button> : null}{canProtectClient && item.effective_status === "ACTIVE" ? <button className="icon-button warning" title={t("admin.integrations.suspendAction")} aria-label={t("admin.integrations.suspendAction")} onClick={() => setPrivilegedAction({ kind: "suspend", client: item })}><CircleOff size={15} /></button> : null}{canProtectClient && item.effective_status !== "REVOKED" ? <button className="icon-button danger" title={t("admin.integrations.revokeAction")} aria-label={t("admin.integrations.revokeAction")} onClick={() => setPrivilegedAction({ kind: "revoke", client: item })}><Ban size={15} /></button> : null}{!canManageClient && !canProtectClient ? "—" : null}</div></td></tr>;
      })}</tbody></table></div> : <div className="empty-state">{t("admin.integrations.noClients")}</div>}
    </section>

    {privilegedAction ? <div className="modal-backdrop" role="presentation"><section className="service-confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="service-confirm-title"><header><div><span className="eyebrow">{t("admin.integrations.protectedAction")}</span><h2 id="service-confirm-title">{t(`admin.integrations.confirm.${privilegedAction.kind}`)}</h2></div><button className="icon-button" title={t("common.close")} aria-label={t("common.close")} onClick={() => { setPrivilegedAction(null); setTotpCode(""); }}><X size={18} /></button></header><p>{t("admin.integrations.confirmHint")}</p>{security.isPending ? <div className="state"><RefreshCw className="spin" size={17} />{t("admin.integrations.checkingSecurity")}</div> : security.data?.totp_enabled ? needsTotp ? <label>{t("admin.integrations.totpCode")}<input inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} value={totpCode} onChange={(event) => setTotpCode(event.target.value.replace(/\D/gu, ""))} autoFocus required /></label> : <p className="form-success"><ShieldCheck size={16} />{t("admin.integrations.identityConfirmed")}</p> : <p className="form-error"><KeyRound size={16} />{t("admin.integrations.enableTotp")}</p>}<div className="dialog-actions"><button className="secondary-button" type="button" onClick={() => setPrivilegedAction(null)}>{t("common.cancel")}</button><button className={`primary-button ${privilegedAction.kind === "reject" || privilegedAction.kind === "revoke" ? "danger-command" : ""}`} disabled={privileged.isPending || security.isPending || !security.data?.totp_enabled || !canSubmitPrivileged} onClick={() => privileged.mutate()}>{privileged.isPending ? <RefreshCw className="spin" size={16} /> : <Check size={16} />}{t("admin.integrations.confirmAction")}</button></div></section></div> : null}

    {credential?.client_code && credential.credential_secret ? <div className="modal-backdrop" role="presentation"><section className="service-secret-dialog" role="dialog" aria-modal="true" aria-labelledby="service-secret-title"><header><div><span className="eyebrow">{t("admin.integrations.created")}</span><h2 id="service-secret-title">{t("admin.integrations.saveCredentials")}</h2></div></header><p className="credential-warning"><AlertTriangle size={19} />{t("admin.integrations.secretOnce")}</p><label>{t("admin.integrations.clientId")}<span><code data-i18n-ignore="true">{credential.client_code}</code><button className="icon-button" title={t("admin.integrations.copyId")} aria-label={t("admin.integrations.copyId")} onClick={() => void copyValue("id", credential.client_code!)}>{copied === "id" ? <Check size={16} /> : <Copy size={16} />}</button></span></label><label>{t("admin.integrations.clientSecret")}<span><code data-i18n-ignore="true">{credential.credential_secret}</code><button className="icon-button" title={t("admin.integrations.copySecret")} aria-label={t("admin.integrations.copySecret")} onClick={() => void copyValue("secret", credential.credential_secret!)}>{copied === "secret" ? <Check size={16} /> : <Copy size={16} />}</button></span></label><small>{t("admin.integrations.credentialExpires", { date: formatLocalDateTime(credential.credential_expires_at) })}</small><button className="primary-button" onClick={() => setCredential(null)}><Check size={16} />{t("admin.integrations.saved")}</button></section></div> : null}
  </div>;
}