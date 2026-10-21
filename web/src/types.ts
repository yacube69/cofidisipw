// The result view served by GET /api/results/{id}: src/result.py `Result` without `page_words`, plus `id`,
// `page_sizes` and `stubbed_stages` (see src/api.py). Keep in sync with src/schema.py and src/result.py.

export type Period = "gross" | "adjustment" | "current" | "prior";
export type Source = "text_layer" | "ocr";
export type StageStatus = "ok" | "stub" | "skipped" | "error";

export interface BBox {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface Value {
  value: number | null; // CZK
  raw: string | null;
  page: number | null;
  bbox: BBox | null; // PDF points, top-left origin
  ocr_conf: number | null; // 0-100, null for a text layer
  match_score: number | null; // 0-100
  source: Source | null;
  needs_review: boolean;
  candidates: number[];
  note: string | null;
}

export interface LineItem {
  key: string;
  code: string | null;
  row: string | null;
  label: string | null;
  gross: Value | null;
  adjustment: Value | null;
  current: Value | null;
  prior: Value | null;
}

export interface Metadata {
  company_name: string | null;
  ico: string | null;
  period_end: string | null;
  prior_period_end: string | null;
  scope: "full" | "abbreviated" | null;
  accounting_standard: string;
  currency: string;
  unit_multiplier: number | null;
  source_file: string | null;
  statement_pages: Record<string, number[]>;
  extraction_source: Record<string, Source>;
}

export interface FinancialStatements {
  metadata: Metadata;
  balance_sheet: Record<string, LineItem>;
  income_statement: Record<string, LineItem>;
}

export interface CheckResult {
  name: string;
  period: "current" | "prior" | "gross" | null;
  passed: boolean;
  severity: "error" | "warning";
  expected: number | null;
  actual: number | null;
  difference: number | null;
  fields: string[];
  message: string;
}

export interface ValidationReport {
  checks: CheckResult[];
  tolerance: number;
}

export interface RatioValue {
  value: number | null;
  flag: "ok" | "unreliable" | "not_meaningful" | "missing";
  reason: string | null;
  inputs: Record<string, number | null>;
}

export interface Ratio {
  key: string;
  name: string;
  group: "liquidity" | "profitability" | "leverage" | "activity";
  formula: string;
  unit: "x" | "%" | "days";
  current: RatioValue;
  prior: RatioValue;
}

export type Light = "green" | "amber" | "red";
export type Zone = "safe" | "grey" | "distress";
export type Rating = "low" | "medium" | "high";

// bankruptcy, benchmarks, report and number_check are free-form dicts in Python until CIPW-13/14/16/17 fix them
export interface Bankruptcy {
  mock?: boolean;
  in05?: { score: number; zone: Zone };
  altman_z_prime?: { score: number; zone: Zone };
  note?: string;
}

export interface Benchmarks {
  mock?: boolean;
  source?: string;
  ratios: Record<string, { value: number; green_min: number; amber_min: number; light: Light }>;
}

export interface Report {
  mock?: boolean;
  summary: string;
  strengths: string[];
  risks: string[];
  red_flags: string[];
  rating: { value: Rating; reasoning: string };
  data_quality_note?: string;
}

export interface NumberCheck {
  mock?: boolean;
  numbers_found: number;
  unverified: unknown[];
  passed: boolean;
}

export interface StageRun {
  stage: string;
  status: StageStatus;
  seconds: number;
  note: string | null;
}

export interface ResultView {
  id: string;
  tool_version: string;
  file: string;
  created_at: string;
  pages: Record<string, number[]>;
  consolidation: "individual" | "consolidated" | null;
  page_sizes: Record<string, [number, number]>;
  statements: FinancialStatements;
  validation: ValidationReport | null;
  ratios: { ratios: Ratio[] } | null;
  bankruptcy: Bankruptcy | null;
  benchmarks: Benchmarks | null;
  report: Report | null;
  number_check: NumberCheck | null;
  stages: StageRun[];
  stubbed_stages: string[];
  errors: string[];
  warnings: string[];
}

export interface LineDef {
  key: string;
  statement: "balance_sheet" | "income_statement";
  side: "assets" | "liabilities" | null;
  code: string;
  label: string; // official Czech label
  en: string;
}

export interface Sample {
  id: string;
  label: string;
}
