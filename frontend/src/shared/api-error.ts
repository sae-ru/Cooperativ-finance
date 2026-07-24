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