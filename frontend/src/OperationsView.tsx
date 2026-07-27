import { useMutation, useQuery } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  BatteryCharging,
  BadgeCheck,
  Clock3,
  DatabaseBackup,
  Download,
  FileCheck2,
  Gauge,
  HardDrive,
  KeyRound,
  LockKeyhole,
  Network,
  RefreshCw,
  ScrollText,
  ShieldCheck,
} from "lucide-react";
import type { TFunction } from "i18next";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import {
  downloadDiagnosticBundle,
  getDiagnosticPlan,
  getHostReadiness,
  getOperationalSnapshot,
  type HostCheck,
} from "./api/operations";
import { formatLocalDateTime } from "./shared/date-time";

type Row = readonly [labelKey: string, value: number, detailKey: string];

const checkLabels: Record<HostCheck["name"], string> = {
  storage: "operations.host.storage",
  clock: "operations.host.clock",
  backup: "operations.host.backup",
  certificates: "operations.host.certificates",
  ups: "operations.host.ups",
};

const checkIcons = {
  storage: HardDrive,
  clock: Clock3,
  backup: DatabaseBackup,
  certificates: BadgeCheck,
  ups: BatteryCharging,
} satisfies Record<HostCheck["name"], typeof HardDrive>;

const statusLabels = {
  OK: "operations.host.status.ok",
  WARNING: "operations.host.status.warning",
  CRITICAL: "operations.host.status.critical",
  UNKNOWN: "operations.host.status.unknown",
} as const;

const checkMessages: Record<string, string> = Object.fromEntries(
  [
    "DISK_OK",
    "DISK_LOW",
    "DISK_CRITICAL",
    "HOST_DISK_LOW",
    "HOST_DISK_CRITICAL",
    "CLOCK_OK",
    "CLOCK_DRIFT",
    "CLOCK_UNSAFE",
    "CLOCK_SYNC_UNKNOWN",
    "BACKUP_OK",
    "BACKUP_AGING",
    "BACKUP_OVERDUE",
    "BACKUP_DATA_ONLY",
    "BACKUP_STATUS_MISSING",
    "BACKUP_STATUS_INVALID",
    "CERTIFICATES_OK",
    "CERTIFICATE_RENEWAL_DUE",
    "CERTIFICATE_EXPIRING",
    "CERTIFICATE_EXPIRED",
    "UPS_ONLINE",
    "UPS_ON_BATTERY",
    "UPS_LOW_BATTERY",
    "UPS_NOT_CONFIGURED",
    "UPS_UNKNOWN",
    "UPS_PROBE_MISSING",
  ].map((code) => [code, `operations.host.message.${code}`]),
);

const backupKindLabels: Record<string, string> = {
  FULL: "operations.host.backupKind.full",
  DATA_ONLY: "operations.host.backupKind.dataOnly",
};

const upsStatusLabels: Record<string, string> = {
  ONLINE: "operations.host.upsStatus.online",
  ON_BATTERY: "operations.host.upsStatus.onBattery",
  LOW_BATTERY: "operations.host.upsStatus.lowBattery",
  NOT_CONFIGURED: "operations.host.upsStatus.notConfigured",
  UNKNOWN: "operations.host.upsStatus.unknown",
};
function Health({ value }: { value: number }) {
  const { t } = useTranslation();
  return <span className={`status ${value === 0 ? "good" : "warn"}`}>{value === 0 ? t("operations.normal") : value}</span>;
}

function OperationalRows({ rows }: { rows: readonly Row[] }) {
  const { t } = useTranslation();
  return (
    <div className="rows">
      {rows.map(([labelKey, value, detailKey]) => (
        <div className="data-row" key={labelKey}>
          <strong>{t(labelKey)}</strong>
          <span>{t(detailKey)}</span>
          <Health value={value} />
        </div>
      ))}
    </div>
  );
}

function formatBytes(value: number | string | boolean | null | undefined, t: TFunction): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return t("operations.host.noData");
  const gibibytes = value / 1_073_741_824;
  return t("operations.host.gibibytes", {
    value: gibibytes.toLocaleString(undefined, { maximumFractionDigits: 1 }),
  });
}

