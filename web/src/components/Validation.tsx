import { useState } from "react";
import { formatAmount, unitLabel } from "../lib/format";
import { accountingChecks, type ValueRow } from "../lib/values";
import type { ResultView } from "../types";
import { Status } from "./ui";

interface Props {
  result: ResultView;
  rows: ValueRow[];
  onShowValue: (rowId: string) => void;
}

const PERIOD = { current: "Current year", prior: "Prior year", gross: "Gross" } as const;

export function Validation({ result, rows, onShowValue }: Props) {
  const checks = accountingChecks(result.validation?.checks ?? []);
  const failed = checks.filter((c) => !c.passed);
  const [onlyFailed, setOnlyFailed] = useState(failed.length > 0);
  const unit = result.statements.metadata.unit_multiplier;
  const names = new Map(rows.map((r) => [r.key, r.name]));
  const shown = onlyFailed ? failed : checks;
  const errors = failed.filter((c) => c.severity === "error").length;

  if (checks.length === 0) {
    return <div className="empty"><h2>No checks ran</h2><p>Too few lines were read to test any accounting identity.</p></div>;
  }

  return (
    <div className="stack">
      <div className="stat-row">
        <div className="stat"><div className="label">Checks passed</div><div className="value">{checks.length - failed.length} of {checks.length}</div></div>
        <div className="stat"><div className="label">Failed</div><div className="value">{errors}</div></div>
        <div className="stat"><div className="label">Warnings</div><div className="value">{failed.length - errors}</div></div>
      </div>
      <p className="muted" style={{ margin: 0, fontSize: 14 }}>
        Each check tests an identity the statement must satisfy, such as total assets = total liabilities and equity. A
        failed check means at least one of its values was read wrong (or the statement itself does not add up). Sums
        allow ±1 {unitLabel(unit)} for rounding; the same number printed twice must match exactly. Values the parser
        was unsure about are listed under Extraction.
      </p>
      <div className="toolbar" style={{ marginBottom: 0 }}>
        <div className="segmented" role="group" aria-label="Show checks">
          <button type="button" aria-pressed={onlyFailed} onClick={() => setOnlyFailed(true)}>Failed <span className="faint">{failed.length}</span></button>
          <button type="button" aria-pressed={!onlyFailed} onClick={() => setOnlyFailed(false)}>All <span className="faint">{checks.length}</span></button>
        </div>
      </div>
      <div className="table-wrap" style={{ maxHeight: "none" }}>
        <table className="values checks">
          <thead>
            <tr><th>Status</th><th>Check</th><th>Period</th><th className="r">Difference</th><th>Values</th></tr>
          </thead>
          <tbody>
            {shown.map((c, i) => {
              const tone = c.passed ? "ok" : c.severity === "error" ? "bad" : "warn";
              const period = c.period === "gross" || c.period === null ? "current" : c.period;
              return (
                <tr key={i} style={{ cursor: "default" }}>
                  <td><Status tone={tone}>{c.passed ? "Passed" : c.severity === "error" ? "Failed" : "Warning"}</Status></td>
                  <td>{c.name}{c.message && <span className="sub">{c.message}</span>}</td>
                  <td>{c.period ? PERIOD[c.period] : "–"}</td>
                  <td className="r num">{c.difference === null || c.passed ? "–" : formatAmount(c.difference, unit)}</td>
                  <td>
                    <div className="chips">
                      {c.fields.map((f) => (
                        <button key={f} type="button" className="chip" onClick={() => onShowValue(`${f}:${period}`)} title="Show on the page">
                          {names.get(f) ?? f}
                        </button>
                      ))}
                    </div>
                  </td>
                </tr>
              );
            })}
            {shown.length === 0 && <tr><td colSpan={5}><Status tone="ok">Every check passed</Status></td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}
