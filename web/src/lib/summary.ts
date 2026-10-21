import type { Light, Ratio, ResultView } from "../types";
import { accountingChecks, type ValueRow } from "./values";

export type StepId = "overview" | "extraction" | "validation" | "ratios" | "report";
export type Tone = "ok" | "warn" | "bad" | "neutral";

export interface StepSummary {
  id: Exclude<StepId, "overview">;
  title: string;
  headline: string;
  detail: string;
  tone: Tone;
  mock: boolean;
}

export function ratioLight(result: ResultView, ratio: Ratio): Light | null {
  // mock lights belong to the mock company: next to this file's own ratios they would mislead
  const stub = result.stubbed_stages;
  if (stub.includes("benchmarks") && !stub.includes("ratios")) return null;
  return result.benchmarks?.ratios?.[ratio.key]?.light ?? null;
}

export function stepSummaries(result: ResultView, rows: ValueRow[]): StepSummary[] {
  const stub = new Set(result.stubbed_stages);
  const review = rows.filter((r) => r.confidence === "review").length;
  const check = rows.filter((r) => r.confidence === "check").length;
  const checks = accountingChecks(result.validation?.checks ?? []);
  const errors = checks.filter((c) => !c.passed && c.severity === "error").length;
  const warnings = checks.filter((c) => !c.passed && c.severity === "warning").length;
  const passed = checks.filter((c) => c.passed).length;
  const ratios = result.ratios?.ratios ?? [];
  const lights = ratios.map((r) => ratioLight(result, r)).filter((l): l is Light => l !== null);
  const red = lights.filter((l) => l === "red").length;
  const rating = result.report?.rating?.value;

  return [
    {
      id: "extraction",
      title: "Extraction",
      headline: `${rows.length} values`,
      detail: review ? `${review} to review, ${check} to check` : check ? `${check} to check` : "all read cleanly",
      tone: review ? "bad" : check ? "warn" : "ok",
      mock: false,
    },
    {
      id: "validation",
      title: "Validation",
      headline: checks.length ? `${passed} of ${checks.length} checks` : "no checks",
      detail: errors
        ? `${errors} failed${warnings ? `, ${warnings} warnings` : ""}`
        : warnings
          ? `${warnings} warnings`
          : "every identity holds",
      tone: errors ? "bad" : warnings ? "warn" : checks.length ? "ok" : "neutral",
      mock: false,
    },
    {
      id: "ratios",
      title: "Ratios",
      headline: `${ratios.length} ratios`,
      detail: lights.length ? `${red} red of ${lights.length} benchmarked` : "no benchmarks yet",
      tone: red ? "bad" : lights.length ? "ok" : "neutral",
      mock: stub.has("ratios") || stub.has("bankruptcy"),
    },
    {
      id: "report",
      title: "AI report",
      headline: rating ? `${rating[0].toUpperCase()}${rating.slice(1)} risk` : "not generated",
      detail: result.number_check ? (result.number_check.passed ? "every number checked" : "numbers to verify") : "",
      tone: rating === "high" ? "bad" : rating === "medium" ? "warn" : rating === "low" ? "ok" : "neutral",
      mock: stub.has("report"),
    },
  ];
}
