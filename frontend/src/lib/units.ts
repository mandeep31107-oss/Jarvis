/** Unit conversions mirroring backend/app/services/units.py. Canonical unit is metres. */

export const SQFT_PER_SQM = 10.76391041671;
export const FT_PER_M = 3.280839895013123;

export function m2ToSqft(value: number): number {
  return value * SQFT_PER_SQM;
}
export function sqftToM2(value: number): number {
  return value / SQFT_PER_SQM;
}
export function mToSqm(value: number): number {
  return value;
}
export function sqydToM2(value: number): number {
  return value * 0.83612736;
}
export function m2ToSqyd(value: number): number {
  return value / 0.83612736;
}

const TO_METRES: Record<string, number> = {
  ft: 0.3048,
  in: 0.0254,
  m: 1,
  cm: 0.01,
  yd: 0.9144,
};

export function toMetres(value: number, unit: string): number {
  return value * (TO_METRES[unit] ?? 1);
}
export function fromMetres(value: number, unit: string): number {
  return value / (TO_METRES[unit] ?? 1);
}

export function areaFromM2(value: number, unit: string): number {
  switch (unit) {
    case "sqft":
      return m2ToSqft(value);
    case "sqyd":
    case "gaj":
      return m2ToSqyd(value);
    case "acre":
      return value / 4046.8564224;
    case "cent":
      return value / 40.468564224;
    default:
      return value;
  }
}

export function formatLength(metres: number, unit: "m" | "ft" = "ft", precision = 2): string {
  if (unit === "m") return `${metres.toFixed(precision)} m`;
  const totalInches = metres / 0.0254;
  let feet = Math.floor(totalInches / 12);
  let inches = totalInches - feet * 12;
  if (inches >= 11.75) {
    feet += 1;
    inches = 0;
  }
  return `${feet}'-${Math.round(inches)}"`;
}

export function formatArea(m2: number, unit = "sqft", precision = 0): string {
  const labels: Record<string, string> = {
    sqft: "sq.ft", sqm: "sq.m", sqyd: "sq.yd", gaj: "gaj", acre: "acre", cent: "cent",
  };
  return `${areaFromM2(m2, unit).toLocaleString(undefined, {
    maximumFractionDigits: precision,
    minimumFractionDigits: precision,
  })} ${labels[unit] ?? unit}`;
}

export function formatMoney(value: number, currency = "INR"): string {
  const symbol = currency === "INR" ? "₹" : currency === "USD" ? "$" : `${currency} `;
  if (Math.abs(value) >= 1e7 && currency === "INR") {
    return `${symbol}${(value / 1e7).toFixed(2)} Cr`;
  }
  if (Math.abs(value) >= 1e5 && currency === "INR") {
    return `${symbol}${(value / 1e5).toFixed(1)} L`;
  }
  return `${symbol}${Math.round(value).toLocaleString("en-IN")}`;
}

export function parseDimension(text: string): number | null {
  const t = (text || "").trim().toLowerCase().replace(/[’]/g, "'").replace(/[”]/g, '"');
  if (!t) return null;
  const feetInches = t.match(/^(\d+(?:\.\d+)?)\s*(?:'|ft|feet)?\s*(?:(\d+(?:\.\d+)?)\s*(?:"|in|inches)?)?$/);
  if (feetInches && (t.includes("'") || t.includes('"') || t.includes("ft") || t.includes("in"))) {
    const feet = parseFloat(feetInches[1]);
    const inches = feetInches[2] ? parseFloat(feetInches[2]) : 0;
    return toMetres(feet, "ft") + toMetres(inches, "in");
  }
  const metric = t.match(/^(\d+(?:\.\d+)?)\s*(m|cm|mm)?$/);
  if (metric) {
    const value = parseFloat(metric[1]);
    const unit = metric[2] === "cm" ? "cm" : metric[2] === "mm" ? "in" : "m";
    return metric[2] === "mm" ? value / 1000 : toMetres(value, unit);
  }
  const plain = t.match(/^(\d+(?:\.\d+)?)$/);
  return plain ? parseFloat(plain[1]) : null;
}
