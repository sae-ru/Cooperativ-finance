import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  FileWarning,
  GitMerge,
  KeyRound,
  RefreshCw,
  ShieldCheck,
  X,
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  type Cooperative,
  type Member,
  type MemberMergeCase,
  type Principal,
  decideMemberMerge,
  getMemberMergeCases,
  getSecurityState,
  requestMemberMerge,
  verifyTotpStepUp,
} from "./api/admin";
import { userErrorMessage } from "./shared/api-error";
import { formatLocalDateTime } from "./shared/date-time";

type ReviewAction = { mergeCase: MemberMergeCase; approve: boolean };

function permanentRole(
  principal: Principal,
  roles: string[],
  cooperativeId?: string,
): boolean {
  return principal.roles.some(
    (grant) =>
      grant.source !== "BREAK_GLASS" &&
      roles.includes(grant.role) &&
      (grant.cooperative_id === null || grant.cooperative_id === cooperativeId),
  );
}

function evidenceReferences(value: string): string[] {
  return value
    .split(/[\n,;]/u)
    .map((item) => item.trim())
    .filter(Boolean);
}

function memberName(members: Member[], id: string): string {
  return members.find((item) => item.id === id)?.display_name ?? id.slice(0, 8);
}

function blockerGroups(mergeCase: MemberMergeCase): Array<{ key: string; count: number }> {
  const groups = new Map<string, number>();
  for (const code of mergeCase.blocker_summary.codes ?? []) {
    groups.set(`admin.memberMerge.blocker.${code}`, 1);
  }
  for (const [reference, count] of Object.entries(
    mergeCase.blocker_summary.references ?? {},
  )) {
    const schema = reference.split(".")[0] ?? "other";
    groups.set(
      `admin.memberMerge.reference.${schema}`,
      (groups.get(`admin.memberMerge.reference.${schema}`) ?? 0) + count,
    );
  }
  return [...groups].map(([key, count]) => ({ key, count }));
}

function Status({ value }: { value: MemberMergeCase["status"] }) {
  const { t } = useTranslation();
  return (
    <span className={`status status-${value.toLowerCase()}`}>
      {t(`admin.memberMerge.status.${value}`)}
    </span>
  );
}

