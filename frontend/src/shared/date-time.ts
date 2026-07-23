function activeLocale(): string {
  if (typeof document === "undefined") return "ru-RU";
  return document.documentElement.lang.toLowerCase().startsWith("en") ? "en-US" : "ru-RU";
}

export function formatLocalDateTime(value: string | null, locale = activeLocale()): string {
  if (value === null) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat(locale, {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}