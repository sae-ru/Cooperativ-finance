import { Component, type ErrorInfo, type ReactNode } from "react";
import { RotateCw } from "lucide-react";

import i18n from "./i18n";

const RELOAD_MARKER = "coop.asset-reload-at";
const RELOAD_COOLDOWN_MS = 60_000;

type Props = { children: ReactNode };
type State = { error: Error | null; autoReloading: boolean };

export function isOutdatedAssetError(value: unknown): boolean {
  const message = value instanceof Error ? `${value.name}: ${value.message}` : String(value);
  const normalized = message.toLowerCase();
  return [
    "failed to fetch dynamically imported module",
    "importing a module script failed",
    "error loading dynamically imported module",
    "chunkloaderror",
    "loading chunk",
  ].some((fragment) => normalized.includes(fragment));
}

export function reserveAutomaticReload(storage: Storage, now = Date.now()): boolean {
  try {
    const previous = Number(storage.getItem(RELOAD_MARKER));
    if (Number.isFinite(previous) && now - previous < RELOAD_COOLDOWN_MS) return false;
    storage.setItem(RELOAD_MARKER, String(now));
    return true;
  } catch {
    return false;
  }
}

async function refreshServiceWorkerAndReload(): Promise<void> {
  try {
    if ("serviceWorker" in navigator) {
      const registration = await navigator.serviceWorker.getRegistration();
      await registration?.update();
    }
  } finally {
    window.location.reload();
  }
}

export default class AppErrorBoundary extends Component<Props, State> {
  state: State = { error: null, autoReloading: false };

  static getDerivedStateFromError(error: Error): State {
    return { error, autoReloading: false };
  }

  componentDidCatch(error: Error, _info: ErrorInfo): void {
    if (!isOutdatedAssetError(error) || !reserveAutomaticReload(window.sessionStorage)) return;
    this.setState({ autoReloading: true });
    void refreshServiceWorkerAndReload();
  }

  render() {
    if (this.state.error === null) return this.props.children;

    const isAssetUpdate = isOutdatedAssetError(this.state.error);
    return (
      <main className="app-recovery">
        <section className="app-recovery__content" role="alert">
          <h1>{i18n.t(isAssetUpdate ? "app.updateRequired.title" : "app.failed.title")}</h1>
          <p>{i18n.t(isAssetUpdate ? "app.updateRequired.body" : "app.failed.body")}</p>
          {this.state.autoReloading ? (
            <p className="app-recovery__status">{i18n.t("app.updateRequired.working")}</p>
          ) : (
            <button type="button" className="primary-button" onClick={() => window.location.reload()}>
              <RotateCw aria-hidden="true" size={18} />
              {i18n.t("app.updateRequired.action")}
            </button>
          )}
        </section>
      </main>
    );
  }
}
