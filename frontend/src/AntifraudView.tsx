import {
  AlertTriangle,
  BadgeCheck,
  Ban,
  CheckCircle2,
  Eye,
  FileSearch,
  Play,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  X,
} from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  beginAntifraudReview,
  decideAntifraudSignal,
  getAntifraudOverview,
  getAntifraudRules,
  getAntifraudScans,
  getAntifraudSignals,
  runAntifraudScan,
  type AntifraudSignal,
} from "./api/antifraud";
import { getCooperatives, type Principal, type RoleCode } from "./api/admin";
import { uploadEvidence } from "./api/inventory";
import { userErrorMessage } from "./shared/api-error";
import { formatLocalDateTime } from "./shared/date-time";
import "./antifraud.css";

const evidenceAccept = "application/pdf,image/jpeg,image/png,image/webp,text/plain";

function hasRole(principal: Principal, ...roles: RoleCode[]): boolean {
  return principal.roles.some((item) => roles.includes(item.role));
}

function shortId(value: string): string {
  return value.slice(0, 8);
}

function Status({ value }: { value: string }) {
  const { t } = useTranslation();
  const kind = value === "CLEARED"
    ? "good"
    : ["CONFIRMED", "CRITICAL"].includes(value)
      ? "bad"
      : "warn";
  return (
    <span className={`status ${kind}`}>
      {t(`antifraud.value.${value.toLowerCase()}`)}
    </span>
  );
}

