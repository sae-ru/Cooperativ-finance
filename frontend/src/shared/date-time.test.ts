import { describe, expect, it } from "vitest";

import { formatLocalDateTime } from "./date-time";

describe("formatLocalDateTime", () => {
  it("returns a neutral mark for missing or invalid time", () => {
    expect(formatLocalDateTime(null)).toBe("—");
    expect(formatLocalDateTime("not-a-date")).toBe("—");
  });

  it("formats a valid instant", () => {
    expect(formatLocalDateTime("2026-07-20T10:30:15Z", "en-GB")).toContain("20/07/2026");
  });
});
