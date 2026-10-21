import { Info, X } from "lucide-react";
import { useRef, type ReactNode } from "react";
import { formatNumber } from "../lib/format";
import type { ResultView } from "../types";
import { Status } from "./ui";

const STAGE_TONE = { ok: "ok", stub: "neutral", skipped: "neutral", error: "bad" } as const;

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
                      <li key={i}>{c.key} ({c.period}): {c.old ?? "–"} → {c.new ?? "–"}{c.by && `, ${c.by}`}, {when(c.at)}</li>
                    ))}
                  </ul>
                )}
              </Row>
            </dl>
          </>
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
