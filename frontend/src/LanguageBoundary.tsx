import { type ReactNode, useEffect } from "react";
import { useTranslation } from "react-i18next";

import { getPhraseMap, getValueMap } from "./i18n";

const skippedElements = new Set(["CODE", "PRE", "SCRIPT", "STYLE", "TEXTAREA"]);
const translatedAttributes = ["aria-label", "placeholder", "title"] as const;

function escapePattern(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&");
}

function boundedPattern(source: string): RegExp {
  return new RegExp(
    `(^|[^\\p{L}\\p{N}_.-])(${escapePattern(source)})(?=$|[^\\p{L}\\p{N}_.-])`,
    "gu",
  );
}

export function createTranslator(
  phrases: Record<string, string>,
  values: Record<string, string>,
): (value: string) => string {
  const phrasePartials = Object.entries(phrases)
    .filter(([source]) => source.length >= 2)
    .sort(([left], [right]) => right.length - left.length)
    .map(([source, replacement]) => ({ pattern: boundedPattern(source), replacement }));
  const valueEntries = Object.entries(values).sort(([left], [right]) => right.length - left.length);
  const valuePattern = valueEntries.length > 0
    ? new RegExp(
        `(^|[^A-Za-z0-9_.-])(${valueEntries.map(([source]) => escapePattern(source)).join("|")})(?=$|[^A-Za-z0-9_.-])`,
        "gu",
      )
    : null;

  return (value: string) => {
    const exact = phrases[value] ?? values[value];
    if (exact) return exact;

    let translated = value;
    for (const { pattern, replacement } of phrasePartials) {
      translated = translated.replace(pattern, (_match, prefix: string) => `${prefix}${replacement}`);
    }
    if (valuePattern) {
      translated = translated.replace(
        valuePattern,
        (_match, prefix: string, source: string) => `${prefix}${values[source] ?? source}`,
      );
    }
    return translated;
  };
}

function isUserData(element: Element | null): boolean {
  return element?.closest("[data-i18n-ignore]") !== null;
}

export function translateTree(root: ParentNode, translator: (value: string) => string): void {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let textNode = walker.nextNode();
  while (textNode) {
    const parent = textNode.parentElement;
    if (parent && !skippedElements.has(parent.tagName) && !isUserData(parent)) {
      const original = textNode.nodeValue ?? "";
      const core = original.trim();
      if (core) {
        const translated = translator(core);
        if (translated !== core) textNode.nodeValue = original.replace(core, translated);
      }
    }
    textNode = walker.nextNode();
  }

  const elements = root instanceof Element
    ? [root, ...root.querySelectorAll("*")]
    : [...root.querySelectorAll("*")];
  for (const element of elements) {
    if (isUserData(element)) continue;
    for (const name of translatedAttributes) {
      const original = element.getAttribute(name);
      if (!original) continue;
      const translated = translator(original);
      if (translated !== original) element.setAttribute(name, translated);
    }
  }
}

export default function LanguageBoundary({ children }: { children: ReactNode }) {
  const { i18n } = useTranslation();

  useEffect(() => {
    const language = i18n.resolvedLanguage ?? i18n.language;
    const translator = createTranslator(getPhraseMap(language), getValueMap(language));
    const root = document.getElementById("root");
    if (!root) return;

    translateTree(root, translator);
    const observer = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        if (mutation.type === "characterData" && mutation.target.parentNode) {
          translateTree(mutation.target.parentNode, translator);
        }
        for (const node of mutation.addedNodes) {
          if (node instanceof Element) translateTree(node, translator);
          else if (node.parentNode) translateTree(node.parentNode, translator);
        }
      }
    });
    observer.observe(root, { childList: true, subtree: true, characterData: true });
    return () => observer.disconnect();
  }, [i18n, i18n.resolvedLanguage]);

  return children;
}