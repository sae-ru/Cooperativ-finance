import { describe, expect, it } from "vitest";

import { AdminApiError } from "../api/admin";
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
});