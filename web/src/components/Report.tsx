import type { ResultView } from "../types";
import { Badge, MockBanner, Status } from "./ui";

const RATING = { low: "ok", medium: "warn", high: "bad" } as const;

export function Report({ result }: { result: ResultView }) {
  const report = result.report;
  if (!report) {
    return (
      <div className="empty">
        <h2>No AI report for this file</h2>
        <p>The report is generated after the numbers are checked (CIPW-16). It was skipped for this run.</p>
      </div>
    );
  }
  const check = result.number_check;
  return (
    <div className="stack">
      {result.stubbed_stages.includes("report") && <MockBanner what="The AI report and its number check" />}
      <section className="card">
        <div className="card-title">
          <h2>Assessment</h2>
          <Badge tone={RATING[report.rating.value]} icon>{report.rating.value[0].toUpperCase() + report.rating.value.slice(1)} risk</Badge>
        </div>
        <p className="verdict" style={{ margin: 0 }}>{report.summary}</p>
        <p className="faint" style={{ fontSize: 13, marginBottom: 0 }}>{report.rating.reasoning}</p>
      </section>
      <div className="grid-3">
        {([
          ["Strengths", report.strengths, "ok"],
          ["Risks", report.risks, "warn"],
          ["Red flags", report.red_flags, "bad"],
        ] as const).map(([title, items, tone]) => (
          <section key={title} className="card">
            <div className="card-title"><h2>{title}</h2><span className="faint">{items.length}</span></div>
            {items.length ? (
              <ul className="list">{items.map((t, i) => <li key={i}><Status tone={tone}>{t}</Status></li>)}</ul>
            ) : (
              <p className="faint" style={{ margin: 0, fontSize: 14 }}>None</p>
            )}
          </section>
        ))}
      </div>
      {check && (
        <section className="card">
          <div className="card-title"><h2>Number check</h2>
            <Status tone={check.passed ? "ok" : "bad"}>{check.passed ? "Every number traced" : "Numbers to verify"}</Status>
          </div>
          <p className="muted" style={{ margin: 0, fontSize: 14 }}>
            {check.numbers_found} numbers in the report were compared with the extracted values and ratios
            {check.unverified.length ? `; ${check.unverified.length} could not be traced.` : "; all of them match."}
          </p>
        </section>
      )}
      {report.data_quality_note && <p className="faint" style={{ margin: 0, fontSize: 13 }}>{report.data_quality_note}</p>}
    </div>
  );
}