function checkDetail(check: HostCheck, t: TFunction): string {
  if (check.name === "storage") {
    return t("operations.host.storageDetail", {
      percent: check.metrics.free_percent ?? 0,
      bytes: formatBytes(check.metrics.free_bytes, t),
    });
  }
  if (check.name === "clock") {
    return t("operations.host.clockDetail", {
      seconds: check.metrics.database_drift_seconds ?? 0,
    });
  }
  if (check.name === "backup") {
    const age = check.metrics.age_hours;
    const kind = check.metrics.backup_kind;
    const kindKey = typeof kind === "string" ? backupKindLabels[kind] : undefined;
    return typeof age === "number"
      ? t("operations.host.backupDetail", {
          kind: kindKey ? t(kindKey) : t("operations.host.backup"),
          hours: age,
        })
      : t("operations.host.noVerifiedBackup");
  }
  if (check.name === "certificates") {
    const days = check.metrics.nearest_expiry_days;
    return typeof days === "number"
      ? t("operations.host.certificateDetail", { days })
      : t("operations.host.noExternalCertificates");
  }
  const ups = check.metrics.ups_status;
  const key = typeof ups === "string" ? upsStatusLabels[ups] : undefined;
  return key ? t(key) : t("operations.host.upsStatus.unknown");
}
function HostReadinessPanel() {
  const { t } = useTranslation();
  const readiness = useQuery({
    queryKey: ["host-readiness"],
    queryFn: getHostReadiness,
    refetchInterval: 30_000,
  });
  if (readiness.isPending) {
    return <section className="panel host-readiness"><div className="state" role="status"><RefreshCw className="spin" size={20} /><span>{t("operations.host.checking")}</span></div></section>;
  }
  if (readiness.isError || !readiness.data) {
    return <section className="panel host-readiness"><div className="state error" role="alert"><AlertTriangle size={20} /><strong>{t("operations.host.unavailable")}</strong></div></section>;
  }
  return (
    <section className="panel host-readiness">
      <div className="panel-heading">
        <div><h2>{t("operations.host.title")}</h2><span>{formatLocalDateTime(readiness.data.generated_at)}</span></div>
        <span className={`readiness-summary ${readiness.data.status.toLowerCase()}`}>
          {readiness.data.status === "OPERATIONAL" ? t("operations.host.ready") : readiness.data.status === "CRITICAL" ? t("operations.host.critical") : t("operations.host.attention")}
        </span>
      </div>
      <div className="host-check-grid">
        {readiness.data.checks.map((check) => {
          const Icon = checkIcons[check.name];
          return (
            <article className={`host-check ${check.status.toLowerCase()}`} key={check.name}>
              <Icon size={19} />
              <div>
                <strong>{t(checkLabels[check.name])}</strong>
                <span>{t(checkMessages[check.code] ?? "operations.host.manualCheck")}</span>
                <small>{checkDetail(check, t)}</small>
              </div>
              <span className={`status ${check.status === "OK" ? "good" : check.status === "CRITICAL" ? "danger" : "warn"}`}>{t(statusLabels[check.status])}</span>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function DiagnosticBundlePanel() {
  const { t } = useTranslation();
  const [passphrase, setPassphrase] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const plan = useQuery({ queryKey: ["diagnostic-plan"], queryFn: getDiagnosticPlan });
  const download = useMutation({
    mutationFn: downloadDiagnosticBundle,
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      const timestamp = new Date().toISOString().replace(/\D/gu, "").slice(0, 14);
      anchor.download = `cooperative-clearing-diagnostic-${timestamp}Z.ccdiag`;
      anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
      setPassphrase("");
      setConfirmation("");
    },
  });
  const mismatch = confirmation.length > 0 && passphrase !== confirmation;
  const ready = passphrase.length >= 16 && passphrase === confirmation && !download.isPending;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (ready) download.mutate(passphrase);
  }

  return (
    <section className="panel diagnostic-panel">
      <div className="panel-heading"><div><h2>{t("operations.diagnostic.title")}</h2><span data-i18n-ignore>{plan.data?.encryption ?? "AES-256-GCM"}</span></div><LockKeyhole size={18} /></div>
      <div className="diagnostic-layout">
        <div className="diagnostic-inventory">
          {(plan.data?.included ?? []).map((name) => <span key={name}><FileCheck2 size={15} /><code data-i18n-ignore>{name}</code></span>)}
        </div>
        <form onSubmit={submit}>
          <label>{t("operations.diagnostic.passphrase")}<input type="password" minLength={16} maxLength={128} autoComplete="new-password" value={passphrase} onChange={(event) => setPassphrase(event.target.value)} required /></label>
          <label>{t("operations.diagnostic.confirmPassphrase")}<input type="password" minLength={16} maxLength={128} autoComplete="new-password" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} required /></label>
          <button className="primary-button" disabled={!ready}><Download size={17} />{t("operations.diagnostic.download")}</button>
        </form>
      </div>
      {mismatch ? <p className="form-error" role="alert">{t("operations.diagnostic.mismatch")}</p> : null}
      {download.isError ? <p className="form-error" role="alert">{t("operations.diagnostic.failed")}</p> : null}
    </section>
  );
}

export default function OperationsView() {
  const { t } = useTranslation();
  const snapshot = useQuery({
    queryKey: ["operational-snapshot"],
    queryFn: getOperationalSnapshot,
    refetchInterval: 30_000,
  });

  if (snapshot.isPending) {
    return <div className="state" role="status"><RefreshCw className="spin" size={24} /><span>{t("operations.loading")}</span></div>;
  }
  if (snapshot.isError || !snapshot.data) {
    return <div className="state error" role="alert"><Activity size={24} /><strong>{t("operations.snapshotUnavailable")}</strong></div>;
  }

  const data = snapshot.data;
  const delivery: readonly Row[] = [
    ["operations.delivery.pending.label", data.outbox_pending, "operations.delivery.pending.detail"],
    ["operations.delivery.quarantined.label", data.outbox_quarantined, "operations.delivery.quarantined.detail"],
    ["operations.delivery.offline.label", data.open_offline_epochs, "operations.delivery.offline.detail"],
  ];
  const federation: readonly Row[] = [
    ["operations.federation.syncConflicts.label", data.open_sync_conflicts, "operations.federation.syncConflicts.detail"],
    ["operations.federation.nodeIncidents.label", data.open_node_incidents, "operations.federation.nodeIncidents.detail"],
    ["operations.federation.keyRotations.label", data.pending_key_rotations, "operations.federation.keyRotations.detail"],
    ["operations.federation.activePrepares.label", data.active_federated_prepares, "operations.federation.activePrepares.detail"],
    ["operations.federation.pendingApplies.label", data.pending_federated_applies, "operations.federation.pendingApplies.detail"],
    ["operations.federation.expiredPrepares.label", data.expired_federated_prepares, "operations.federation.expiredPrepares.detail"],
    ["operations.federation.paperForms.label", data.issued_federation_forms, "operations.federation.paperForms.detail"],
  ];
  const governance: readonly Row[] = [
    ["operations.governance.trustCases.label", data.open_trust_cases, "operations.governance.trustCases.detail"],
    ["operations.governance.appeals.label", data.submitted_appeals, "operations.governance.appeals.detail"],
    ["operations.governance.crisisMandates.label", data.active_crisis_mandates, "operations.governance.crisisMandates.detail"],
    ["operations.governance.crisisForms.label", data.issued_crisis_forms, "operations.governance.crisisForms.detail"],
  ];
  return (
    <div className="view-stack operations-view">
      <header className="view-header">
        <div>
          <span className="eyebrow">{t("operations.header.eyebrow")}</span>
          <h1>{t("operations.header.title")}</h1>
          <p>{t("operations.header.generatedAt", { date: formatLocalDateTime(data.generated_at) })}</p>
        </div>
        <span className="release">{t("operations.header.schema")}<br /><code data-i18n-ignore>{data.schema_revision}</code></span>
      </header>
      <HostReadinessPanel />
      <section className="metric-grid" aria-label={t("operations.summary.aria")}>
        <article className="metric"><ScrollText size={18} /><span>{t("operations.summary.events")}</span><strong>{data.signed_events}</strong></article>
        <article className="metric"><Gauge size={18} /><span>{t("operations.summary.outbox")}</span><strong>{data.outbox_pending}</strong></article>
        <article className="metric"><KeyRound size={18} /><span>{t("operations.summary.sessions")}</span><strong>{data.active_sessions}</strong></article>
        <article className="metric"><Network size={18} /><span>{t("operations.summary.conflicts")}</span><strong>{data.open_sync_conflicts}</strong></article>
        <article className="metric"><AlertTriangle size={18} /><span>{t("operations.summary.incidents")}</span><strong>{data.open_node_incidents}</strong></article>
        <article className="metric"><FileCheck2 size={18} /><span>{t("operations.summary.forms")}</span><strong>{data.issued_federation_forms + data.issued_crisis_forms}</strong></article>
      </section>
      <section className="panel"><div className="panel-heading"><h2>{t("operations.section.delivery")}</h2><ShieldCheck size={17} /></div><OperationalRows rows={delivery} /></section>
      <section className="panel"><div className="panel-heading"><h2>{t("operations.section.federation")}</h2><Network size={17} /></div><OperationalRows rows={federation} /></section>
      <section className="panel"><div className="panel-heading"><h2>{t("operations.section.governance")}</h2><AlertTriangle size={17} /></div><OperationalRows rows={governance} /></section>
      <DiagnosticBundlePanel />
    </div>
  );
}
