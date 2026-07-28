import { describe, expect, it } from "vitest";

const businessSources = import.meta.glob(
  ["../*.tsx", "../api/*.ts"],
  { eager: true, import: "default", query: "?raw" },
) as Record<string, string>;

const businessDecimalNames = [
  "amount",
  "amount_reserved",
  "assessed_loss",
  "available",
  "available_input",
  "balance",
  "coverage",
  "cost",
  "critical_minimum",
  "established_loss",
  "executed_amount",
  "handling_cost",
  "held",
  "landed_cost",
  "maximum_per_member",
  "max_loss",
  "outstanding",
  "price",
  "protected_amount",
  "quantity",
  "reserved",
  "shortfall",
  "target_quantity",
  "total_allocated",
  "transport_cost",
  "unit_price",
] as const;

describe("business decimal boundary", () => {
  it("keeps quantity, share, price and coverage fields out of binary floating point", () => {
    const field = businessDecimalNames.join("|");
    const forbidden = new RegExp(
      `(?:Number|parseFloat)\\([^)]*\\.(?:${field})\\b`,
      "g",
    );
    const violations = Object.entries(businessSources).flatMap(([path, source]) =>
      [...source.matchAll(forbidden)].map((match) => `${path}: ${match[0]}`),
    );

    expect(violations).toEqual([]);
  });
});