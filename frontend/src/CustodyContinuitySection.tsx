import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  Check,
  ClipboardCheck,
  FileCheck2,
  HeartPulse,
  RefreshCw,
  ShieldCheck,
  Upload,
  UserCheck,
  X,
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  type CustodyContinuityCase,
  type CustodyContinuityItem,
  type CustodyContinuitySource,
  attestCustodyContinuityItem,
  decideCustodyContinuity,
  decideCustodyContinuityCandidate,
  getCustodyContinuityCandidates,
  getCustodyContinuityCases,
  getCustodyContinuitySources,
  requestCustodyContinuity,
} from "./api/custody-continuity";
import {
  type Principal,
  getSecurityState,
  verifyTotpStepUp,
} from "./api/admin";
import { uploadEvidence } from "./api/inventory";
import { userErrorMessage } from "./shared/api-error";
import { formatLocalDateTime } from "./shared/date-time";

type CountDraft = {
  quantity: string;
  notes: string;
  file: File | null;
};

type ReviewAction = {
  continuityCase: CustodyContinuityCase;
  approve: boolean;
};

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

function defaultExpiry(): string {
  const date = new Date(Date.now() + 2 * 24 * 60 * 60 * 1000);
  date.setMinutes(date.getMinutes() - date.getTimezoneOffset());
  return date.toISOString().slice(0, 16);
}

function Status({ value }: { value: CustodyContinuityCase["status"] }) {
  const { t } = useTranslation();
  return (
    <span className={`status status-${value.toLowerCase()}`}>
      {t(`inventory.continuity.status.${value}`)}
    </span>
  );
}

function ItemStatus({ value }: { value: CustodyContinuityItem["status"] }) {
  const { t } = useTranslation();
  return (
    <span className={`status status-${value.toLowerCase()}`}>
      {t(`inventory.continuity.itemStatus.${value}`)}
    </span>
  );
}

function ContinuityProgress({
  continuityCase,
}: {
  continuityCase: CustodyContinuityCase;
}) {
  const { t } = useTranslation();
  const active =
    continuityCase.status === "INVENTORY_PENDING" ||
    continuityCase.status === "BLOCKED"
      ? 0
      : continuityCase.status === "PENDING_APPROVAL"
        ? 1
        : 2;
  return (
    <ol className="custody-continuity-progress">
      {["inventory", "approval", "acceptance"].map((step, index) => (
        <li className={index <= active ? "active" : ""} key={step}>
          <span>{index + 1}</span>
          {t(`inventory.continuity.step.${step}`)}
        </li>
      ))}
    </ol>
  );
}

