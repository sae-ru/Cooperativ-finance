import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import localeFiles from "virtual:coop-locales";

export type LocaleDefinition = {
  code: string;
  label: string;
  messages: Record<string, string>;
  phrases: Record<string, string>;
  values: Record<string, string>;
};

function decodeXml(value: string): string {
  return value
    .replace(/^<!\[CDATA\[([\s\S]*?)\]\]>$/u, "$1")
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&quot;", '"')
    .replaceAll("&apos;", "'")
    .replaceAll("&amp;", "&");
}

function attribute(source: string, name: string): string | null {
  const match = source.match(new RegExp(`${name}="([^"]+)"`, "u"));
  return match?.[1] ? decodeXml(match[1]) : null;
}

function parseLocale(xml: string, path: string): LocaleDefinition {
  const header = xml.match(/<locale\b([^>]*)>/u)?.[1] ?? "";
  const code = attribute(header, "code") ?? path.split(/[\\/]/u).pop()?.replace(/\.xml$/u, "") ?? "ru";
  const label = attribute(header, "label") ?? code.toUpperCase();
  const messages: Record<string, string> = {};
  const phrases: Record<string, string> = {};
  const values: Record<string, string> = {};

  for (const match of xml.matchAll(/<message\b([^>]*)>([\s\S]*?)<\/message>/gu)) {
    const key = attribute(match[1] ?? "", "key");
    if (key) messages[key] = decodeXml((match[2] ?? "").trim());
  }
  for (const match of xml.matchAll(/<phrase\b([^>]*)>([\s\S]*?)<\/phrase>/gu)) {
    const source = attribute(match[1] ?? "", "source");
    if (source) phrases[source] = decodeXml((match[2] ?? "").trim());
  }
  for (const match of xml.matchAll(/<value\b([^>]*)>([\s\S]*?)<\/value>/gu)) {
    const source = attribute(match[1] ?? "", "code");
    if (source) values[source] = decodeXml((match[2] ?? "").trim());
  }

  return { code, label, messages, phrases, values };
}

export const locales = Object.entries(localeFiles)
  .map(([path, xml]) => parseLocale(xml, path))
  .sort((left, right) => left.code.localeCompare(right.code));

if (!locales.some((locale) => locale.code === "ru")) {
  throw new Error("The required lang/ru.xml locale is missing");
}

const resources = Object.fromEntries(
  locales.map((locale) => [locale.code, { translation: locale.messages }]),
);

function preferredLanguage(): string {
  if (typeof window === "undefined") return "ru";
  const stored = window.localStorage.getItem("coop.language");
  if (stored && locales.some((locale) => locale.code === stored)) return stored;
  const browserLanguage = window.navigator.language.toLowerCase().split("-")[0] ?? "ru";
  return locales.some((locale) => locale.code === browserLanguage) ? browserLanguage : "ru";
}

void i18n.use(initReactI18next).init({
  resources,
  lng: preferredLanguage(),
  fallbackLng: "ru",
  keySeparator: false,
  interpolation: { escapeValue: false },
  initAsync: false,
  returnNull: false,
});

export function setInterfaceLanguage(code: string): void {
  if (!locales.some((locale) => locale.code === code)) return;
  window.localStorage.setItem("coop.language", code);
  document.documentElement.lang = code;
  window.location.reload();
}

export function getPhraseMap(code = i18n.resolvedLanguage ?? i18n.language): Record<string, string> {
  return locales.find((locale) => locale.code === code)?.phrases ?? {};
}

export function getValueMap(code = i18n.resolvedLanguage ?? i18n.language): Record<string, string> {
  return locales.find((locale) => locale.code === code)?.values ?? {};
}

document.documentElement.lang = i18n.resolvedLanguage ?? i18n.language;

export default i18n;