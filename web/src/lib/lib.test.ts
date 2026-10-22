import { describe, expect, it } from "vitest";
import type { CheckResult, LineDef, Ratio, ResultView, Value } from "../types";
import { formatAmount, formatChange, formatDate, formatNumber, formatRatio } from "./format";
import { ratioLight, stepSummaries } from "./summary";
import { accountingChecks, confidence, failedFields, valueRows } from "./values";

const value = (over: Partial<Value> = {}): Value => ({
  value: 1000,
  raw: "1",
  page: 1,
  bbox: { x0: 0, y0: 0, x1: 10, y1: 5 },
  ocr_conf: null,
  match_score: 100,
  source: "text_layer",
  needs_review: false,
  candidates: [],
  note: null,
  ...over,
});

const check = (over: Partial<CheckResult>): CheckResult => ({
  name: "Balance: total assets = total liabilities and equity",
  period: "current",
  passed: false,
  severity: "error",
  expected: null,
  actual: null,
  difference: 1000,
  fields: ["total_assets", "total_liabilities_equity"],
  message: "",
  ...over,
});

describe("format", () => {
  it("writes numbers the Czech way", () => {
    expect(formatNumber(29457798)).toBe("29 457 798");
    expect(formatNumber(-17406042)).toBe("−17 406 042");
    expect(formatNumber(0.2227, 2)).toBe("0,22");
    expect(formatNumber(-0.001, 2)).toBe("0,00"); // no "minus zero"
  });
  it("shows amounts in the printed unit", () => {
    expect(formatAmount(29_457_798_000, 1000)).toBe("29 457 798");
    expect(formatAmount(null, 1000)).toBe("–");
  });
  it("formats ratios and changes by unit", () => {
    expect(formatRatio(0.2227, "x")).toBe("0,22×");
    expect(formatRatio(4.97, "%")).toBe("5,0 %");
    expect(formatRatio(46.68, "days")).toBe("47 days");
    expect(formatChange(-11.53, "%")).toBe("−11,5 pp");
    expect(formatChange(0.0063, "x")).toBe("+0,01");
  });
  it("formats dates", () => {
    expect(formatDate("2025-12-31")).toBe("31. 12. 2025");
  });
});

describe("confidence", () => {
  it("trusts the text layer and clean OCR", () => {
    expect(confidence(value())).toBe("high");
    expect(confidence(value({ ocr_conf: 93, source: "ocr" }))).toBe("high");
  });
  it("asks to check middling OCR and to review poor or flagged values", () => {
    expect(confidence(value({ ocr_conf: 80, source: "ocr" }))).toBe("check");
    expect(confidence(value({ ocr_conf: 60, source: "ocr" }))).toBe("review");
    expect(confidence(value({ needs_review: true }))).toBe("review");
    expect(confidence(value(), true)).toBe("review"); // breaks an accounting identity
  });
  it("trusts a value a passing accounting check confirms, unless another check fails", () => {
    const confirmed = value({ ocr_conf: 30, source: "ocr", needs_review: true, confirmed_by: "Assets" });
    expect(confidence(confirmed)).toBe("high");
    expect(confidence(confirmed, true)).toBe("review");
  });
});

describe("checks", () => {
  it("keeps the per-value review notes out of the accounting checks", () => {
    const checks = [check({}), check({ name: "Value needs review (cash)", severity: "warning", fields: ["cash"] })];
    expect(accountingChecks(checks).map((c) => c.name)).toEqual([checks[0].name]);
  });
  it("marks only the values of failed identities", () => {
    const fields = failedFields([
      check({}),
      check({ name: "Negative value where none is expected (cash)", severity: "warning", fields: ["cash"] }),
      check({ name: "passed", passed: true, fields: ["equity"] }),
    ]);
    expect([...fields.keys()].sort()).toEqual(["total_assets:current", "total_liabilities_equity:current"]);
  });
});

describe("rows and summaries", () => {
  const dictionary: LineDef[] = [
    { key: "total_assets", statement: "balance_sheet", side: "assets", code: "", label: "AKTIVA CELKEM", en: "Total assets" },
  ];
  const result = {
    id: "t",
    statements: {
      metadata: { unit_multiplier: 1000 },
      balance_sheet: {
        total_assets: {
          key: "total_assets", code: "", row: "001", label: "AKTIVA CELKEM",
          gross: null, adjustment: null, current: value(), prior: value({ needs_review: true }),
        },
      },
      income_statement: {},
    },
    validation: { checks: [check({ passed: true })], tolerance: 1000 },
    ratios: null, benchmarks: null, report: null, number_check: null,
    stubbed_stages: ["report"], pages: {}, page_sizes: {},
  } as unknown as ResultView;

  it("lists every amount with its English name and confidence", () => {
    const rows = valueRows(result, dictionary);
    expect(rows.map((r) => [r.id, r.name, r.confidence])).toEqual([
      ["total_assets:current", "Total assets", "high"],
      ["total_assets:prior", "Total assets", "review"],
    ]);
  });

  it("summarises each step for the overview", () => {
    const [extraction, validation, , report] = stepSummaries(result, valueRows(result, dictionary));
    expect(extraction).toMatchObject({ headline: "2 values", detail: "1 to review, 0 to check", tone: "bad" });
    expect(validation).toMatchObject({ headline: "1 of 1 checks", tone: "ok" });
    expect(report).toMatchObject({ headline: "not generated", mock: true });
  });

  it("shows benchmark lights only when they belong to the same data as the ratios", () => {
    const ratio = { key: "current_ratio" } as Ratio;
    const benchmarks = { ratios: { current_ratio: { light: "red" } } };
    const view = (stubbed_stages: string[]) => ({ ...result, benchmarks, stubbed_stages }) as unknown as ResultView;
    expect(ratioLight(view(["ratios", "benchmarks"]), ratio)).toBe("red"); // both mock
    expect(ratioLight(view([]), ratio)).toBe("red"); // both computed
    expect(ratioLight(view(["benchmarks"]), ratio)).toBeNull(); // real ratios, mock lights
  });
});
