import { Languages, Moon, Sun } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { locales, setInterfaceLanguage } from "./i18n";
import { applyTheme, preferredTheme, type InterfaceTheme } from "./theme";

export default function InterfaceControls({ placement }: { placement: "floating" | "topbar" }) {
  const { t, i18n } = useTranslation();
  const [theme, setTheme] = useState<InterfaceTheme>(preferredTheme);
  const language = i18n.resolvedLanguage ?? i18n.language;
  const nextTheme = theme === "light" ? "dark" : "light";

  function toggleTheme() {
    setTheme(nextTheme);
    applyTheme(nextTheme);
  }

  return (
    <div className={`interface-controls ${placement}`} aria-label={t("common.interfaceSettings")}>
      <label className="language-control" title={t("common.language")}>
        <Languages size={16} aria-hidden="true" />
        <span className="sr-only">{t("common.language")}</span>
        <select
          aria-label={t("common.language")}
          value={language}
          onChange={(event) => setInterfaceLanguage(event.target.value)}
        >
          {locales.map((locale) => (
            <option key={locale.code} value={locale.code}>{t(`language.${locale.code}`, { defaultValue: locale.label })}</option>
          ))}
        </select>
      </label>
      <button
        className="theme-toggle"
        type="button"
        title={nextTheme === "dark" ? t("common.useDarkTheme") : t("common.useLightTheme")}
        aria-label={nextTheme === "dark" ? t("common.useDarkTheme") : t("common.useLightTheme")}
        onClick={toggleTheme}
      >
        {theme === "light" ? <Moon size={17} /> : <Sun size={17} />}
      </button>
    </div>
  );
}