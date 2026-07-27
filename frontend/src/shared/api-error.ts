import i18n from "../i18n";

type ApiErrorShape = {
  code: string;
  requestId?: string | null;
  status: number;
};

function asApiError(error: unknown): ApiErrorShape | null {
  if (typeof error !== "object" || error === null) return null;
  const candidate = error as Partial<ApiErrorShape>;
  if (typeof candidate.code !== "string" || typeof candidate.status !== "number") return null;
  return candidate as ApiErrorShape;
}

export function userErrorMessage(error: unknown, language?: string): string {
  const translate = (key: string): string =>
    language ? i18n.getFixedT(language)(key) : i18n.t(key);
  const apiError = asApiError(error);

  if (!apiError) {
    return error instanceof TypeError
      ? translate("errors.network")
      : translate("errors.generic");
  }

  const code = apiError.code.toUpperCase();

  if (code === "MEMBER_IMPORT_PREVIEW_STALE") {
    return translate("errors.memberImportPreviewStale");
  }
  if (code === "MEMBER_IMPORT_INDEPENDENT_REVIEW_REQUIRED") {
    return translate("errors.memberImportIndependentReview");
  }
  if (code === "MEMBER_DUPLICATE_REVIEW_REQUIRED") {
    return translate("errors.memberDuplicateReviewRequired");
  }
  if (code === "MEMBER_IDENTIFIER_EXISTS") {
    return translate("errors.memberIdentifierExists");
  }
  if (
    code.startsWith("MEMBER_IMPORT_")
    && (
      code.includes("FILE")
      || code.includes("CSV")
      || code.includes("HEADER")
      || code.includes("ROW")
    )
  ) {
    return translate("errors.memberImportInvalid");
  }
  if (code === "MEMBER_MERGE_INDEPENDENT_REVIEW_REQUIRED") {
    return translate("errors.memberMergeIndependentReview");
  }
  if (code === "MEMBER_MERGE_CROSS_COOPERATIVE_UNSUPPORTED") {
    return translate("errors.memberMergeCrossCooperative");
  }
  if (code === "MEMBER_MERGE_EVIDENCE_INVALID") {
    return translate("errors.memberMergeEvidence");
  }
  if (code === "PERMANENT_MEMBER_MERGE_ROLE_REQUIRED") {
    return translate("errors.memberMergePermanentRole");
  }
  if (
    code === "MEMBER_MERGE_CASE_NOT_PENDING"
    || code === "MEMBER_MERGE_CONFLICT"
    || code === "MEMBER_MERGE_VERSION_CONFLICT"
  ) {
    return translate("errors.memberMergeBlocked");
  }
  if (code === "SERVICE_INDEPENDENT_REVIEW_REQUIRED") {
    return translate("errors.serviceIndependentReview");
  }
  if (code === "SERVICE_NETWORK_ALLOWLIST_INVALID") {
    return translate("errors.serviceNetworkInvalid");
  }
  if (code === "SERVICE_SCOPES_INVALID" || code === "SERVICE_SCOPE_DENIED") {
    return translate("errors.serviceScopeInvalid");
  }
  if (code === "SERVICE_EXPIRY_INVALID") return translate("errors.serviceExpiryInvalid");
  if (code === "SERVICE_RATE_LIMIT_INVALID") return translate("errors.serviceRateInvalid");
  if (code === "SERVICE_REQUEST_EXPIRED") return translate("errors.serviceRequestExpired");
  if (code === "PERMANENT_SERVICE_CLIENT_ROLE_REQUIRED") {
    return translate("errors.serviceRoleRequired");
  }
  if (
    code === "SERVICE_CLIENT_INACTIVE"
    || code === "SERVICE_CLIENT_NOT_ACTIVE"
    || code === "SERVICE_CLIENT_REVOKED"
    || code === "SERVICE_CLIENT_EXPIRED"
  ) {
    return translate("errors.serviceClientInactive");
  }
  if (code === "STEP_UP_REQUIRED") return translate("errors.stepUpRequired");
  if (code === "TOTP_NOT_ENROLLED") return translate("errors.totpNotEnrolled");
  if (code === "TOTP_INVALID_OR_REPLAYED") return translate("errors.totpInvalid");
  if (code === "TOTP_TEMPORARILY_LOCKED") return translate("errors.totpLocked");
  if (code === "INDEPENDENT_APPROVAL_REQUIRED") {
    return translate("errors.independentApprovalRequired");
  }
  if (code === "PERSONAL_ACTOR_REQUIRED" || code === "PERMANENT_SECURITY_ROLE_REQUIRED") {
    return translate("errors.personalSecurityActorRequired");
  }
  if (code === "PERMANENT_ROLE_REQUIRED") {
    return translate("errors.permanentRoleRequired");
  }
  if (code.includes("AUTHORIZATION") || code.includes("DENIED") || apiError.status === 403) {
    return translate("errors.permissionDenied");
  }
  if (code.includes("AUTHENTICATION") || code.includes("CREDENTIAL")) {
    return translate("errors.authenticationFailed");
  }
  if (code.includes("AMOUNT") && code.includes("INVALID")) {
    return translate("errors.amountInvalid");
  }
  if (code.includes("QUANTITY") && code.includes("INVALID")) {
    return translate("errors.quantityInvalid");
  }
  if (code.includes("EVIDENCE") && code.includes("SIZE")) {
    return translate("errors.evidenceSizeInvalid");
  }
  if (code.includes("EVIDENCE") && code.includes("TYPE")) {
    return translate("errors.evidenceTypeInvalid");
  }
  if (code.includes("EVIDENCE") && code.includes("REQUIRED")) {
    return translate("errors.evidenceRequired");
  }
  if (apiError.status === 401) return translate("errors.sessionExpired");
  if (apiError.status === 404 || code.endsWith("_NOT_FOUND")) {
    return translate("errors.notFound");
  }
  if (apiError.status === 409 || code.includes("CONFLICT") || code.includes("ALREADY_EXISTS")) {
    return translate("errors.conflict");
  }
  if (apiError.status === 413) return translate("errors.evidenceSizeInvalid");
  if (apiError.status === 429 || code.includes("LIMIT_EXCEEDED")) {
    return translate("errors.limitExceeded");
  }
  if (apiError.status >= 500) return translate("errors.serverUnavailable");
  if (code.includes("INSUFFICIENT")) return translate("errors.insufficient");
  if (code.includes("EXPIRED")) return translate("errors.expired");
  if (code.includes("LOCKED") || code.includes("FROZEN")) return translate("errors.locked");
  if (code.endsWith("_REQUIRED")) return translate("errors.required");
  if (code.endsWith("_INVALID") || apiError.status === 422) {
    return translate("errors.invalidValue");
  }
  if (code.includes("UNAVAILABLE") || code.endsWith("_DOWN")) {
    return translate("errors.unavailable");
  }

  return translate("errors.generic");
}