function SignalFacts({ signal }: { signal: AntifraudSignal }) {
  const { t } = useTranslation();
  return (
    <dl className="antifraud-facts">
      {Object.entries(signal.observed_data).map(([key, value]) => (
        <div key={key}>
          <dt>{t(`antifraud.fact.${key}`)}</dt>
          <dd data-i18n-ignore>{String(value)}</dd>
        </div>
      ))}
      {Object.entries(signal.threshold_data).map(([key, value]) => (
        <div key={`threshold-${key}`}>
          <dt>{t(`antifraud.threshold.${key}`)}</dt>
          <dd data-i18n-ignore>{String(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

export default function AntifraudView({ principal }: { principal: Principal }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const cooperatives = useQuery({
    queryKey: ["cooperatives"],
    queryFn: getCooperatives,
  });
  const rules = useQuery({
    queryKey: ["antifraud", "rules"],
    queryFn: getAntifraudRules,
  });
  const scopedCooperative = principal.roles.find(
    (item) =>
      ["RISK_ADMIN", "AUDITOR", "SECURITY_ADMIN"].includes(item.role) &&
      item.cooperative_id,
  )?.cooperative_id;
  const [cooperativeId, setCooperativeId] = useState(scopedCooperative ?? "");
  const [statusFilter, setStatusFilter] = useState("");
  const [severityFilter, setSeverityFilter] = useState("");
  const [lookbackHours, setLookbackHours] = useState(168);
  const [selectedId, setSelectedId] = useState("");
  const [rationale, setRationale] = useState("");
  const [file, setFile] = useState<File | null>(null);

  useEffect(() => {
    if (!cooperativeId && cooperatives.data?.[0]?.id) {
      setCooperativeId(cooperatives.data[0].id);
    }
  }, [cooperativeId, cooperatives.data]);

  const overview = useQuery({
    queryKey: ["antifraud", "overview", cooperativeId],
    queryFn: () => getAntifraudOverview(cooperativeId || undefined),
  });
  const scans = useQuery({
    queryKey: ["antifraud", "scans", cooperativeId],
    queryFn: () => getAntifraudScans(cooperativeId || undefined),
  });
  const signals = useQuery({
    queryKey: ["antifraud", "signals", cooperativeId, statusFilter, severityFilter],
    queryFn: () =>
      getAntifraudSignals({
        cooperativeId: cooperativeId || undefined,
        status: statusFilter || undefined,
        severity: severityFilter || undefined,
      }),
  });
  const selected = useMemo(
    () => signals.data?.find((item) => item.id === selectedId) ?? null,
    [selectedId, signals.data],
  );

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["antifraud"] });
  const scan = useMutation({
    mutationFn: () => runAntifraudScan(cooperativeId, lookbackHours),
    onSuccess: refresh,
  });
  const review = useMutation({
    mutationFn: (signal: AntifraudSignal) => beginAntifraudReview(signal),
    onSuccess: async (_result, signal) => {
      setSelectedId(signal.id);
      await refresh();
    },
  });
  const decide = useMutation({
    mutationFn: async (decision: "CLEARED" | "CONFIRMED") => {
      if (!selected || !file) throw new Error("EVIDENCE_REQUIRED");
      const evidenceId = await uploadEvidence(
        selected.cooperative_id,
        file,
        "ANTIFRAUD_REVIEW",
      );
      return decideAntifraudSignal(selected, {
        decision,
        rationale,
        evidence_ids: [evidenceId],
      });
    },
    onSuccess: async () => {
      setSelectedId("");
      setRationale("");
      setFile(null);
      await refresh();
    },
  });

  const pending = [cooperatives, rules, overview, scans, signals].some((item) => item.isPending);
  const failed = [cooperatives, rules, overview, scans, signals].find((item) => item.isError);
  if (pending) {
    return (
      <div className="view-stack">
        <div className="state" role="status">
          <RefreshCw className="spin" size={24} />
          <span>{t("antifraud.loading")}</span>
        </div>
      </div>
    );
  }
  if (failed) {
    return (
      <div className="view-stack">
        <div className="state error" role="alert">
          <AlertTriangle size={22} />
          <strong>{userErrorMessage(failed.error)}</strong>
        </div>
      </div>
    );
  }

  const data = signals.data ?? [];
  const canScan = hasRole(principal, "RISK_ADMIN");
  const canReview = hasRole(principal, "AUDITOR");
  const latestScan = scans.data?.[0] ?? null;
  const reviewOwned =
    selected?.status === "IN_REVIEW" &&
    selected.reviewer_member_id === principal.member_id;

  function choose(signal: AntifraudSignal) {
    setSelectedId(signal.id);
    setRationale(signal.decision_rationale ?? "");
    setFile(null);
  }


  return (
    <div className="view-stack antifraud-view">
      <header className="view-header">
        <div>
          <span className="eyebrow">{t("antifraud.eyebrow")}</span>
          <h1>{t("antifraud.title")}</h1>
          <p>{t("antifraud.subtitle")}</p>
        </div>
      </header>

      <section className="antifraud-notice" role="note">
        <ShieldAlert size={20} />
        <div>
          <strong>{t("antifraud.notice.title")}</strong>
          <span>{t("antifraud.notice.body")}</span>
        </div>
      </section>

      {rules.data ? (
        <section className="antifraud-catalog" aria-labelledby="antifraud-catalog-title">
          <div className="antifraud-catalog-heading">
            <ShieldCheck size={20} />
            <div>
              <h2 id="antifraud-catalog-title">{t("antifraud.catalog.title")}</h2>
              <p>
                {t("antifraud.catalog.coverage", {
                  requirements: rules.data.requirement_count,
                  rules: rules.data.rule_count,
                })}
              </p>
            </div>
            <dl>
              <div>
                <dt>{t("antifraud.catalog.version")}</dt>
                <dd data-i18n-ignore>{rules.data.algorithm_version}</dd>
              </div>
              <div>
                <dt>{t("antifraud.catalog.manifest")}</dt>
                <dd data-i18n-ignore>{shortId(rules.data.manifest_hash.slice(7))}</dd>
              </div>
            </dl>
          </div>
          <div className="antifraud-calibration-warning" role="status">
            <AlertTriangle size={17} />
            <span>{t("antifraud.catalog.pilot_pending")}</span>
          </div>
          <details className="antifraud-rule-list">
            <summary>{t("antifraud.catalog.show_rules")}</summary>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t("antifraud.catalog.column.risk")}</th>
                    <th>{t("antifraud.catalog.column.effect")}</th>
                    <th>{t("antifraud.catalog.column.tests")}</th>
                  </tr>
                </thead>
                <tbody>
                  {rules.data.rules.map((rule) => (
                    <tr key={rule.code}>
                      <td>{t(rule.requirement_key)}</td>
                      <td>{t(`antifraud.action_value.${rule.action.toLowerCase()}`)}</td>
                      <td>{rule.engineering_case_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </section>
      ) : null}

      <section className="metric-grid antifraud-metrics" aria-label={t("antifraud.summary")}>
        <article className="metric">
          <FileSearch size={19} />
          <span>{t("antifraud.metric.signals")}</span>
          <strong>{overview.data?.signal_count ?? 0}</strong>
        </article>
        <article className="metric">
          <Ban size={19} />
          <span>{t("antifraud.metric.holds")}</span>
          <strong>{overview.data?.active_hold_count ?? 0}</strong>
        </article>
        <article className="metric">
          <Eye size={19} />
          <span>{t("antifraud.metric.in_review")}</span>
          <strong>{overview.data?.by_status.IN_REVIEW ?? 0}</strong>
        </article>
        <article className="metric">
          <CheckCircle2 size={19} />
          <span>{t("antifraud.metric.cleared")}</span>
          <strong>{overview.data?.by_status.CLEARED ?? 0}</strong>
        </article>
      </section>

      <section className="action-band antifraud-controls">
        <div className="antifraud-filter-row">
          <label>
            {t("antifraud.field.cooperative")}
            <select
              value={cooperativeId}
              onChange={(event) => setCooperativeId(event.target.value)}
            >
              {cooperatives.data?.map((item) => (
                <option key={item.id} value={item.id} data-i18n-ignore>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            {t("antifraud.field.status")}
            <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
              <option value="">{t("antifraud.filter.all")}</option>
              {["OPEN", "IN_REVIEW", "CLEARED", "CONFIRMED"].map((value) => (
                <option value={value} key={value}>
                  {t(`antifraud.value.${value.toLowerCase()}`)}
                </option>
              ))}
            </select>
          </label>
          <label>
            {t("antifraud.field.severity")}
            <select
              value={severityFilter}
              onChange={(event) => setSeverityFilter(event.target.value)}
            >
              <option value="">{t("antifraud.filter.all")}</option>
              {["LOW", "MEDIUM", "HIGH", "CRITICAL"].map((value) => (
                <option value={value} key={value}>
                  {t(`antifraud.value.${value.toLowerCase()}`)}
                </option>
              ))}
            </select>
          </label>
          {canScan ? (
            <>
              <label>
                {t("antifraud.field.period")}
                <select
                  value={lookbackHours}
                  onChange={(event) => setLookbackHours(Number(event.target.value))}
                >
                  <option value={24}>{t("antifraud.period.day")}</option>
                  <option value={168}>{t("antifraud.period.week")}</option>
                  <option value={720}>{t("antifraud.period.month")}</option>
                </select>
              </label>
              <button
                type="button"
                className="primary-button"
                disabled={!cooperativeId || scan.isPending}
                onClick={() => scan.mutate()}
              >
                {scan.isPending ? <RefreshCw className="spin" size={16} /> : <Play size={16} />}
                <span>{t("antifraud.action.scan")}</span>
              </button>
            </>
          ) : null}
        </div>
        {latestScan ? (
          <small>
            {t("antifraud.latest_scan")}: {formatLocalDateTime(latestScan.created_at)}
            {" · "}
            {t("antifraud.metric.found")}: {latestScan.finding_count}
          </small>
        ) : null}
        {scan.isError ? <p className="form-error">{userErrorMessage(scan.error)}</p> : null}
      </section>

      <section className="panel">
        <div className="panel-heading">
          <h2>{t("antifraud.signals.title")}</h2>
          <span>{data.length}</span>
        </div>
        <div className="table-wrap antifraud-table-wrap">
          <table className="antifraud-table">
            <thead>
              <tr>
                <th>{t("antifraud.column.reason")}</th>
                <th>{t("antifraud.column.subject")}</th>
                <th>{t("antifraud.column.severity")}</th>
                <th>{t("antifraud.column.effect")}</th>
                <th>{t("antifraud.column.status")}</th>
                <th>{t("antifraud.column.seen")}</th>
                <th>{t("antifraud.column.action")}</th>
              </tr>
            </thead>
            <tbody>
              {data.map((signal) => (
                <tr key={signal.id}>
                  <td>
                    <strong>{t(signal.reason_key)}</strong>
                    <small>
                      {t("antifraud.occurrences", { count: signal.occurrence_count })}
                    </small>
                  </td>
                  <td>
                    {t(`antifraud.subject.${signal.subject_type.toLowerCase()}`)}
                    <small data-i18n-ignore>{shortId(signal.subject_id)}</small>
                  </td>
                  <td><Status value={signal.severity} /></td>
                  <td>{t(`antifraud.action_value.${signal.automation_action.toLowerCase()}`)}</td>
                  <td><Status value={signal.status} /></td>
                  <td>{formatLocalDateTime(signal.last_seen_at)}</td>
                  <td>
                    <button
                      type="button"
                      className="compact-command"
                      onClick={() => choose(signal)}
                    >
                      <Eye size={14} />
                      <span>{t("antifraud.action.details")}</span>
                    </button>
                  </td>
                </tr>
              ))}
              {!data.length ? (
                <tr>
                  <td colSpan={7} className="empty-cell">{t("antifraud.signals.empty")}</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
        <div className="antifraud-mobile-list">
          {data.map((signal) => (
            <article key={signal.id}>
              <div>
                <strong>{t(signal.reason_key)}</strong>
                <Status value={signal.status} />
              </div>
              <span>
                {t(`antifraud.subject.${signal.subject_type.toLowerCase()}`)}
                {" · "}
                {t(`antifraud.action_value.${signal.automation_action.toLowerCase()}`)}
              </span>
              <small>{formatLocalDateTime(signal.last_seen_at)}</small>
              <button type="button" className="compact-command" onClick={() => choose(signal)}>
                <Eye size={14} />
                <span>{t("antifraud.action.details")}</span>
              </button>
            </article>
          ))}
        </div>
      </section>

      {selected ? (
        <section className="panel antifraud-detail" aria-labelledby="antifraud-detail-title">
          <div className="panel-heading">
            <h2 id="antifraud-detail-title">{t(selected.reason_key)}</h2>
            <button
              type="button"
              className="icon-button"
              title={t("antifraud.action.close")}
              onClick={() => setSelectedId("")}
            >
              <X size={16} aria-hidden="true" />
            </button>
          </div>
          <div className="antifraud-detail-body">
            <div className="antifraud-detail-summary">
              <Status value={selected.severity} />
              <Status value={selected.status} />
              <span>{t(`antifraud.action_value.${selected.automation_action.toLowerCase()}`)}</span>
              <small data-i18n-ignore>{selected.id}</small>
            </div>
            <SignalFacts signal={selected} />
            {selected.decision_rationale ? (
              <div className="antifraud-rationale">
                <strong>{t("antifraud.decision.rationale")}</strong>
                <p data-i18n-ignore>{selected.decision_rationale}</p>
              </div>
            ) : null}
          </div>

          {canReview && selected.status === "OPEN" ? (
            <div className="antifraud-review-start">
              <p>{t("antifraud.review.independence")}</p>
              <button
                type="button"
                className="primary-button"
                disabled={review.isPending}
                onClick={() => review.mutate(selected)}
              >
                <FileSearch size={16} />
                <span>{t("antifraud.action.take_review")}</span>
              </button>
            </div>
          ) : null}

          {canReview && reviewOwned ? (
            <form className="antifraud-decision-form">
              <label>
                {t("antifraud.decision.rationale")}
                <textarea
                  value={rationale}
                  onChange={(event) => setRationale(event.target.value)}
                  minLength={2}
                  maxLength={8000}
                  required
                />
              </label>
              <label>
                {t("antifraud.decision.evidence")}
                <input
                  type="file"
                  accept={evidenceAccept}
                  onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                  required
                />
              </label>
              <div className="antifraud-decision-actions">
                <button
                  type="button"
                  className="secondary-button"
                  disabled={decide.isPending || !file || rationale.trim().length < 2}
                  onClick={() => decide.mutate("CLEARED")}
                >
                  <BadgeCheck size={16} />
                  <span>{t("antifraud.action.clear")}</span>
                </button>
                <button
                  type="button"
                  className="primary-button danger-command"
                  disabled={decide.isPending || !file || rationale.trim().length < 2}
                  onClick={() => decide.mutate("CONFIRMED")}
                >
                  <ShieldAlert size={16} />
                  <span>{t("antifraud.action.confirm")}</span>
                </button>
              </div>
            </form>
          ) : null}
          {review.isError || decide.isError ? (
            <p className="form-error">
              {userErrorMessage(review.error ?? decide.error)}
            </p>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}
