import { describe, expect, it } from "vitest";

import {
  decimalAdd,
  decimalCompare,
  decimalIsNegative,
  decimalIsPositive,
  decimalMin,
  decimalSubtract,
  formatDecimal,
  requireDecimalString,
} from "./decimal";

describe("exact decimal arithmetic", () => {
  it("does not inherit binary floating-point errors", () => {
    expect(decimalAdd("0.1", "0.2")).toBe("0.3");
    expect(decimalSubtract("1", "0.1", "0.2")).toBe("0.7");
  });

  it("preserves numeric(38,12) boundaries and carries exactly", () => {
    expect(
      decimalAdd("99999999999999999999999999.999999999999", "0.000000000001"),
    ).toBe("100000000000000000000000000");
  });

  it("compares and selects signed values without Number conversion", () => {
    expect(decimalCompare("-0.000000000001", "0")).toBe(-1);
    expect(decimalMin("9007199254740993.1", "9007199254740993.01")).toBe(
      "9007199254740993.01",
    );
    expect(decimalIsPositive("0.000000000001")).toBe(true);
    expect(decimalIsNegative("-1")).toBe(true);
  });

  it("formats Russian and English output without losing large digits", () => {
    const ru = formatDecimal("12345678901234567890.125", "ru-RU", {
      maximumFractionDigits: 2,
    });
    const en = formatDecimal("12345678901234567890.125", "en-US", {
      maximumFractionDigits: 2,
    });
    expect(ru.replace(/\s/g, " ")).toBe("12 345 678 901 234 567 890,13");
    expect(en).toBe("12,345,678,901,234,567,890.13");
  });

  it("supports exponent notation only through exact decimal expansion", () => {
    expect(decimalAdd("1e-12", "2E+3")).toBe("2000.000000000001");
  });

  it("rejects non-decimal input", () => {
    expect(() => decimalAdd("1", "NaN")).toThrow(TypeError);
    expect(() => formatDecimal("12 shares", "en-US")).toThrow(TypeError);
    expect(() => decimalAdd("1,2")).toThrow(TypeError);
    expect(() => requireDecimalString(0.1)).toThrow(TypeError);
  });
});
