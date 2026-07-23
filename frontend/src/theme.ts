export type InterfaceTheme = "light" | "dark";

export function preferredTheme(): InterfaceTheme {
  const stored = window.localStorage.getItem("coop.theme");
  if (stored === "light" || stored === "dark") return stored;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function applyTheme(theme: InterfaceTheme): void {
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  window.localStorage.setItem("coop.theme", theme);
}

applyTheme(preferredTheme());