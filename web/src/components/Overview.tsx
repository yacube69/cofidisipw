import { ArrowRight } from "lucide-react";
import { formatRatio } from "../lib/format";
import { ratioLight, type StepId, type StepSummary } from "../lib/summary";
import { accountingChecks, type ValueRow } from "../lib/values";
import type { ResultView } from "../types";
import { MockBadge, Status } from "./ui";

interface Props {
  result: ResultView;
  rows: ValueRow[];
  summaries: StepSummary[];
  onOpen: (step: StepId, rowId?: string) => void;
}

const MAX_CHECKS = 5;

interface Attention {
  text: string;
  tone: "bad" | "warn";
  step: StepId;
  rowId?: string;
}

export function Overview({ result, rows, summaries, onOpen }: Props) {
  // the short list a credit analyst reads first: broken identities, how many values to look at, weak ratios
  const attention: Attention[] = [];
  const failed = accountingChecks(result.validation?.checks ?? []).filter((c) => !c.passed);
  const errors = failed.filter((c) => c.severity === "error");
  for (const check of errors.slice(0, MAX_CHECKS)) {
    attention.push({ text: `${check.name} does not hold`, tone: "bad", step: "validation" });
  }
  if (errors.length > MAX_CHECKS) {
    attention.push({ text: `${errors.length - MAX_CHECKS} more failed checks`, tone: "bad", step: "validation" });
  }
  const warnings = failed.length - errors.length;
  if (warnings) attention.push({ text: `${warnings} plausibility warnings`, tone: "warn", step: "validation" });
  const review = rows.filter((r) => r.confidence === "review");
  if (review.length) {
    attention.push({ text: `${review.length} values to review on the page`, tone: "bad", step: "extraction", rowId: review[0].id });
  }
  for (const ratio of result.ratios?.ratios ?? []) {
    if (ratioLight(result, ratio) === "red") {
      attention.push({ text: `${ratio.name} ${formatRatio(ratio.current.value, ratio.unit)} below the benchmark`, tone: "bad", step: "ratios" });
    }
  }

  return (
    <div className="stack">
      <div className="grid-4">
        {summaries.map((s) => (
          <button key={s.id} type="button" className="card step-card" onClick={() => onOpen(s.id)}>
            <span className="card-title" style={{ marginBottom: 0 }}>
              <Status tone={s.tone}>{s.title}</Status>
              {s.mock && <MockBadge />}
            </span>
            <span className="headline">{s.headline}</span>
            <span className="muted" style={{ fontSize: 13 }}>{s.detail}</span>
            <span className="open">
              Open {s.title.toLowerCase()} <ArrowRight aria-hidden="true" />
            </span>
          </button>
        ))}
      </div>

      <div className="grid-2">
        <section className="card" aria-labelledby="attention-title">
          <div className="card-title">
            <h2 id="attention-title">Needs attention</h2>

          </div>
          {attention.length === 0 ? (
            <p className="muted" style={{ margin: 0 }}>Nothing to look at: every check passed and every value was read cleanly.</p>
          ) : (
            <ul className="list">
              {attention.map((a, i) => (
                <li key={i}>
                  <Status tone={a.tone}>{a.text}</Status>
                  <button type="button" className="link" onClick={() => onOpen(a.step, a.rowId)}>
                    Show
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="card" aria-labelledby="verdict-title">
          <div className="card-title">
            <h2 id="verdict-title">Assessment</h2>
            {result.stubbed_stages.includes("report") && <MockBadge />}
          </div>
          {result.report ? (
            <>
              <p className="verdict" style={{ marginTop: 0 }}>{result.report.summary}</p>
              <button type="button" className="btn" onClick={() => onOpen("report")}>
                Read the full report <ArrowRight aria-hidden="true" size={16} />
              </button>
            </>
          ) : (
            <p className="muted">The AI report was not generated for this file.</p>
          )}
        </section>
      </div>
    </div>
  );
}
