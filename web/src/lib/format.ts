// Czech number style: narrow no-break space between thousands, comma for decimals, minus sign.
const groups = (digits: string) => digits.replace(/\B(?=(\d{3})+(?!\d))/g, " ");

export function formatNumber(value: number, decimals = 0): string {
  const [whole, fraction] = Math.abs(value).toFixed(decimals).split(".");
  const text = groups(whole) + (fraction ? `,${fraction}` : "");
  return value < 0 && Number(text.replace(/\D/g, "")) !== 0 ? `−${text}` : text;
}

/** An amount in CZK shown in the unit the statement is printed in (tis. Kč for most). */
export function formatAmount(czk: number | null, unit: number | null): string {
  if (czk === null) return "–";
  return formatNumber(czk / (unit || 1));
}

export function unitLabel(unit: number | null): string {
  if (unit === 1_000_000) return "mil. Kč";
  if (unit === 1) return "Kč";
  return "tis. Kč";
}

export function formatRatio(value: number | null, unit: "x" | "%" | "days"): string {
  if (value === null) return "–";
  if (unit === "%") return `${formatNumber(value, 1)} %`;
  if (unit === "days") return `${formatNumber(value, 0)} days`;
  return `${formatNumber(value, 2)}×`;
}

export function formatChange(change: number | null, unit: "x" | "%" | "days"): string {
  if (change === null) return "";
  const sign = change > 0 ? "+" : "";
  if (unit === "%") return `${sign}${formatNumber(change, 1)} pp`;
  if (unit === "days") return `${sign}${formatNumber(change, 0)} days`;
  return `${sign}${formatNumber(change, 2)}`;
}

export function formatDate(iso: string | null): string {
  if (!iso) return "–";
  const [y, m, d] = iso.split("-").map(Number);
  return `${d}. ${m}. ${y}`;
}