export default function MemberMergeSection({
  principal,
  cooperatives,
  members,
}: {
  principal: Principal;
  cooperatives: Cooperative[];
  members: Member[];
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const canReview = principal.roles.some(
    (grant) => grant.source !== "BREAK_GLASS" && grant.role === "SECURITY_ADMIN",
  );
  const manageableCooperatives = useMemo(
    () =>
      cooperatives.filter(
        (cooperative) =>
          cooperative.status === "ACTIVE" &&
          permanentRole(
            principal,
            ["DATA_STEWARD", "MEMBER_REGISTRAR"],
            cooperative.id,
          ),
      ),
    [cooperatives, principal],
  );
  const cases = useQuery({
    queryKey: ["member-merge-cases"],
    queryFn: getMemberMergeCases,
  });
  const security = useQuery({
    queryKey: ["security-state"],
    queryFn: getSecurityState,
    enabled: canReview,
  });
  const [cooperativeId, setCooperativeId] = useState(manageableCooperatives[0]?.id ?? "");
  const [sourceId, setSourceId] = useState("");
  const [survivorId, setSurvivorId] = useState("");
  const [evidence, setEvidence] = useState("");
  const [message, setMessage] = useState("");
  const [reviewAction, setReviewAction] = useState<ReviewAction | null>(null);
  const [totpCode, setTotpCode] = useState("");

  useEffect(() => {
    if (!cooperativeId && manageableCooperatives[0]) {
      setCooperativeId(manageableCooperatives[0].id);
    }
  }, [cooperativeId, manageableCooperatives]);

  const eligibleMembers = members.filter(
    (member) =>
      member.registered_by_cooperative_id === cooperativeId &&
      !["MERGED", "REJECTED", "EXITED"].includes(member.status),
  );

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["member-merge-cases"] }),
      queryClient.invalidateQueries({ queryKey: ["members"] }),
      queryClient.invalidateQueries({ queryKey: ["memberships"] }),
      queryClient.invalidateQueries({ queryKey: ["users"] }),
      queryClient.invalidateQueries({ queryKey: ["security-state"] }),
    ]);
  }

  const createCase = useMutation({
    mutationFn: requestMemberMerge,
    onSuccess: async (result) => {
      setMessage(
        result.status === "BLOCKED"
          ? t("admin.memberMerge.createdBlocked")
          : t("admin.memberMerge.createdPending"),
      );
      setSourceId("");
      setSurvivorId("");
      setEvidence("");
      await refresh();
    },
  });

  const review = useMutation({
    mutationFn: async () => {
      if (!reviewAction) throw new Error("missing review action");
      if (!security.data?.step_up_active) await verifyTotpStepUp(totpCode);
      return decideMemberMerge(
        reviewAction.mergeCase,
        reviewAction.approve,
        reviewAction.approve ? "INDEPENDENT_SECURITY_REVIEW" : "DUPLICATE_NOT_CONFIRMED",
      );
    },
    onSuccess: async (result) => {
      setMessage(
        result.status === "APPROVED"
          ? t("admin.memberMerge.approvedMessage")
          : result.status === "BLOCKED"
            ? t("admin.memberMerge.reviewBlocked")
            : t("admin.memberMerge.rejectedMessage"),
      );
      setReviewAction(null);
      setTotpCode("");
      await refresh();
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const source = members.find((item) => item.id === sourceId);
    const survivor = members.find((item) => item.id === survivorId);
    if (!source || !survivor) return;
    createCase.mutate({
      cooperative_id: cooperativeId,
      source_member_id: source.id,
      survivor_member_id: survivor.id,
      source_expected_version: source.version,
      survivor_expected_version: survivor.version,
      evidence_refs: evidenceReferences(evidence),
      reason_code: "CONFIRMED_DUPLICATE",
    });
  }

  const allCases = cases.data ?? [];
  const pending = allCases.filter((item) => item.status === "PENDING_REVIEW").length;
  const blocked = allCases.filter((item) => item.status === "BLOCKED").length;
  const needsTotp = canReview && !security.data?.step_up_active;
  const canConfirm = !needsTotp || /^[0-9]{6}$/u.test(totpCode);
  const currentError = cases.error ?? security.error ?? createCase.error ?? review.error;

  if (cases.isPending) {
    return (
      <div className="state" role="status">
        <RefreshCw className="spin" size={22} />
        {t("admin.memberMerge.loading")}
      </div>
    );
  }

  return (
    <div className="member-merge-workspace">
      {manageableCooperatives.length ? (
        <section className="panel member-merge-editor">
          <div className="panel-heading">
            <div>
              <h2>{t("admin.memberMerge.createTitle")}</h2>
              <small>{t("admin.memberMerge.createHint")}</small>
            </div>
            <GitMerge size={21} />
          </div>
          <form onSubmit={submit}>
            <label>
              {t("admin.memberMerge.cooperative")}
              <select
                value={cooperativeId}
                onChange={(event) => {
                  setCooperativeId(event.target.value);
                  setSourceId("");
                  setSurvivorId("");
                }}
                required
              >
                <option value="">{t("admin.memberMerge.chooseCooperative")}</option>
                {manageableCooperatives.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t("admin.memberMerge.source")}
              <select value={sourceId} onChange={(event) => setSourceId(event.target.value)} required>
                <option value="">{t("admin.memberMerge.chooseSource")}</option>
                {eligibleMembers
                  .filter((item) => item.id !== survivorId)
                  .map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.display_name}
                    </option>
                  ))}
              </select>
              <small>{t("admin.memberMerge.sourceHint")}</small>
            </label>
            <label>
              {t("admin.memberMerge.survivor")}
              <select
                value={survivorId}
                onChange={(event) => setSurvivorId(event.target.value)}
                required
              >
                <option value="">{t("admin.memberMerge.chooseSurvivor")}</option>
                {eligibleMembers
                  .filter((item) => item.id !== sourceId)
                  .map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.display_name}
                    </option>
                  ))}
              </select>
              <small>{t("admin.memberMerge.survivorHint")}</small>
            </label>
            <label className="member-merge-evidence">
              {t("admin.memberMerge.evidence")}
              <textarea
                rows={2}
                value={evidence}
                onChange={(event) => setEvidence(event.target.value)}
                placeholder={t("admin.memberMerge.evidencePlaceholder")}
                required
              />
              <small>{t("admin.memberMerge.evidenceHint")}</small>
            </label>
            <button
              className="primary-button"
              type="submit"
              disabled={
                createCase.isPending ||
                !sourceId ||
                !survivorId ||
                evidenceReferences(evidence).length < 1
              }
            >
              {createCase.isPending ? (
                <RefreshCw className="spin" size={17} />
              ) : (
                <FileWarning size={17} />
              )}
              <span>{t("admin.memberMerge.checkAndSend")}</span>
            </button>
          </form>
        </section>
      ) : null}

      <section className="member-merge-summary" aria-label={t("admin.memberMerge.summary")}>
        <div>
          <span>{t("admin.memberMerge.pending")}</span>
          <strong>{pending}</strong>
        </div>
        <div>
          <span>{t("admin.memberMerge.blocked")}</span>
          <strong>{blocked}</strong>
        </div>
        <div>
          <span>{t("admin.memberMerge.completed")}</span>
          <strong>{allCases.filter((item) => item.status === "APPROVED").length}</strong>
        </div>
      </section>

      {message ? (
        <p className="form-success" role="status">
          <Check size={16} />
          {message}
        </p>
      ) : null}
      {currentError ? (
        <p className="form-error" role="alert">
          {userErrorMessage(currentError)}
        </p>
      ) : null}

      <section className="panel member-merge-cases">
        <div className="panel-heading">
          <div>
            <h2>{t("admin.memberMerge.cases")}</h2>
            <small>{t("admin.memberMerge.casesHint")}</small>
          </div>
          <span>{allCases.length}</span>
        </div>
        {allCases.length ? (
          <div className="member-merge-list">
            {allCases.map((item) => {
              const isOwn = item.requested_by_user_id === principal.user_id;
              const canDecide =
                item.status === "PENDING_REVIEW" &&
                !isOwn &&
                permanentRole(principal, ["SECURITY_ADMIN"], item.cooperative_id);
              const blockers = blockerGroups(item);
              return (
                <article key={item.id} className={item.status === "BLOCKED" ? "blocked" : ""}>
                  <header>
                    <div>
                      <span>{t("admin.memberMerge.archive")}</span>
                      <strong data-i18n-ignore="true">{memberName(members, item.source_member_id)}</strong>
                    </div>
                    <GitMerge size={18} />
                    <div>
                      <span>{t("admin.memberMerge.keep")}</span>
                      <strong data-i18n-ignore="true">{memberName(members, item.survivor_member_id)}</strong>
                    </div>
                    <Status value={item.status} />
                  </header>
                  <div className="member-merge-meta">
                    <span>{formatLocalDateTime(item.created_at)}</span>
                    <span>{t("admin.memberMerge.validUntil", { date: formatLocalDateTime(item.expires_at) })}</span>
                  </div>
                  {blockers.length ? (
                    <div className="member-merge-blockers">
                      <strong>
                        <AlertTriangle size={16} />
                        {t("admin.memberMerge.cannotMerge")}
                      </strong>
                      {blockers.map((blocker) => (
                        <span key={blocker.key}>
                          {t(blocker.key, { count: blocker.count })}
                        </span>
                      ))}
                      <small>{t("admin.memberMerge.resolveFirst")}</small>
                    </div>
                  ) : null}
                  {canDecide ? (
                    <div className="member-merge-actions">
                      <button
                        className="compact-command approve"
                        onClick={() => setReviewAction({ mergeCase: item, approve: true })}
                      >
                        <Check size={15} />
                        {t("admin.memberMerge.approve")}
                      </button>
                      <button
                        className="compact-command"
                        onClick={() => setReviewAction({ mergeCase: item, approve: false })}
                      >
                        <X size={15} />
                        {t("admin.memberMerge.reject")}
                      </button>
                    </div>
                  ) : item.status === "PENDING_REVIEW" ? (
                    <span className="independent-note">
                      <AlertTriangle size={14} />
                      {isOwn
                        ? t("admin.memberMerge.anotherReviewer")
                        : t("admin.memberMerge.waitingReviewer")}
                    </span>
                  ) : null}
                </article>
              );
            })}
          </div>
        ) : (
          <div className="empty-state">{t("admin.memberMerge.noCases")}</div>
        )}
      </section>

      {reviewAction ? (
        <div className="modal-backdrop" role="presentation">
          <section
            className="service-confirm-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="member-merge-confirm-title"
          >
            <header>
              <div>
                <span className="eyebrow">{t("admin.memberMerge.protectedAction")}</span>
                <h2 id="member-merge-confirm-title">
                  {reviewAction.approve
                    ? t("admin.memberMerge.confirmApprove")
                    : t("admin.memberMerge.confirmReject")}
                </h2>
              </div>
              <button
                className="icon-button"
                title={t("common.close")}
                aria-label={t("common.close")}
                onClick={() => setReviewAction(null)}
              >
                <X size={18} />
              </button>
            </header>
            <p>{t("admin.memberMerge.confirmHint")}</p>
            {security.data?.totp_enabled ? (
              needsTotp ? (
                <label>
                  {t("admin.memberMerge.totpCode")}
                  <input
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    pattern="[0-9]{6}"
                    maxLength={6}
                    value={totpCode}
                    onChange={(event) => setTotpCode(event.target.value.replace(/\D/gu, ""))}
                    autoFocus
                    required
                  />
                </label>
              ) : (
                <p className="form-success">
                  <ShieldCheck size={16} />
                  {t("admin.memberMerge.identityConfirmed")}
                </p>
              )
            ) : (
              <p className="form-error">
                <KeyRound size={16} />
                {t("admin.memberMerge.enableTotp")}
              </p>
            )}
            <div className="dialog-actions">
              <button className="secondary-button" onClick={() => setReviewAction(null)}>
                {t("common.cancel")}
              </button>
              <button
                className="primary-button"
                disabled={
                  review.isPending ||
                  security.isPending ||
                  !security.data?.totp_enabled ||
                  !canConfirm
                }
                onClick={() => review.mutate()}
              >
                {review.isPending ? (
                  <RefreshCw className="spin" size={16} />
                ) : (
                  <Check size={16} />
                )}
                {t("admin.memberMerge.confirmAction")}
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}