export default function CustodyContinuitySection({
  principal,
}: {
  principal: Principal;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const cases = useQuery({
    queryKey: ["custody-continuity-cases"],
    queryFn: getCustodyContinuityCases,
  });
  const sources = useQuery({
    queryKey: ["custody-continuity-sources"],
    queryFn: getCustodyContinuitySources,
  });
  const canReview = permanentRole(principal, ["SECURITY_ADMIN"]);
  const security = useQuery({
    queryKey: ["security-state"],
    queryFn: getSecurityState,
    enabled: canReview,
  });
  const manageableSources = useMemo(
    () =>
      (sources.data ?? []).filter((source) =>
        permanentRole(
          principal,
          ["COOPERATIVE_ADMIN", "SECURITY_ADMIN"],
          source.cooperative_id,
        ),
      ),
    [principal, sources.data],
  );
  const [sourceAssignmentId, setSourceAssignmentId] = useState("");
  const source = manageableSources.find(
    (item) => item.source_assignment_id === sourceAssignmentId,
  );
  const candidates = useQuery({
    queryKey: [
      "custody-continuity-candidates",
      source?.cooperative_id,
      source?.warehouse_id,
    ],
    queryFn: () =>
      getCustodyContinuityCandidates(
        source!.cooperative_id,
        source!.warehouse_id,
      ),
    enabled: Boolean(source),
  });
  const [candidateRoleId, setCandidateRoleId] = useState("");
  const [handoverPlace, setHandoverPlace] = useState("");
  const [validUntil, setValidUntil] = useState(defaultExpiry);
  const [evidenceRefs, setEvidenceRefs] = useState("");
  const [countDrafts, setCountDrafts] = useState<Record<string, CountDraft>>({});
  const [acceptanceFiles, setAcceptanceFiles] = useState<
    Record<string, File | null>
  >({});
  const [reviewAction, setReviewAction] = useState<ReviewAction | null>(null);
  const [totpCode, setTotpCode] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (!sourceAssignmentId && manageableSources[0]) {
      setSourceAssignmentId(manageableSources[0].source_assignment_id);
    }
  }, [manageableSources, sourceAssignmentId]);

  useEffect(() => {
    setCandidateRoleId("");
    if (source) setHandoverPlace(source.warehouse_name);
  }, [source]);

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["custody-continuity-cases"] }),
      queryClient.invalidateQueries({ queryKey: ["custody-continuity-sources"] }),
      queryClient.invalidateQueries({
        queryKey: ["custody-continuity-candidates"],
      }),
      queryClient.invalidateQueries({ queryKey: ["inventory"] }),
      queryClient.invalidateQueries({ queryKey: ["responsibility"] }),
      queryClient.invalidateQueries({ queryKey: ["security-state"] }),
    ]);
  }

  const createCase = useMutation({
    mutationFn: requestCustodyContinuity,
    onSuccess: async () => {
      setMessage(t("inventory.continuity.message.created"));
      setSourceAssignmentId("");
      setCandidateRoleId("");
      setEvidenceRefs("");
      setValidUntil(defaultExpiry());
      await refresh();
    },
  });

  const countItem = useMutation({
    mutationFn: async ({
      continuityCase,
      item,
    }: {
      continuityCase: CustodyContinuityCase;
      item: CustodyContinuityItem;
    }) => {
      const draft = countDrafts[item.id];
      if (!draft?.file) throw new Error("missing inventory evidence");
      const evidenceId = await uploadEvidence(
        continuityCase.cooperative_id,
        draft.file,
        "INVENTORY_ACT",
      );
      return attestCustodyContinuityItem(continuityCase, item, {
        actual_quantity: draft.quantity,
        condition_notes: draft.notes,
        evidence_ids: [evidenceId],
      });
    },
    onSuccess: async (result, variables) => {
      setMessage(
        result.status === "BLOCKED"
          ? t("inventory.continuity.message.discrepancy")
          : t("inventory.continuity.message.counted"),
      );
      setCountDrafts((value) => {
        const next = { ...value };
        delete next[variables.item.id];
        return next;
      });
      await refresh();
    },
  });

  const review = useMutation({
    mutationFn: async () => {
      if (!reviewAction) throw new Error("missing review action");
      if (!security.data?.step_up_active) await verifyTotpStepUp(totpCode);
      return decideCustodyContinuity(
        reviewAction.continuityCase,
        reviewAction.approve,
        reviewAction.approve
          ? "INDEPENDENT_INVENTORY_REVIEW"
          : "EMERGENCY_TRANSFER_REJECTED",
      );
    },
    onSuccess: async (result) => {
      setMessage(
        result.status === "PENDING_ACCEPTANCE"
          ? t("inventory.continuity.message.approved")
          : t("inventory.continuity.message.rejected"),
      );
      setReviewAction(null);
      setTotpCode("");
      await refresh();
    },
  });

  const candidateDecision = useMutation({
    mutationFn: async ({
      continuityCase,
      accept,
    }: {
      continuityCase: CustodyContinuityCase;
      accept: boolean;
    }) => {
      const file = acceptanceFiles[continuityCase.id];
      const evidenceIds =
        accept && file
          ? [
              await uploadEvidence(
                continuityCase.cooperative_id,
                file,
                "CUSTODY_ACT",
              ),
            ]
          : [];
      return decideCustodyContinuityCandidate(
        continuityCase,
        accept,
        evidenceIds,
      );
    },
    onSuccess: async (result, variables) => {
      setMessage(
        result.status === "ACCEPTED"
          ? t("inventory.continuity.message.accepted")
          : t("inventory.continuity.message.declined"),
      );
      setAcceptanceFiles((value) => ({
        ...value,
        [variables.continuityCase.id]: null,
      }));
      await refresh();
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!source) return;
    createCase.mutate({
      member_continuity_case_id: source.member_continuity_case_id,
      source_assignment_id: source.source_assignment_id,
      expected_source_assignment_version: source.source_assignment_version,
      target_role_assignment_id: candidateRoleId,
      handover_place: handoverPlace.trim(),
      temporary_valid_until: new Date(validUntil).toISOString(),
      evidence_refs: evidenceReferences(evidenceRefs),
    });
  }

  function updateDraft(item: CustodyContinuityItem, patch: Partial<CountDraft>) {
    setCountDrafts((value) => ({
      ...value,
      [item.id]: {
        quantity: value[item.id]?.quantity ?? item.expected_quantity,
        notes: value[item.id]?.notes ?? "",
        file: value[item.id]?.file ?? null,
        ...patch,
      },
    }));
  }

  const allCases = cases.data ?? [];
  const currentError =
    cases.error ??
    sources.error ??
    candidates.error ??
    security.error ??
    createCase.error ??
    countItem.error ??
    review.error ??
    candidateDecision.error;

  if (cases.isPending || sources.isPending) {
    return (
      <div className="state" role="status">
        <RefreshCw className="spin" size={21} />
        {t("inventory.continuity.loading")}
      </div>
    );
  }

  return (
    <div className="custody-continuity-workspace">
      {manageableSources.length ? (
        <section className="panel custody-continuity-create">
          <div className="panel-heading">
            <div>
              <h2>{t("inventory.continuity.createTitle")}</h2>
              <small>{t("inventory.continuity.createHint")}</small>
            </div>
            <HeartPulse size={21} />
          </div>
          <form onSubmit={submit}>
            <label>
              {t("inventory.continuity.source")}
              <select
                value={sourceAssignmentId}
                onChange={(event) => setSourceAssignmentId(event.target.value)}
                required
              >
                <option value="">{t("inventory.continuity.chooseSource")}</option>
                {manageableSources.map((item) => (
                  <option key={item.source_assignment_id} value={item.source_assignment_id}>
                    {item.source_member_name} · {item.warehouse_name} ·{" "}
                    {t("inventory.continuity.lotCount", { count: item.lot_count })}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t("inventory.continuity.candidate")}
              <select
                value={candidateRoleId}
                onChange={(event) => setCandidateRoleId(event.target.value)}
                disabled={!source || candidates.isPending}
                required
              >
                <option value="">
                  {candidates.isPending
                    ? t("inventory.continuity.loadingCandidates")
                    : t("inventory.continuity.chooseCandidate")}
                </option>
                {(candidates.data ?? []).map((item) => (
                  <option key={item.role_assignment_id} value={item.role_assignment_id}>
                    {item.display_name}
                  </option>
                ))}
              </select>
            </label>
            <label className="span-two">
              {t("inventory.continuity.handoverPlace")}
              <input
                value={handoverPlace}
                onChange={(event) => setHandoverPlace(event.target.value)}
                required
              />
            </label>
            <label>
              {t("inventory.continuity.validUntil")}
              <input
                type="datetime-local"
                value={validUntil}
                onChange={(event) => setValidUntil(event.target.value)}
                required
              />
            </label>
            <label>
              {t("inventory.continuity.evidenceReferences")}
              <textarea
                rows={2}
                value={evidenceRefs}
                onChange={(event) => setEvidenceRefs(event.target.value)}
                placeholder={t("inventory.continuity.evidencePlaceholder")}
                required
              />
            </label>
            <div className="continuity-warning span-two" role="note">
              <AlertTriangle size={18} />
              <div>
                <strong>{t("inventory.continuity.holdTitle")}</strong>
                <span>{t("inventory.continuity.holdHint")}</span>
              </div>
            </div>
            <button
              className="primary-button"
              disabled={
                createCase.isPending ||
                !source ||
                !candidateRoleId ||
                evidenceReferences(evidenceRefs).length < 1
              }
            >
              {createCase.isPending ? (
                <RefreshCw className="spin" size={17} />
              ) : (
                <HeartPulse size={17} />
              )}
              {t("inventory.continuity.start")}
            </button>
          </form>
        </section>
      ) : null}

      <section className="custody-continuity-summary">
        <div>
          <span>{t("inventory.continuity.summaryInventory")}</span>
          <strong>
            {allCases.filter((item) => item.status === "INVENTORY_PENDING").length}
          </strong>
        </div>
        <div>
          <span>{t("inventory.continuity.summaryApproval")}</span>
          <strong>
            {allCases.filter((item) => item.status === "PENDING_APPROVAL").length}
          </strong>
        </div>
        <div>
          <span>{t("inventory.continuity.summaryAcceptance")}</span>
          <strong>
            {allCases.filter((item) => item.status === "PENDING_ACCEPTANCE").length}
          </strong>
        </div>
        <div>
          <span>{t("inventory.continuity.summaryBlocked")}</span>
          <strong>
            {allCases.filter((item) => item.status === "BLOCKED").length}
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

      <section className="panel custody-continuity-cases">
        <div className="panel-heading">
          <div>
            <h2>{t("inventory.continuity.cases")}</h2>
            <small>{t("inventory.continuity.casesHint")}</small>
          </div>
          <span>{allCases.length}</span>
        </div>
        {allCases.length ? (
          <div className="custody-continuity-list">
            {allCases.map((continuityCase) => {
              const canCount =
                continuityCase.status === "INVENTORY_PENDING" &&
                principal.user_id !== continuityCase.requested_by_user_id &&
                principal.member_id !== continuityCase.target_member_id &&
                permanentRole(
                  principal,
                  ["INVENTORY_CONTROLLER", "AUDITOR"],
                  continuityCase.cooperative_id,
                );
              const canApprove =
                continuityCase.status === "PENDING_APPROVAL" &&
                principal.user_id !== continuityCase.requested_by_user_id &&
                principal.member_id !== continuityCase.target_member_id &&
                permanentRole(
                  principal,
                  ["SECURITY_ADMIN"],
                  continuityCase.cooperative_id,
                );
              const canReject =
                ["INVENTORY_PENDING", "PENDING_APPROVAL", "BLOCKED"].includes(
                  continuityCase.status,
                ) &&
                principal.user_id !== continuityCase.requested_by_user_id &&
                principal.member_id !== continuityCase.target_member_id &&
                permanentRole(
                  principal,
                  ["SECURITY_ADMIN"],
                  continuityCase.cooperative_id,
                );
              const isCandidate =
                continuityCase.status === "PENDING_ACCEPTANCE" &&
                principal.member_id === continuityCase.target_member_id &&
                permanentRole(
                  principal,
                  ["WAREHOUSE_CUSTODIAN"],
                  continuityCase.cooperative_id,
                );
              return (
                <article
                  className={continuityCase.status === "BLOCKED" ? "blocked" : ""}
                  key={continuityCase.id}
                >
                  <header>
                    <div>
                      <span>{t("inventory.continuity.transfer")}</span>
                      <strong>
                        <b data-i18n-ignore="true">
                          {continuityCase.source_member_name}
                        </b>
                        <ArrowRight size={16} />
                        <b data-i18n-ignore="true">
                          {continuityCase.target_member_name}
                        </b>
                      </strong>
                    </div>
                    <div>
                      <span>{t("inventory.continuity.warehouse")}</span>
                      <strong>
                        {continuityCase.warehouse_name}
                      </strong>
                    </div>
                    <Status value={continuityCase.status} />
                  </header>
                  <ContinuityProgress continuityCase={continuityCase} />
                  <div className="custody-continuity-meta">
                    <span>
                      {t("inventory.continuity.createdAt", {
                        date: formatLocalDateTime(continuityCase.created_at),
                      })}
                    </span>
                    <span>
                      {t("inventory.continuity.expiresAt", {
                        date: formatLocalDateTime(
                          continuityCase.temporary_valid_until,
                        ),
                      })}
                    </span>
                    <span>
                      {continuityCase.handover_place}
                    </span>
                  </div>
                  {continuityCase.status !== "ACCEPTED" &&
                  continuityCase.status !== "REJECTED" ? (
                    <p className="custody-retained-note">
                      <ShieldCheck size={16} />
                      {t("inventory.continuity.oldCustodianRetained")}
                    </p>
                  ) : null}
                  {continuityCase.blocked_reasons.length ? (
                    <div className="custody-continuity-blockers">
                      <strong>
                        <AlertTriangle size={16} />
                        {t("inventory.continuity.blockedTitle")}
                      </strong>
                      {continuityCase.blocked_reasons.map((reason) => (
                        <span key={reason}>
                          {t(`inventory.continuity.blocker.${reason}`)}
                        </span>
                      ))}
                    </div>
                  ) : null}
                  <div className="custody-continuity-items">
                    {continuityCase.items.map((item) => {
                      const draft = countDrafts[item.id] ?? {
                        quantity: item.expected_quantity,
                        notes: "",
                        file: null,
                      };
                      return (
                        <div className="custody-continuity-item" key={item.id}>
                          <div>
                            <strong>
                              {item.product_name} · {item.lot_number}
                            </strong>
                            <span>
                              {t("inventory.continuity.expectedQuantity", {
                                quantity: item.expected_quantity,
                                unit: item.unit_symbol,
                              })}
                            </span>
                          </div>
                          <ItemStatus value={item.status} />
                          {canCount && item.status === "PENDING" ? (
                            <div className="custody-count-form">
                              <label>
                                {t("inventory.continuity.actualQuantity")}
                                <input
                                  inputMode="decimal"
                                  value={draft.quantity}
                                  onChange={(event) =>
                                    updateDraft(item, {
                                      quantity: event.target.value,
                                    })
                                  }
                                  required
                                />
                              </label>
                              <label>
                                {t("inventory.continuity.conditionNotes")}
                                <input
                                  value={draft.notes}
                                  onChange={(event) =>
                                    updateDraft(item, {
                                      notes: event.target.value,
                                    })
                                  }
                                  required
                                />
                              </label>
                              <label className="evidence-file">
                                <Upload size={15} />
                                <span>
                                  {draft.file?.name ??
                                    t("inventory.continuity.countEvidence")}
                                </span>
                                <input
                                  type="file"
                                  accept=".pdf,.jpg,.jpeg,.png,.webp,.txt"
                                  aria-label={t(
                                    "inventory.continuity.countEvidence",
                                  )}
                                  onChange={(event) =>
                                    updateDraft(item, {
                                      file: event.target.files?.[0] ?? null,
                                    })
                                  }
                                />
                              </label>
                              <button
                                className="compact-command approve"
                                disabled={
                                  countItem.isPending ||
                                  !draft.quantity ||
                                  !draft.notes.trim() ||
                                  !draft.file
                                }
                                onClick={() =>
                                  countItem.mutate({ continuityCase, item })
                                }
                              >
                                <ClipboardCheck size={15} />
                                {t("inventory.continuity.recordCount")}
                              </button>
                            </div>
                          ) : item.actual_quantity !== null ? (
                            <small>
                              {t("inventory.continuity.actualRecorded", {
                                quantity: item.actual_quantity,
                                unit: item.unit_symbol,
                              })}
                            </small>
                          ) : null}
                        </div>
                      );
                    })}
                  </div>
                  {canApprove || canReject ? (
                    <div className="custody-continuity-actions">
                      {canApprove ? (
                        <button
                          className="compact-command approve"
                          onClick={() =>
                            setReviewAction({
                              continuityCase,
                              approve: true,
                            })
                          }
                        >
                          <Check size={15} />
                          {t("inventory.continuity.approve")}
                        </button>
                      ) : null}
                      {canReject ? (
                        <button
                          className="compact-command"
                          onClick={() =>
                            setReviewAction({
                              continuityCase,
                              approve: false,
                            })
                          }
                        >
                          <X size={15} />
                          {t("inventory.continuity.reject")}
                        </button>
                      ) : null}
                    </div>
                  ) : null}
                  {isCandidate ? (
                    <div className="custody-candidate-actions">
                      <p>{t("inventory.continuity.candidateHint")}</p>
                      <label className="evidence-file">
                        <FileCheck2 size={15} />
                        <span>
                          {acceptanceFiles[continuityCase.id]?.name ??
                            t("inventory.continuity.acceptanceEvidence")}
                        </span>
                        <input
                          type="file"
                          accept=".pdf,.jpg,.jpeg,.png,.webp,.txt"
                          aria-label={t(
                            "inventory.continuity.acceptanceEvidence",
                          )}
                          onChange={(event) =>
                            setAcceptanceFiles((value) => ({
                              ...value,
                              [continuityCase.id]:
                                event.target.files?.[0] ?? null,
                            }))
                          }
                        />
                      </label>
                      <button
                        className="compact-command approve"
                        disabled={
                          candidateDecision.isPending ||
                          !acceptanceFiles[continuityCase.id]
                        }
                        onClick={() =>
                          candidateDecision.mutate({
                            continuityCase,
                            accept: true,
                          })
                        }
                      >
                        <UserCheck size={15} />
                        {t("inventory.continuity.acceptPersonally")}
                      </button>
                      <button
                        className="compact-command"
                        disabled={candidateDecision.isPending}
                        onClick={() =>
                          candidateDecision.mutate({
                            continuityCase,
                            accept: false,
                          })
                        }
                      >
                        <X size={15} />
                        {t("inventory.continuity.decline")}
                      </button>
                    </div>
                  ) : null}
                </article>
              );
            })}
          </div>
        ) : (
          <div className="state">{t("inventory.continuity.empty")}</div>
        )}
      </section>

      {reviewAction ? (
        <div className="modal-backdrop" role="presentation">
          <section
            className="decision-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="custody-continuity-decision-title"
          >
            <header>
              <ShieldCheck size={21} />
              <div>
                <span>{t("inventory.continuity.independentDecision")}</span>
                <h2 id="custody-continuity-decision-title">
                  {reviewAction.approve
                    ? t("inventory.continuity.approveTitle")
                    : t("inventory.continuity.rejectTitle")}
                </h2>
              </div>
            </header>
            {!security.data?.step_up_active ? (
              <label>
                {t("inventory.continuity.totp")}
                <input
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={6}
                  value={totpCode}
                  onChange={(event) =>
                    setTotpCode(event.target.value.replace(/\D/gu, ""))
                  }
                  autoFocus
                />
              </label>
            ) : (
              <p>{t("inventory.continuity.stepUpActive")}</p>
            )}
            {review.error ? (
              <p className="form-error" role="alert">
                {userErrorMessage(review.error)}
              </p>
            ) : null}
            <footer>
              <button
                className="secondary-button"
                onClick={() => {
                  setReviewAction(null);
                  setTotpCode("");
                }}
              >
                {t("inventory.continuity.cancel")}
              </button>
              <button
                className="primary-button"
                disabled={
                  review.isPending ||
                  (!security.data?.step_up_active && !/^[0-9]{6}$/u.test(totpCode))
                }
                onClick={() => review.mutate()}
              >
                {review.isPending ? (
                  <RefreshCw className="spin" size={16} />
                ) : reviewAction.approve ? (
                  <Check size={16} />
                ) : (
                  <X size={16} />
                )}
                {t("inventory.continuity.confirmDecision")}
              </button>
            </footer>
          </section>
        </div>
      ) : null}
    </div>
  );
}
