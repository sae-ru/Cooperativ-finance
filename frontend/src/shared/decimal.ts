export type DecimalInput = string;

type ParsedDecimal = {
  coefficient: bigint;
  scale: number;
};

type DecimalFormatOptions = {
  minimumFractionDigits?: number;
  maximumFractionDigits?: number;
  useGrouping?: boolean;
};

const DECIMAL_PATTERN = /^([+-])?(\d+)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/;
const LOCALE_MARKS = new Map<string, { decimal: string; group: string; minus: string }>();

function powerOfTen(exponent: number): bigint {
  if (!Number.isSafeInteger(exponent) || exponent < 0 || exponent > 10_000) {
    throw new RangeError("Decimal scale is outside the supported range.");
  }
  return 10n ** BigInt(exponent);
}

function parseDecimal(value: DecimalInput): ParsedDecimal {
  const text = value.trim();
  const match = DECIMAL_PATTERN.exec(text);
  if (!match) throw new TypeError(`Invalid decimal value: ${text}`);

  const negative = match[1] === "-";
  const integer = match[2] ?? "0";
  const fraction = match[3] ?? "";
  const exponent = Number(match[4] ?? "0");
  if (!Number.isSafeInteger(exponent)) throw new TypeError(`Invalid decimal exponent: ${text}`);

  let coefficient = BigInt(`${integer}${fraction}` || "0");
  let scale = fraction.length - exponent;
  if (scale < 0) {
    coefficient *= powerOfTen(-scale);
    scale = 0;
  }
  if (negative && coefficient !== 0n) coefficient = -coefficient;
  return normalize({ coefficient, scale });
}

function normalize(value: ParsedDecimal): ParsedDecimal {
  let { coefficient, scale } = value;
  while (scale > 0 && coefficient % 10n === 0n) {
    coefficient /= 10n;
    scale -= 1;
  }
  return { coefficient, scale };
}

function align(left: ParsedDecimal, right: ParsedDecimal): [bigint, bigint, number] {
  const scale = Math.max(left.scale, right.scale);
  return [
    left.coefficient * powerOfTen(scale - left.scale),
    right.coefficient * powerOfTen(scale - right.scale),
    scale,
  ];
}

function toCanonical(value: ParsedDecimal): string {
  const normalized = normalize(value);
  const negative = normalized.coefficient < 0n;
  const digits = (negative ? -normalized.coefficient : normalized.coefficient).toString();
  if (normalized.scale === 0) return `${negative ? "-" : ""}${digits}`;
  const padded = digits.padStart(normalized.scale + 1, "0");
  const split = padded.length - normalized.scale;
  return `${negative ? "-" : ""}${padded.slice(0, split)}.${padded.slice(split)}`;
}

function roundToScale(value: ParsedDecimal, scale: number): ParsedDecimal {
  if (!Number.isSafeInteger(scale) || scale < 0 || scale > 10_000) {
    throw new RangeError("Decimal display scale is outside the supported range.");
  }
  if (value.scale <= scale) return value;

  const divisor = powerOfTen(value.scale - scale);
  const negative = value.coefficient < 0n;
  const absolute = negative ? -value.coefficient : value.coefficient;
  let quotient = absolute / divisor;
  const remainder = absolute % divisor;
  if (remainder * 2n >= divisor) quotient += 1n;
  return normalize({ coefficient: negative ? -quotient : quotient, scale });
}

function localeMarks(locale: string): { decimal: string; group: string; minus: string } {
  const cached = LOCALE_MARKS.get(locale);
  if (cached) return cached;
  const parts = new Intl.NumberFormat(locale).formatToParts(-12345.6);
  const marks = {
    decimal: parts.find((part) => part.type === "decimal")?.value ?? ".",
    group: parts.find((part) => part.type === "group")?.value ?? ",",
    minus: parts.find((part) => part.type === "minusSign")?.value ?? "-",
  };
  LOCALE_MARKS.set(locale, marks);
  return marks;
}

function groupInteger(value: string, separator: string): string {
  return value.replace(/\B(?=(\d{3})+(?!\d))/g, separator);
}

export function requireDecimalString(value: unknown): DecimalInput {
  if (typeof value !== "string") {
    throw new TypeError("Business decimal values must be encoded as strings.");
  }
  return value;
}

export function decimalAdd(...values: DecimalInput[]): string {
  let result: ParsedDecimal = { coefficient: 0n, scale: 0 };
  for (const value of values) {
    const [left, right, scale] = align(result, parseDecimal(value));
    result = normalize({ coefficient: left + right, scale });
  }
  return toCanonical(result);
}

export function decimalSubtract(left: DecimalInput, ...rights: DecimalInput[]): string {
  return decimalAdd(left, ...rights.map((value) => decimalNegate(value)));
}

export function decimalNegate(value: DecimalInput): string {
  const parsed = parseDecimal(value);
  return toCanonical({ coefficient: -parsed.coefficient, scale: parsed.scale });
}

export function decimalCompare(left: DecimalInput, right: DecimalInput): -1 | 0 | 1 {
  const [leftCoefficient, rightCoefficient] = align(parseDecimal(left), parseDecimal(right));
  if (leftCoefficient < rightCoefficient) return -1;
  if (leftCoefficient > rightCoefficient) return 1;
  return 0;
}

export function decimalMin(...values: DecimalInput[]): string {
  if (values.length === 0) throw new TypeError("decimalMin requires at least one value.");
  const minimum = values.slice(1).reduce<DecimalInput>(
    (current, value) => decimalCompare(value, current) < 0 ? value : current,
    values[0]!,
  );
  return toCanonical(parseDecimal(minimum));
}

export function decimalIsPositive(value: DecimalInput): boolean {
  return decimalCompare(value, "0") > 0;
}

export function decimalIsNegative(value: DecimalInput): boolean {
  return decimalCompare(value, "0") < 0;
}

export function formatDecimal(
  value: DecimalInput,
  locale: string,
  options: DecimalFormatOptions = {},
): string {
  const maximum = options.maximumFractionDigits;
  let parsed = parseDecimal(value);
  if (maximum !== undefined) parsed = roundToScale(parsed, maximum);

  const canonical = toCanonical(parsed);
  const negative = canonical.startsWith("-");
  const unsigned = negative ? canonical.slice(1) : canonical;
  const [integer = "0", rawFraction = ""] = unsigned.split(".");
  const minimum = options.minimumFractionDigits ?? 0;
  const fraction = rawFraction.padEnd(minimum, "0");
  const marks = localeMarks(locale);
  const grouped = options.useGrouping === false ? integer : groupInteger(integer, marks.group);
  return `${negative ? marks.minus : ""}${grouped}${fraction ? `${marks.decimal}${fraction}` : ""}`;
}
