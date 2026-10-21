import type { CheckResult, LineDef, Period, ResultView, Value } from "../types";

export type Confidence = "high" | "check" | "review";

// Same thresholds as the parser's review flag: below 75 the OCR reading is poor, 90+ is clean
export const HIGH_CONF = 90;
export const LOW_CONF = 75;

export const CONFIDENCE_LABEL: Record<Confidence, string> = { high: "Confident", check: "Check", review: "Review" };
export const PERIOD_LABEL: Record<Period, string> = {
  gross: "Gross",
  adjustment: "Adjustment",
  current: "Current year",
  prior: "Prior year",
};

/** Confident: text layer or clean OCR. Review: flagged by the parser or poor OCR. Check: everything between. */
export function confidence(value: Value, inFailedCheck = false): Confidence {
  if (value.needs_review || (value.ocr_conf !== null && value.ocr_conf < LOW_CONF)) return "review";
  if (inFailedCheck) return "review";
  if (value.ocr_conf === null || value.ocr_conf >= HIGH_CONF) return "high";
  return "check";
}

/** "Value needs review (key)" warnings repeat the parser's review flag, which the extraction view already shows. */
export function isReviewNote(check: CheckResult): boolean {
  return check.name.startsWith("Value needs review");
}

/** The accounting checks: identities (errors) and plausibility warnings, without the per-value review notes. */
export function accountingChecks(checks: CheckResult[]): CheckResult[] {
  return checks.filter((c) => !isReviewNote(c));
}

export interface ValueRow {
  id: string; // key:period, unique within a result
  key: string;
  name: string; // English name from the dictionary
  officialLabel: string; // Czech label from the decree
  readLabel: string; // label as read from the page
  code: string;
  statement: "balance_sheet" | "income_statement";
  period: Period;
  value: Value;
  confidence: Confidence;
  failedChecks: string[];
}

const PERIODS: Period[] = ["gross", "adjustment", "current", "prior"];

/** Fields (key, period) in failed accounting identities: a value that breaks an identity must be looked at. */
export function failedFields(checks: CheckResult[]): Map<string, string[]> {
  const out = new Map<string, string[]>();
  for (const check of checks) {
    if (check.passed || check.severity !== "error") continue;
    const period = check.period === "gross" || check.period === null ? "current" : check.period;
    for (const field of check.fields) {
      const id = `${field}:${period}`;
      out.set(id, [...(out.get(id) ?? []), check.name]);
    }
  }
  return out;
}

export function valueRows(result: ResultView, dictionary: LineDef[]): ValueRow[] {
  const defs = new Map(dictionary.map((d) => [d.key, d]));
  const failed = failedFields(result.validation?.checks ?? []);
  const rows: ValueRow[] = [];
  for (const statement of ["balance_sheet", "income_statement"] as const) {
    for (const [key, item] of Object.entries(result.statements[statement])) {
      for (const period of PERIODS) {
        const value = item[period];
        if (!value) continue;
        const id = `${key}:${period}`;
        const checks = failed.get(id) ?? [];
        rows.push({
          id,
          key,
          name: defs.get(key)?.en || key,
          officialLabel: defs.get(key)?.label ?? "",
          readLabel: item.label ?? "",
          code: item.code ?? "",
          statement,
          period,
          value,
          confidence: confidence(value, checks.length > 0),
          failedChecks: checks,
        });
      }
    }
  }
  return rows;
}

export function countBy<T extends string>(items: T[]): Record<T, number> {
  const out = {} as Record<T, number>;
  for (const item of items) out[item] = (out[item] ?? 0) + 1;
  return out;
}
