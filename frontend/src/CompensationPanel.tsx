import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  FileCheck2,
  HandCoins,
  Scale,
} from "lucide-react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import type { Principal } from "./api/admin";
import { uploadEvidence, type InventoryMember } from "./api/inventory";
import {
  acceptCompensation,
  authorizeCompensation,
  type CompensationTransfer,
  type ExposureCommitment,
  type LiabilityCase,
  type ShareAccount,
  voidCompensation,
} from "./api/risk";
import {
  getTrustDecisions,
  type TrustCase,
  type TrustDecision,
} from "./api/trust";
import { userErrorMessage } from "./shared/api-error";
import { formatLocalDateTime } from "./shared/date-time";
import { decimalMin, formatDecimal } from "./shared/decimal";

const evidenceAccept = "application/pdf,image/jpeg,image/png,image/webp,text/plain";

type Props = {
  principal: Principal;
  liabilities: LiabilityCase[];
  commitments: ExposureCommitment[];
  accounts: ShareAccount[];
  transfers: CompensationTransfer[];
  trustCases: TrustCase[];
  members: InventoryMember[];
  onDone: () => Promise<unknown>;
};

function memberName(members: InventoryMember[], memberId: string): string {
  return members.find((item) => item.member_id === memberId)?.display_name
    ?? memberId.slice(0, 8);
}

function exact(value: string, locale: string): string {
  return formatDecimal(value, locale, { maximumFractionDigits: 12 });
}

function hasOperatorRole(principal: Principal): boolean {
  return principal.roles.some((item) => ["RISK_ADMIN", "AUDITOR"].includes(item.role));
}

function finalDecisions(trustCase: TrustCase | undefined, decisions: TrustDecision[]) {
  if (!trustCase) return [];
  return decisions.filter((decision) => {
    if (decision.stage === "APPEAL") return decision.outcome === "AFFIRMED";
    return decision.stage === "ORIGINAL"
      && ["SUBSTANTIATED", "PARTLY_SUBSTANTIATED"].includes(decision.outcome)
      && trustCase.status === "DECIDED"
      && Boolean(trustCase.appeal_until)
      && new Date(trustCase.appeal_until ?? 0).getTime() <= Date.now();
  });
}

