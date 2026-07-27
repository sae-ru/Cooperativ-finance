import { describe, expect, it } from "vitest";

import { AdminApiError } from "../api/admin";
import i18n from "../i18n";
import { userErrorMessage } from "./api-error";

describe("userErrorMessage", () => {
  it("turns amount validation details into an actionable Russian message", () => {
    const message = userErrorMessage(
      new AdminApiError("AMOUNT_INVALID", "a2617076-1c0b-c138-8bf6-c4c32dc401a6", 422),
      "ru",
    );

    expect(message).toBe(
      "Введите сумму больше нуля. Используйте только цифры и разделитель дробной части.",
    );
    expect(message).not.toContain("AMOUNT_INVALID");
    expect(message).not.toContain("a2617076");
  });

  it("uses the selected English locale", () => {
    expect(
      userErrorMessage(new AdminApiError("AMOUNT_INVALID", "request-1", 422), "en"),
    ).toBe("Enter an amount greater than zero. Use digits and a decimal separator only.");
  });

  it("explains why emergency access cannot delegate permanent roles", () => {
    expect(
      userErrorMessage(new AdminApiError("PERMANENT_ROLE_REQUIRED", "request-role", 403), "ru"),
    ).toBe("Для управления постоянными правами нужна обычная, не аварийная роль.");
    expect(
      userErrorMessage(new AdminApiError("PERMANENT_ROLE_REQUIRED", "request-role", 403), "en"),
    ).toBe("A regular, non-emergency role is required to manage permanent access.");
  });

  it("explains permissions and server failures without exposing internal codes", () => {
    expect(
      userErrorMessage(new AdminApiError("RISK_READ_DENIED", "request-2", 403), "ru"),
    ).toBe("У вас нет права выполнять это действие. Обратитесь к администратору кооператива.");
    expect(
      userErrorMessage(new AdminApiError("FEDERATION_UNAVAILABLE", "request-3", 503), "ru"),
    ).toBe("Сервер временно не может выполнить действие. Повторите позже.");
  });

  it("does not expose arbitrary exception messages", () => {
    expect(userErrorMessage(new Error("database-password-leaked"), "ru")).toBe(
      "Не удалось выполнить действие. Проверьте данные и повторите попытку.",
    );
  });
  it("maps validation, authorization, capacity, and availability failures to safe messages", () => {
    const cases: Array<[string, number, string]> = [
      ["MEMBER_IMPORT_PREVIEW_STALE", 409, "errors.memberImportPreviewStale"],
      ["MEMBER_IMPORT_INDEPENDENT_REVIEW_REQUIRED", 409, "errors.memberImportIndependentReview"],
      ["MEMBER_DUPLICATE_REVIEW_REQUIRED", 409, "errors.memberDuplicateReviewRequired"],
      ["MEMBER_IDENTIFIER_EXISTS", 409, "errors.memberIdentifierExists"],
      ["MEMBER_IMPORT_CSV_INVALID", 422, "errors.memberImportInvalid"],
      ["MEMBER_MERGE_INDEPENDENT_REVIEW_REQUIRED", 409, "errors.memberMergeIndependentReview"],
      ["MEMBER_MERGE_CROSS_COOPERATIVE_UNSUPPORTED", 409, "errors.memberMergeCrossCooperative"],
      ["MEMBER_MERGE_EVIDENCE_INVALID", 422, "errors.memberMergeEvidence"],
      ["PERMANENT_MEMBER_MERGE_ROLE_REQUIRED", 403, "errors.memberMergePermanentRole"],
      ["MEMBER_MERGE_CASE_NOT_PENDING", 409, "errors.memberMergeBlocked"],
      ["SERVICE_INDEPENDENT_REVIEW_REQUIRED", 409, "errors.serviceIndependentReview"],      ["SERVICE_NETWORK_ALLOWLIST_INVALID", 422, "errors.serviceNetworkInvalid"],
      ["SERVICE_SCOPES_INVALID", 422, "errors.serviceScopeInvalid"],
      ["SERVICE_EXPIRY_INVALID", 422, "errors.serviceExpiryInvalid"],
      ["SERVICE_RATE_LIMIT_INVALID", 422, "errors.serviceRateInvalid"],
      ["SERVICE_REQUEST_EXPIRED", 409, "errors.serviceRequestExpired"],
      ["PERMANENT_SERVICE_CLIENT_ROLE_REQUIRED", 403, "errors.serviceRoleRequired"],
      ["SERVICE_CLIENT_INACTIVE", 409, "errors.serviceClientInactive"],
      ["STEP_UP_REQUIRED", 403, "errors.stepUpRequired"],
      ["TOTP_NOT_ENROLLED", 409, "errors.totpNotEnrolled"],
      ["TOTP_INVALID_OR_REPLAYED", 422, "errors.totpInvalid"],
      ["TOTP_TEMPORARILY_LOCKED", 429, "errors.totpLocked"],
      ["INDEPENDENT_APPROVAL_REQUIRED", 409, "errors.independentApprovalRequired"],
      ["PERSONAL_ACTOR_REQUIRED", 403, "errors.personalSecurityActorRequired"],
      ["PERMANENT_SECURITY_ROLE_REQUIRED", 403, "errors.personalSecurityActorRequired"],
      ["AUTHENTICATION_FAILED", 401, "errors.authenticationFailed"],
      ["QUANTITY_INVALID", 422, "errors.quantityInvalid"],
      ["EVIDENCE_SIZE_INVALID", 422, "errors.evidenceSizeInvalid"],
      ["EVIDENCE_TYPE_INVALID", 422, "errors.evidenceTypeInvalid"],
      ["EVIDENCE_REQUIRED", 422, "errors.evidenceRequired"],
      ["SESSION_REVOKED", 401, "errors.sessionExpired"],
      ["MEMBER_NOT_FOUND", 404, "errors.notFound"],
      ["VERSION_CONFLICT", 409, "errors.conflict"],
      ["PAYLOAD_TOO_LARGE", 413, "errors.evidenceSizeInvalid"],
      ["RATE_LIMIT_EXCEEDED", 429, "errors.limitExceeded"],
      ["BALANCE_INSUFFICIENT", 422, "errors.insufficient"],
      ["OFFER_EXPIRED", 422, "errors.expired"],
      ["ACCOUNT_FROZEN", 422, "errors.locked"],
      ["FIELD_REQUIRED", 422, "errors.required"],
      ["FIELD_INVALID", 422, "errors.invalidValue"],
      ["PEER_DOWN", 400, "errors.unavailable"],
      ["UNKNOWN_FAILURE", 500, "errors.serverUnavailable"],
    ];

    for (const [code, status, key] of cases) {
      const message = userErrorMessage(new AdminApiError(code, "request-secret", status), "en");
      expect(message, code).toBe(i18n.getFixedT("en")(key));
      expect(message).not.toContain(code);
      expect(message).not.toContain("request-secret");
    }
  });

  it("distinguishes transport failures from malformed and unknown values", () => {
    expect(userErrorMessage(new TypeError("offline"), "en")).toBe(i18n.getFixedT("en")("errors.network"));
    expect(userErrorMessage(null, "en")).toBe(i18n.getFixedT("en")("errors.generic"));
    expect(userErrorMessage({ code: 12, status: "bad" }, "en")).toBe(i18n.getFixedT("en")("errors.generic"));
    expect(userErrorMessage(new AdminApiError("UNKNOWN_CLIENT_FAILURE", "request-unknown", 400), "en"))
      .toBe(i18n.getFixedT("en")("errors.generic"));
  });
});
