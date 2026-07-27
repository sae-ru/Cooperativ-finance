import { describe, expect, it } from "vitest";

import { getPhraseMap, getValueMap } from "./i18n";
import { createTranslator, translateTree } from "./LanguageBoundary";

describe("LanguageBoundary translator", () => {
  it("renders Russian labels for machine values without changing identifiers", () => {
    const translate = createTranslator(getPhraseMap("ru"), getValueMap("ru"));

    expect(translate("Emergency continuity warehouse, receiving desk"))
      .toBe("Склад аварийной преемственности, зона приёмки");
    expect(translate("Dry room")).toBe("Сухое помещение");


    expect(translate("ACTIVE")).toBe("Действует");
    expect(translate("WARNING · LOW")).toBe("Предупреждение · Низкий");
    expect(translate("Статус: AUTH_LOGIN · SUCCESS")).toBe("Статус: Вход · Успешно");
    expect(translate("NODE-ACTIVE-001")).toBe("NODE-ACTIVE-001");
    expect(translate("CustomSnapshotToken")).toBe("CustomSnapshotToken");
    expect(translate("OfferIndexSnapshot")).toBe("Снимок индекса предложений");
    expect(translate("clearing.cycle_reconciled")).toBe("Клиринг: цикл сверен");
    expect(translate("Fresh white cabbage from the local cooperative warehouse"))
      .toBe("Свежая белокочанная капуста с местного склада кооператива");
    expect(translate("Galvanized steel nails, 100 millimetres"))
      .toBe("Оцинкованные стальные гвозди, 100 миллиметров");
  });

  it("renders English labels for legacy Russian text and machine values", () => {
    const translate = createTranslator(getPhraseMap("en"), getValueMap("en"));

    expect(translate("Капуста свежая · DEMO-EMERGENCY-001"))
      .toBe("Fresh cabbage · DEMO-EMERGENCY-001");

    expect(translate("Администратор безопасности узла")).toBe("Node security administrator");
    expect(translate("AUTHORIZATION_DENIED · DENIED")).toBe("Authorization denied · Denied");
    expect(translate("Anna Petrova · до 20.08.2026")).toBe("Anna Petrova · until 20.08.2026");
  });

  it("does not translate participant data marked as user content", () => {
    const root = document.createElement("div");
    root.innerHTML = '<span data-i18n-ignore="true">Склад</span><span>Склад</span>';

    translateTree(root, createTranslator(getPhraseMap("en"), getValueMap("en")));

    expect(root.children[0]).toHaveTextContent("Склад");
    expect(root.children[1]).toHaveTextContent("Inventory");
  });
});