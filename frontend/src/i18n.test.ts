import { beforeEach, describe, expect, it } from "vitest";

import i18n, { getPhraseMap, getValueMap, locales } from "./i18n";

describe("XML locales", () => {
  beforeEach(async () => {
    window.localStorage.clear();
    await i18n.changeLanguage("ru");
  });

  it("discovers Russian and English locale files with the same contracts", () => {
    expect(locales.map((locale) => locale.code)).toEqual(["en", "ru"]);
    const [english, russian] = locales;
    expect(english?.messages["market.title"]).toBe("What do you need?");
    expect(russian?.messages["market.title"]).toBeTruthy();
    expect(russian?.messages["market.title"]).not.toBe(english?.messages["market.title"]);
    expect(getPhraseMap("en")["Администратор безопасности узла"]).toBe("Node security administrator");
    expect(getPhraseMap("en")["Состояние подписанного журнала"]).toBe("Signed journal status");
    expect(getValueMap("ru").AUTH_LOGIN).toBe("Вход");
    expect(getValueMap("en").AUTH_LOGIN).toBe("Sign in");
    expect(Object.keys(russian?.messages ?? {}).sort()).toEqual(
      Object.keys(english?.messages ?? {}).sort(),
    );
    expect(Object.keys(russian?.values ?? {}).sort()).toEqual(
      Object.keys(english?.values ?? {}).sort(),
    );
  });

  it("changes every key-based label when the active language changes", async () => {
    const russianTitle = i18n.t("market.title");
    expect(russianTitle).not.toBe("market.title");
    expect(russianTitle).not.toBe("What do you need?");
    expect(i18n.t("language.en")).toBe("Английский");

    await i18n.changeLanguage("en");
    expect(i18n.t("market.title")).toBe("What do you need?");
    expect(i18n.t("language.ru")).toBe("Russian");
  });
});