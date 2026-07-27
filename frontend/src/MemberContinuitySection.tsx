import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  DoorOpen,
  FileWarning,
  HeartPulse,
  KeyRound,
  RefreshCw,
  ShieldCheck,
  UserRoundX,
  X,
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  type Cooperative,
  type Member,
  type MemberContinuityCase,
  type MemberContinuityCaseType,
  type Principal,
  decideMemberContinuity,
  getMemberContinuityCases,
  getSecurityState,
  requestMemberContinuity,
  verifyTotpStepUp,
} from "./api/admin";
import { userErrorMessage } from "./shared/api-error";
import { formatLocalDateTime } from "./shared/date-time";

type ReviewAction = { continuityCase: MemberContinuityCase; approve: boolean };

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

function Status({ value }: { value: MemberContinuityCase["status"] }) {
  const { t } = useTranslation();
  return (
    <span className={`status status-${value.toLowerCase()}`}>
      {t(`admin.memberContinuity.status.${value}`)}
    </span>
  );
}

export default function MemberContinuitySection({
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
            ["MEMBER_REGISTRAR", "COOPERATIVE_ADMIN"],
            cooperative.id,
          ),
      ),
    [cooperatives, principal],
  );
  const cases = useQuery({
    queryKey: ["member-continuity-cases"],
    queryFn: getMemberContinuityCases,
  });
  const security = useQuery({
    queryKey: ["security-state"],
    queryFn: getSecurityState,
    enabled: canReview,
  });
  const [cooperativeId, setCooperativeId] = useState(
    manageableCooperatives[0]?.id ?? "",
  );
  const [memberId, setMemberId] = useState("");
  const [caseType, setCaseType] = useState<MemberContinuityCaseType>("VOLUNTARY_EXIT");
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
      ["ACTIVE", "LIMITED", "SUSPENDED"].includes(member.status),
  );

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["member-continuity-cases"] }),
      queryClient.invalidateQueries({ queryKey: ["members"] }),
      queryClient.invalidateQueries({ queryKey: ["memberships"] }),
      queryClient.invalidateQueries({ queryKey: ["users"] }),
      queryClient.invalidateQueries({ queryKey: ["sessions"] }),
      queryClient.invalidateQueries({ queryKey: ["security-state"] }),
    ]);
  }

  const createCase = useMutation({
    mutationFn: requestMemberContinuity,
    onSuccess: async () => {
      setMessage(t("admin.memberContinuity.createdMessage"));
      setMemberId("");
      setEvidence("");
      await refresh();
    },
  });

  const review = useMutation({
    mutationFn: async () => {
      if (!reviewAction) throw new Error("missing review action");
      if (!security.data?.step_up_active) await verifyTotpStepUp(totpCode);
      return decideMemberContinuity(
        reviewAction.continuityCase,
        reviewAction.approve,
        reviewAction.approve
          ? "INDEPENDENT_CONFIRMATION"
          : "REPORT_NOT_CONFIRMED",
      );
    },
    onSuccess: async (result) => {
      setMessage(
        result.status === "CONFIRMED"
          ? t("admin.memberContinuity.confirmedMessage")
          : result.status === "BLOCKED"
            ? t("admin.memberContinuity.blockedMessage")
            : t("admin.memberContinuity.rejectedMessage"),
      );
      setReviewAction(null);
      setTotpCode("");
      await refresh();
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const member = members.find((item) => item.id === memberId);
    if (!member) return;
    createCase.mutate({
      cooperative_id: cooperativeId,
      member_id: member.id,
      case_type: caseType,
      expected_member_version: member.version,
      evidence_refs: evidenceReferences(evidence),
      reason_code:
        caseType === "VOLUNTARY_EXIT"
          ? "MEMBER_REQUEST_RECEIVED"
          : "OFFICIAL_NOTICE_RECEIVED",
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
        {t("admin.memberContinuity.loading")}
      </div>
    );
  }

  return (
    <div className="member-merge-workspace continuity-workspace">
      {manageableCooperatives.length ? (
        <section className="panel member-merge-editor continuity-editor">
          <div className="panel-heading">
            <div>
              <h2>{t("admin.memberContinuity.createTitle")}</h2>
              <small>{t("admin.memberContinuity.createHint")}</small>
            </div>
            <UserRoundX size={21} />
          </div>
          <form onSubmit={submit}>
            <label>
              {t("admin.memberContinuity.cooperative")}
              <select
                value={cooperativeId}
                onChange={(event) => {
                  setCooperativeId(event.target.value);
                  setMemberId("");
                }}
                required
              >
                <option value="">{t("admin.memberContinuity.chooseCooperative")}</option>
                {manageableCooperatives.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t("admin.memberContinuity.member")}
              <select
                value={memberId}
                onChange={(event) => setMemberId(event.target.value)}
                required
              >
                <option value="">{t("admin.memberContinuity.chooseMember")}</option>
                {eligibleMembers.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.display_name}
                  </option>
                ))}
              </select>
            </label>
            <fieldset className="continuity-type-switch">
              <legend>{t("admin.memberContinuity.caseType")}</legend>
              <label className={caseType === "VOLUNTARY_EXIT" ? "active" : ""}>
                <input
                  type="radio"
                  name="continuity-type"
                  value="VOLUNTARY_EXIT"
                  checked={caseType === "VOLUNTARY_EXIT"}
                  onChange={() => setCaseType("VOLUNTARY_EXIT")}
                />
                <DoorOpen size={17} />
                <span>{t("admin.memberContinuity.type.VOLUNTARY_EXIT")}</span>
              </label>
              <label
                className={caseType === "DEATH_OR_INCAPACITY" ? "active" : ""}
              >
                <input
                  type="radio"
                  name="continuity-type"
                  value="DEATH_OR_INCAPACITY"
                  checked={caseType === "DEATH_OR_INCAPACITY"}
                  onChange={() => setCaseType("DEATH_OR_INCAPACITY")}
                />
                <HeartPulse size={17} />
                <span>{t("admin.memberContinuity.type.DEATH_OR_INCAPACITY")}</span>
              </label>
            </fieldset>
            <div className="continuity-warning" role="note">
              <AlertTriangle size={19} />
              <div>
                <strong>{t("admin.memberContinuity.immediateTitle")}</strong>
                <span>{t("admin.memberContinuity.immediateHint")}</span>
              </div>
            </div>
            <label className="member-merge-evidence">
              {t("admin.memberContinuity.evidence")}
              <textarea
                rows={2}
                value={evidence}
                onChange={(event) => setEvidence(event.target.value)}
                placeholder={t("admin.memberContinuity.evidencePlaceholder")}
                required
              />
              <small>{t("admin.memberContinuity.evidenceHint")}</small>
            </label>
            <button
              className="primary-button"
              type="submit"
              disabled={
                createCase.isPending ||
                !memberId ||
                evidenceReferences(evidence).length < 1
              }
            >
              {createCase.isPending ? (
                <RefreshCw className="spin" size={17} />
              ) : (
                <FileWarning size={17} />
              )}
              <span>{t("admin.memberContinuity.containAndSend")}</span>
            </button>
          </form>
        </section>
      ) : null}

      <section
        className="member-merge-summary continuity-summary"
        aria-label={t("admin.memberContinuity.summary")}
      >
        <div>
          <span>{t("admin.memberContinuity.pending")}</span>
          <strong>{pending}</strong>
        </div>
        <div>
          <span>{t("admin.memberContinuity.blocked")}</span>
          <strong>{blocked}</strong>
        </div>
        <div>
          <span>{t("admin.memberContinuity.confirmed")}</span>
          <strong>
            {allCases.filter((item) => item.status === "CONFIRMED").length}
          </strong>
        </div>
        <div>
          <span>{t("admin.memberContinuity.rejected")}</span>
          <strong>
            {allCases.filter((item) => item.status === "REJECTED").length}
          </strong>
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
            <h2>{t("admin.memberContinuity.cases")}</h2>
            <small>{t("admin.memberContinuity.casesHint")}</small>
          </div>
          <span>{allCases.length}</span>
        </div>
        {allCases.length ? (
          <div className="member-merge-list continuity-list">
            {allCases.map((item) => {
              const isOwn = item.requested_by_user_id === principal.user_id;
              const canDecide =
                item.status === "PENDING_REVIEW" &&
                !isOwn &&
                permanentRole(principal, ["SECURITY_ADMIN"], item.cooperative_id);
              const referenceGroups = Object.entries(item.reference_summary.groups ?? {});
              return (
                <article
                  key={item.id}
                  className={item.status === "BLOCKED" ? "blocked" : ""}
                >
                  <header>
                    <div>
                      <span>{t("admin.memberContinuity.member")}</span>
                      <strong data-i18n-ignore="true">
                        {memberName(members, item.member_id)}
                      </strong>
                    </div>
                    <div className="continuity-case-type">
                      {item.case_type === "VOLUNTARY_EXIT" ? (
                        <DoorOpen size={17} />
                      ) : (
                        <HeartPulse size={17} />
                      )}
                      <strong>
                        {t(`admin.memberContinuity.type.${item.case_type}`)}
                      </strong>
                    </div>
                    <Status value={item.status} />
                  </header>
                  <div className="member-merge-meta">
                    <span>{formatLocalDateTime(item.created_at)}</span>
                    <span>
                      {t("admin.memberContinuity.previousStatus", {
                        status: t(
                          `admin.memberContinuity.memberStatus.${item.previous_member_status}`,
                        ),
                      })}
                    </span>
                  </div>
                  <div className="continuity-impact">
                    <span>
                      {t("admin.memberContinuity.disabledUsers", {
                        count: item.disabled_user_count,
                      })}
                    </span>
                    <span>
                      {t("admin.memberContinuity.suspendedMemberships", {
                        count: item.suspended_membership_count,
                      })}
                    </span>
                  </div>
                  {referenceGroups.length ? (
                    <div className="continuity-references">
                      <strong>{t("admin.memberContinuity.references")}</strong>
                      {referenceGroups.map(([group, count]) => (
                        <span key={group}>
                          {t(`admin.memberContinuity.reference.${group}`, { count })}
                        </span>
                      ))}
                      <small>{t("admin.memberContinuity.referencesHint")}</small>
                    </div>
                  ) : null}
                  {item.review_blockers.length ? (
                    <div className="member-merge-blockers">
                      <strong>
                        <AlertTriangle size={16} />
                        {t("admin.memberContinuity.reviewBlocked")}
                      </strong>
                      {item.review_blockers.map((blocker) => (
                        <span key={blocker}>
                          {t(`admin.memberContinuity.blocker.${blocker}`)}
                        </span>
                      ))}
                    </div>
                  ) : null}
                  {canDecide ? (
                    <div className="member-merge-actions">
                      <button
                        className="compact-command approve"
                        onClick={() =>
                          setReviewAction({ continuityCase: item, approve: true })
                        }
                      >
                        <Check size={15} />
                        {t("admin.memberContinuity.approve")}
                      </button>
                      <button
                        className="compact-command"
                        onClick={() =>
                          setReviewAction({ continuityCase: item, approve: false })
                        }
                      >
                        <X size={15} />
                        {t("admin.memberContinuity.reject")}
                      </button>
                    </div>
                  ) : item.status === "PENDING_REVIEW" ? (
                    <span className="independent-note">
                      <AlertTriangle size={14} />
                      {isOwn
                        ? t("admin.memberContinuity.anotherReviewer")
                        : t("admin.memberContinuity.waitingReviewer")}
                    </span>
                  ) : null}
                </article>
              );
            })}
          </div>
        ) : (
          <div className="empty-state">{t("admin.memberContinuity.noCases")}</div>
        )}
      </section>

      {reviewAction ? (
        <div className="modal-backdrop" role="presentation">
          <section
            className="service-confirm-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="member-continuity-confirm-title"
          >
            <header>
              <div>
                <span className="eyebrow">
                  {t("admin.memberContinuity.protectedAction")}
                </span>
                <h2 id="member-continuity-confirm-title">
                  {reviewAction.approve
                    ? t("admin.memberContinuity.confirmApprove")
                    : t("admin.memberContinuity.confirmReject")}
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
            <p>{t("admin.memberContinuity.confirmHint")}</p>
            {security.data?.totp_enabled ? (
              needsTotp ? (
                <label>
                  {t("admin.memberContinuity.totpCode")}
                  <input
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    pattern="[0-9]{6}"
                    maxLength={6}
                    value={totpCode}
                    onChange={(event) =>
                      setTotpCode(event.target.value.replace(/\D/gu, ""))
                    }
                    autoFocus
                    required
                  />
                </label>
              ) : (
                <p className="form-success">
                  <ShieldCheck size={16} />
                  {t("admin.memberContinuity.identityConfirmed")}
                </p>
              )
            ) : (
              <p className="form-error">
                <KeyRound size={16} />
                {t("admin.memberContinuity.enableTotp")}
              </p>
            )}
            <div className="dialog-actions">
              <button
                className="secondary-button"
                onClick={() => setReviewAction(null)}
              >
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
                {t("admin.memberContinuity.confirmAction")}
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}