export default function CompensationPanel({
  principal,
  liabilities,
  commitments,
  accounts,
  transfers,
  trustCases,
  members,
  onDone,
}: Props) {
  const { t, i18n } = useTranslation();
  const locale = i18n.resolvedLanguage ?? i18n.language;
  const assessed = liabilities.filter(
    (item) => item.status === "ASSESSED" && item.assessed_loss !== null,
  );
  const [liabilityId, setLiabilityId] = useState(assessed[0]?.id ?? "");
  const linkedCases = trustCases.filter(
    (item) => item.source_type === "LIABILITY" && item.source_reference === liabilityId,
  );
  const [trustCaseOverride, setTrustCaseOverride] = useState("");
  const trustCaseId = linkedCases.some((item) => item.id === trustCaseOverride)
    ? trustCaseOverride
    : linkedCases[0]?.id ?? "";
  const selectedLiability = assessed.find((item) => item.id === liabilityId);
  const selectedTrustCase = linkedCases.find((item) => item.id === trustCaseId);
  const selectedCommitment = commitments.find(
    (item) => item.id === selectedLiability?.commitment_id,
  );
  const sourceAccount = accounts.find((item) => item.id === selectedCommitment?.account_id);
  const decisions = useQuery({
    queryKey: ["trust", "decisions", trustCaseId],
    queryFn: () => getTrustDecisions(trustCaseId),
    enabled: Boolean(trustCaseId),
  });
  const eligibleDecisions = finalDecisions(selectedTrustCase, decisions.data ?? []);
  const [decisionOverride, setDecisionOverride] = useState("");
  const decisionId = eligibleDecisions.some((item) => item.id === decisionOverride)
    ? decisionOverride
    : eligibleDecisions[0]?.id ?? "";
  const destinations = accounts.filter(
    (item) => item.status === "ACTIVE"
      && item.contour === "PRIMARY"
      && item.member_id === selectedTrustCase?.claimant_member_id
      && item.denomination === sourceAccount?.denomination,
  );
  const [destinationOverride, setDestinationOverride] = useState("");
  const destinationId = destinations.some((item) => item.id === destinationOverride)
    ? destinationOverride
    : destinations[0]?.id ?? "";
  const [amount, setAmount] = useState("");
  const [rationale, setRationale] = useState("");
  const [authorizationFile, setAuthorizationFile] = useState<File | null>(null);
  const [voidReasons, setVoidReasons] = useState<Record<string, string>>({});
  const [voidFiles, setVoidFiles] = useState<Record<string, File | null>>({});
  const canAuthorize = hasOperatorRole(principal);

  const suggestedAmount = useMemo(() => {
    const decision = eligibleDecisions.find((item) => item.id === decisionId);
    if (!selectedLiability?.assessed_loss || !decision?.established_loss) return "";
    return decimalMin(selectedLiability.assessed_loss, decision.established_loss);
  }, [decisionId, eligibleDecisions, selectedLiability]);

  const authorize = useMutation({
    mutationFn: async () => {
      if (
        !selectedLiability
        || !selectedCommitment
        || !sourceAccount
        || !trustCaseId
        || !decisionId
        || !destinationId
        || !authorizationFile
      ) {
        throw new Error("COMPENSATION_FORM_INCOMPLETE");
      }
      const destination = accounts.find((item) => item.id === destinationId);
      if (!destination) throw new Error("COMPENSATION_DESTINATION_REQUIRED");
      const evidenceId = await uploadEvidence(
        selectedLiability.cooperative_id,
        authorizationFile,
        "RISK_COMPENSATION_AUTHORIZATION",
      );
      return authorizeCompensation(selectedLiability, {
        trust_case_id: trustCaseId,
        trust_decision_id: decisionId,
        destination_account_id: destination.id,
        amount: amount || suggestedAmount,
        rationale,
        evidence_ids: [evidenceId],
        expected_source_account_version: sourceAccount.version,
        expected_destination_account_version: destination.version,
        expected_commitment_version: selectedCommitment.version,
      });
    },
    onSuccess: async () => {
      setAmount("");
      setRationale("");
      setAuthorizationFile(null);
      await onDone();
    },
  });
  const accept = useMutation({
    mutationFn: acceptCompensation,
    onSuccess: onDone,
  });
  const voidTransfer = useMutation({
    mutationFn: async (transfer: CompensationTransfer) => {
      const file = voidFiles[transfer.id];
      const reason = voidReasons[transfer.id] ?? "";
      if (!file) throw new Error("COMPENSATION_VOID_EVIDENCE_REQUIRED");
      const evidenceId = await uploadEvidence(
        transfer.cooperative_id,
        file,
        "RISK_COMPENSATION_VOID",
      );
      return voidCompensation(transfer, reason, [evidenceId]);
    },
    onSuccess: onDone,
  });

  return (
    <div className="compensation-stack">
      <section className="risk-guidance" aria-label={t("risk.compensation.safety.title")}>
        <Scale size={20} />
        <div>
          <strong>{t("risk.compensation.safety.title")}</strong>
          <p>{t("risk.compensation.safety.body")}</p>
        </div>
      </section>

      {canAuthorize ? (
        <section className="panel risk-command">
          <div className="panel-heading">
            <h2>{t("risk.compensation.authorize.title")}</h2>
            <span>{t("risk.compensation.authorize.caption")}</span>
          </div>
          <form
            className="risk-form compensation-form"
            onSubmit={(event) => {
              event.preventDefault();
              authorize.mutate();
            }}
          >
            <label className="span-two">
              {t("risk.compensation.fields.liability")}
              <select value={liabilityId} onChange={(event) => setLiabilityId(event.target.value)}>
                <option value="">{t("common.choose")}</option>
                {assessed.map((item) => (
                  <option value={item.id} key={item.id}>
                    {item.incident_reference} · {exact(item.assessed_loss ?? "0", locale)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t("risk.compensation.fields.trustCase")}
              <select
                value={trustCaseId}
                onChange={(event) => setTrustCaseOverride(event.target.value)}
                disabled={!linkedCases.length}
              >
                <option value="">{t("common.choose")}</option>
                {linkedCases.map((item) => (
                  <option value={item.id} key={item.id}>
                    {item.case_reference} · {t(`risk.compensation.caseStatus.${item.status}`)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t("risk.compensation.fields.finalDecision")}
              <select
                value={decisionId}
                onChange={(event) => setDecisionOverride(event.target.value)}
                disabled={!eligibleDecisions.length}
              >
                <option value="">{t("common.choose")}</option>
                {eligibleDecisions.map((item) => (
                  <option value={item.id} key={item.id}>
                    {t(`risk.compensation.decisionStage.${item.stage}`)} · {item.outcome}
                    {" · "}{exact(item.established_loss ?? "0", locale)}
                  </option>
                ))}
              </select>
            </label>
            <label className="span-two">
              {t("risk.compensation.fields.destination")}
              <select
                value={destinationId}
                onChange={(event) => setDestinationOverride(event.target.value)}
                disabled={!destinations.length}
              >
                <option value="">{t("common.choose")}</option>
                {destinations.map((item) => (
                  <option value={item.id} key={item.id}>
                    {memberName(members, item.member_id)} · {item.denomination}
                    {" · "}{exact(item.balance, locale)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t("risk.compensation.fields.amount")}
              <input
                inputMode="decimal"
                value={amount}
                placeholder={suggestedAmount}
                onChange={(event) => setAmount(event.target.value)}
                required={!suggestedAmount}
              />
            </label>
            <label className="span-two">
              {t("risk.compensation.fields.rationale")}
              <textarea
                value={rationale}
                onChange={(event) => setRationale(event.target.value)}
                required
              />
            </label>
            <label className="file-field">
              {t("risk.compensation.fields.evidence")}
              <input
                type="file"
                accept={evidenceAccept}
                onChange={(event) => setAuthorizationFile(event.target.files?.[0] ?? null)}
                required
              />
              {authorizationFile ? <small>{authorizationFile.name}</small> : null}
            </label>
            <button
              className="primary-button"
              disabled={
                authorize.isPending
                || !selectedLiability
                || !decisionId
                || !destinationId
                || !authorizationFile
                || rationale.trim().length < 2
              }
            >
              <FileCheck2 size={16} />
              {authorize.isPending
                ? t("risk.compensation.authorize.working")
                : t("risk.compensation.authorize.action")}
            </button>
          </form>
          {!linkedCases.length && selectedLiability ? (
            <p className="form-hint warning">
              <AlertTriangle size={15} />
              {t("risk.compensation.noLinkedCase", { id: selectedLiability.id })}
            </p>
          ) : null}
          {linkedCases.length > 0 && !eligibleDecisions.length && !decisions.isPending ? (
            <p className="form-hint warning">
              <AlertTriangle size={15} />
              {t("risk.compensation.noFinalDecision")}
            </p>
          ) : null}
          {destinations.length === 0 && selectedTrustCase ? (
            <p className="form-hint warning">
              <AlertTriangle size={15} />
              {t("risk.compensation.noDestinationAccount")}
            </p>
          ) : null}
          {authorize.isError ? (
            <p className="form-error" role="alert">{userErrorMessage(authorize.error, locale)}</p>
          ) : null}
        </section>
      ) : null}

      <section className="panel">
        <div className="panel-heading">
          <h2>{t("risk.compensation.registry.title")}</h2>
          <span>{transfers.length}</span>
        </div>
        {transfers.length === 0 ? (
          <div className="state compact-state">
            <HandCoins size={22} />
            {t("risk.compensation.registry.empty")}
          </div>
        ) : (
          <div className="table-wrap">
            <table className="risk-table compensation-table">
              <thead>
                <tr>
                  <th>{t("risk.compensation.columns.parties")}</th>
                  <th>{t("risk.compensation.columns.amount")}</th>
                  <th>{t("risk.compensation.columns.decision")}</th>
                  <th>{t("risk.compensation.columns.status")}</th>
                  <th>{t("risk.compensation.columns.action")}</th>
                </tr>
              </thead>
              <tbody>
                {transfers.map((transfer) => {
                  const canAccept = transfer.status === "PENDING_ACCEPTANCE"
                    && principal.member_id === transfer.recipient_member_id;
                  const canVoid = transfer.status === "PENDING_ACCEPTANCE"
                    && hasOperatorRole(principal)
                    && principal.member_id !== transfer.authorized_by_member_id
                    && principal.member_id !== transfer.responsible_member_id
                    && principal.member_id !== transfer.recipient_member_id;
                  return (
                    <tr key={transfer.id}>
                      <td>
                        <strong>{memberName(members, transfer.responsible_member_id)}</strong>
                        <small>
                          {t("risk.compensation.to")}{" "}
                          {memberName(members, transfer.recipient_member_id)}
                        </small>
                      </td>
                      <td>
                        <strong>{exact(transfer.amount, locale)} {transfer.denomination}</strong>
                        <small>{transfer.rationale}</small>
                      </td>
                      <td>
                        <span>{transfer.trust_decision_id.slice(0, 8)}</span>
                        <small>{formatLocalDateTime(transfer.authorized_at)}</small>
                      </td>
                      <td>
                        <span className={`status ${
                          transfer.status === "SETTLED"
                            ? "good"
                            : transfer.status === "VOIDED" ? "bad" : "warn"
                        }`}>
                          {t(`risk.compensation.status.${transfer.status}`)}
                        </span>
                      </td>
                      <td>
                        {canAccept ? (
                          <button
                            className="primary-button compact-command"
                            onClick={() => accept.mutate(transfer)}
                            disabled={accept.isPending}
                          >
                            <CheckCircle2 size={15} />
                            {t("risk.compensation.accept")}
                          </button>
                        ) : canVoid ? (
                          <div className="inline-decision compensation-void">
                            <input
                              aria-label={t("risk.compensation.void.reason")}
                              placeholder={t("risk.compensation.void.reason")}
                              value={voidReasons[transfer.id] ?? ""}
                              onChange={(event) => setVoidReasons((current) => ({
                                ...current,
                                [transfer.id]: event.target.value,
                              }))}
                            />
                            <input
                              aria-label={t("risk.compensation.void.evidence")}
                              type="file"
                              accept={evidenceAccept}
                              onChange={(event) => setVoidFiles((current) => ({
                                ...current,
                                [transfer.id]: event.target.files?.[0] ?? null,
                              }))}
                            />
                            <button
                              className="icon-button danger"
                              title={t("risk.compensation.void.action")}
                              disabled={
                                voidTransfer.isPending
                                || !voidFiles[transfer.id]
                                || (voidReasons[transfer.id]?.trim().length ?? 0) < 2
                              }
                              onClick={() => voidTransfer.mutate(transfer)}
                            >
                              <Ban size={15} />
                            </button>
                          </div>
                        ) : (
                          <span className="muted-value">
                            {transfer.accepted_at
                              ? formatLocalDateTime(transfer.accepted_at)
                              : t("risk.compensation.waitingRecipient")}
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {accept.isError ? (
          <p className="form-error" role="alert">{userErrorMessage(accept.error, locale)}</p>
        ) : null}
        {voidTransfer.isError ? (
          <p className="form-error" role="alert">{userErrorMessage(voidTransfer.error, locale)}</p>
        ) : null}
      </section>
    </div>
  );
}
