import type { ErrorInfo } from "react";
import { describe, expect, it } from "vitest";

import AppErrorBoundary, { isOutdatedAssetError, reserveAutomaticReload } from "./AppErrorBoundary";

describe("application asset recovery", () => {
  it("recognizes stale lazy chunks without treating ordinary errors as deploy failures", () => {
    expect(isOutdatedAssetError(new TypeError("Failed to fetch dynamically imported module: /assets/x.js"))).toBe(true);
    expect(isOutdatedAssetError(new Error("Loading chunk 42 failed"))).toBe(true);
    expect(isOutdatedAssetError(new Error("Unable to preload CSS for /assets/x.css"))).toBe(true);
    expect(isOutdatedAssetError(new Error("Request rejected"))).toBe(false);
  });

  it("allows one automatic reload per cooldown window", () => {
    const storage = window.sessionStorage;
    storage.clear();

    expect(reserveAutomaticReload(storage, 100_000)).toBe(true);
    expect(reserveAutomaticReload(storage, 120_000)).toBe(false);
    expect(reserveAutomaticReload(storage, 160_001)).toBe(true);
  });
  it("keeps normal content visible and records ordinary application failures", () => {
    const boundary = new AppErrorBoundary({ children: "ready" });
    expect(boundary.render()).toBe("ready");

    const error = new Error("Request rejected");
    expect(AppErrorBoundary.getDerivedStateFromError(error)).toEqual({
      error,
      autoReloading: false,
    });
    expect(() => boundary.componentDidCatch(error, {} as ErrorInfo)).not.toThrow();
  });
});
