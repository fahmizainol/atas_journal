// Port of app.py `fmt()`. The API sends "inf"/"-inf" sentinels and null for
// NaN (see api/serialize.py), so render those as ∞ / —.

export type Num = number | "inf" | "-inf" | null | undefined;

export function fmt(x: Num, money = true): string {
  if (x == null) return "—";
  if (x === "inf") return "∞";
  if (x === "-inf") return "-∞";
  const n = x as number;
  const body = n.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return money ? `$${body}` : body;
}

export function fmtPct(x: number | null | undefined, digits = 1): string {
  if (x == null) return "—";
  return `${x.toFixed(digits)}%`;
}

export function fmtInt(x: number | null | undefined): string {
  if (x == null) return "—";
  return Math.round(x).toLocaleString("en-US");
}

// ISO 8601 (already in the display tz from the server) -> "YYYY-MM-DD HH:MM:SS".
export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return iso.replace("T", " ").slice(0, 19);
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return iso.slice(11, 19);
}

export function numValue(x: Num): number {
  if (x === "inf") return Infinity;
  if (x === "-inf") return -Infinity;
  return x == null ? NaN : (x as number);
}
