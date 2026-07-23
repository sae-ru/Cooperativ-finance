import { describe, expect, it } from "vitest";

import { getPhraseMap, getValueMap } from "./i18n";
import { createTranslator } from "./LanguageBoundary";

describe("LanguageBoundary translator", () => {
  it("renders Russian labels for machine values without changing identifiers", () => {
    const translate = createTranslator(getPhraseMap("ru"), getValueMap("ru"));

    expect(translate("ACTIVE")).toBe("Действует");
    expect(translate("WARNING · LOW")).toBe("Предупреждение · Низкий");
    expect(translate("Статус: AUTH_LOGIN · SUCCESS")).toBe("Статус: Вход · Успешно");
    expect(translate("NODE-ACTIVE-001")).toBe("NODE-ACTIVE-001");
    expect(translate("CustomSnapshotToken")).toBe("CustomSnapshotToken");
    expect(translate("OfferIndexSnapshot")).toBe("Снимок индекса предложений");
    expect(translate("clearing.cycle_reconciled")).toBe("Клиринг: цикл сверен");
    expect(translate("Fresh white cabbage from the local cooperative warehouse"))
      .toBe("Свежая белокочанная капуста с местного склада кооператива");
  });

  it("renders English labels for legacy Russian text and machine values", () => {
    const translate = createTranslator(getPhraseMap("en"), getValueMap("en"));

    expect(translate("Администратор безопасности узла")).toBe("Node security administrator");
    expect(translate("AUTHORIZATION_DENIED · DENIED")).toBe("Authorization denied · Denied");
    expect(translate("Anna Petrova · до 20.08.2026")).toBe("Anna Petrova · until 20.08.2026");
  });
});