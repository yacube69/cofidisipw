import { Info, X } from "lucide-react";
import { useRef, type ReactNode } from "react";
import { formatAmount, formatNumber, unitLabel } from "../lib/format";
import { PERIOD_LABEL } from "../lib/values";
import type { ResultView } from "../types";
import { Status } from "./ui";

const STAGE_TONE = { ok: "ok", stub: "neutral", skipped: "neutral", error: "bad" } as const;
const SOURCE = { ares: "ARES, asked now", cache: "ARES, saved answer", snapshot: "dataset snapshot (offline)" } as const;

function when(iso: string | null): string {
  if (!iso) return "–";
  return new Date(iso).toLocaleString("cs-CZ", { dateStyle: "medium", timeStyle: "medium" });
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </>
  );
}

/** "About this result": the audit block (CIPW-45), so anyone can see what produced the numbers. */
export function About({ result }: { result: ResultView }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const a = result.audit;
  const unit = result.statements.metadata.unit_multiplier;
  const seconds = a?.finished_at ? (Date.parse(a.finished_at) - Date.parse(a.started_at)) / 1000 : null;

  return (
    <>
      <button type="button" className="btn ghost" onClick={() => dialog.current?.showModal()}>
        <Info aria-hidden="true" size={16} />
        About this result
      </button>
      <dialog ref={dialog} className="about" aria-labelledby="about-title" onClick={(e) => e.target === dialog.current && dialog.current?.close()}>
        <div className="card-title">
          <h2 id="about-title">About this result</h2>
          <button type="button" className="btn ghost" aria-label="Close" onClick={() => dialog.current?.close()}>
            <X aria-hidden="true" size={18} />
          </button>
        </div>
        {!a ? (
          <p className="muted">This result was saved before Fides recorded an audit block, so its origin is unknown.</p>
        ) : (
          <>
            <p className="muted" style={{ marginTop: 0, fontSize: 13 }}>
              Everything needed to reproduce and explain this result. Two results with the same settings, dictionary and
              code version give the same numbers.
            </p>
            <dl className="audit">
              <Row label="Run">{a.run_id} · {when(a.started_at)}{seconds !== null && ` · ${formatNumber(seconds, 1)} s`}</Row>
              {a.user && <Row label="Analysed by">{a.user}</Row>}
              <Row label="Fides version">
                {a.tool_version}
                {a.git_describe && <> · <span className="mono">{a.git_describe}</span></>}
                {a.git_dirty && <span className="faint"> (uncommitted changes)</span>}
              </Row>
              <Row label="Commit"><span className="mono">{a.git_commit ?? "unknown"}</span></Row>
              <Row label="Settings"><span className="mono">{a.settings_hash}</span> <span className="faint">(config/fides.yaml)</span></Row>
              <Row label="Line dictionary"><span className="mono">{a.dictionary_version}</span></Row>
              <Row label="OCR">{a.ocr_config ?? "–"}{a.tesseract_version && <span className="faint"> · Tesseract {a.tesseract_version}</span>}</Row>
              <Row label="AI model">
                {a.offline ? "Offline mode: no LLM call" : a.model ? <>{a.model}{a.prompt_version && <span className="faint"> · prompt {a.prompt_version}</span>}</> : "No LLM call for this result"}
              </Row>
              <Row label="Python">{a.python_version}</Row>
              <Row label="Run log"><span className="mono">{a.log_file ?? "not written"}</span></Row>
              <Row label="Manual corrections">
                {a.corrections.length === 0 ? "None" : (
                  <ul className="plain">
                    {a.corrections.map((c, i) => (
                      <li key={i}>
                        {c.key} ({PERIOD_LABEL[c.period].toLowerCase()}):{" "}
                        {c.old === c.new
                          ? `confirmed ${formatAmount(c.new, unit)}`
                          : `${formatAmount(c.old, unit)} → ${formatAmount(c.new, unit)}`} {unitLabel(unit)}
                        {c.by && `, by ${c.by}`}, {when(c.at)}
                      </li>
                    ))}
                  </ul>
                )}
              </Row>
            </dl>
          </>
        )}
        <h3 style={{ margin: "16px 0 8px", fontSize: 14 }}>Company in ARES</h3>
        {!result.company ? (
          <p className="muted" style={{ margin: 0, fontSize: 13 }}>
            Not known: {result.stages.find((s) => s.stage === "company")?.note ?? "looked up only in results made since Fides 0.3"}.
          </p>
        ) : (
          <dl className="audit">
            <Row label="Name">{result.company.name ?? "–"} <span className="faint">(IČO {result.company.ico})</span></Row>
            <Row label="Legal form">{result.company.legal_form ?? "–"}</Row>
            <Row label="Address">{result.company.address ?? "–"}</Row>
            <Row label="Industry">
              {result.company.nace ? <>{result.company.nace_name ?? "–"} <span className="faint">(CZ-NACE {result.company.nace}, section {result.company.nace_section ?? "–"} {result.company.nace_section_name ?? ""})</span></> : "–"}
            </Row>
            <Row label="Employees">{result.company.employees ?? "–"}</Row>
            <Row label="Source">{SOURCE[result.company.source]}{result.company.retrieved && `, ${result.company.retrieved}`}</Row>
          </dl>
        )}
        <h3 style={{ margin: "16px 0 8px", fontSize: 14 }}>Steps</h3>
        <table className="values">
          <thead><tr><th>Step</th><th>Status</th><th className="r">Time</th><th>Note</th></tr></thead>
          <tbody>
            {result.stages.map((s) => (
              <tr key={s.stage} style={{ cursor: "default" }}>
                <td>{s.stage}</td>
                <td><Status tone={STAGE_TONE[s.status]}>{s.status === "stub" ? "mock data" : s.status}</Status></td>
                <td className="r num">{formatNumber(s.seconds, 2)} s</td>
                <td className="faint">{s.note ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </dialog>
    </>
  );
}
