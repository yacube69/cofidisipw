import { Download, FileText, Upload } from "lucide-react";
import { useRef, useState, type DragEvent } from "react";
import { api } from "../lib/api";
import type { ResultView, Sample } from "../types";

interface Props {
  samples: Sample[];
  result: ResultView | null;
  busy: boolean;
  onOpenSample: (id: string) => void;
  onAnalyse: (file: File, scope: "individual" | "consolidated") => void;
  ico: string;
  onIcoChange: (ico: string) => void;
}

export function Sidebar({ samples, result, busy, onOpenSample, onAnalyse, ico, onIcoChange }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [scope, setScope] = useState<"individual" | "consolidated">("individual");
  const [drag, setDrag] = useState(false);
  const [fileError, setFileError] = useState("");
  const input = useRef<HTMLInputElement>(null);

  const pick = (picked: File | undefined) => {
    if (!picked) return;
    if (!picked.name.toLowerCase().endsWith(".pdf")) {
      setFileError("Choose a PDF file");
      setFile(null);
      return;
    }
    setFileError("");
    setFile(picked);
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDrag(false);
    pick(e.dataTransfer.files[0]);
  };
  const analyse = () => {
    if (!file) {
      setFileError("Choose a PDF first");
      return;
    }
    onAnalyse(file, scope);
  };
  const icoInvalid = ico !== "" && !/^\d{8}$/.test(ico);

  return (
    <aside className="sidebar" aria-label="Statement">
      <a href="/" aria-label="Fides home">
        <img className="logo logo-light" src="/fides-logo.svg" alt="Fides" />
        <img className="logo logo-dark" src="/fides-logo-white.svg" alt="" />
      </a>

      <section className="side-section">
        <div className="eyebrow">Analyse a statement</div>
        <div
          className={`dropzone${drag ? " drag" : ""}`}
          role="button"
          tabIndex={0}
          onClick={() => input.current?.click()}
          onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDrag(true);
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={onDrop}
        >
          <Upload aria-hidden="true" size={20} />
          {file ? <strong>{file.name}</strong> : <strong>Drop a PDF or browse</strong>}
          <span>Balance sheet and income statement, digital or scanned</span>
          <input
            ref={input}
            type="file"
            accept="application/pdf,.pdf"
            hidden
            onChange={(e) => pick(e.target.files?.[0])}
          />
        </div>
        {fileError && <div className="field"><span className="error">{fileError}</span></div>}
        <div className="field">
          <span>Statements to read</span>
          <div className="segmented" role="group" aria-label="Statements to read">
            {(["individual", "consolidated"] as const).map((s) => (
              <button key={s} type="button" aria-pressed={scope === s} onClick={() => setScope(s)}>
                {s === "individual" ? "Individual" : "Consolidated"}
              </button>
            ))}
          </div>
          <span className="hint">Group annual reports hold both sets</span>
        </div>
        <button className="btn primary block" type="button" onClick={analyse} aria-disabled={busy}>
          {busy ? "Analysing…" : "Analyse"}
        </button>
      </section>

      {samples.length > 0 && (
        <section className="side-section">
          <div className="eyebrow">Or open a sample</div>
          <div className="sample-list">
            {samples.map((s) => (
              <button
                key={s.id}
                type="button"
                className="sample"
                aria-pressed={result?.id === s.id}
                onClick={() => onOpenSample(s.id)}
              >
                <FileText aria-hidden="true" size={16} />
                {s.label}
              </button>
            ))}
          </div>
        </section>
      )}

      {result && (
        <section className="side-section">
          <div className="eyebrow">Company</div>
          <label className="field">
            <span>IČO</span>
            <input
              value={ico}
              inputMode="numeric"
              maxLength={8}
              placeholder="26161516"
              onChange={(e) => onIcoChange(e.target.value.trim())}
              aria-invalid={icoInvalid}
            />
            {icoInvalid ? (
              <span className="error">IČO has 8 digits</span>
            ) : (
              <span className="hint">
                {result.statements.metadata.ico && ico !== result.statements.metadata.ico
                  ? `The statement says ${result.statements.metadata.ico}`
                  : "Read from the statement; used for benchmarks"}
              </span>
            )}
          </label>
          <a className="btn block" href={api.exportUrl(result.id)} download>
            <Download aria-hidden="true" size={16} />
            Export result (JSON)
          </a>
        </section>
      )}

      <footer className="side-foot">
        <span>Every number, traced to its page.</span>
        <span>Fides 0.1 · Cofidis IPW 2026 prototype</span>
      </footer>
    </aside>
  );
}
