import { Upload } from "lucide-react";
import { useRef, useState, type DragEvent } from "react";

interface Props {
  busy: boolean;
  onAnalyse: (file: File, scope: "individual" | "consolidated") => void;
}

export function NewAnalysis({ busy, onAnalyse }: Props) {
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

  return (
    <section className="new-analysis" aria-labelledby="new-title">
      <div>
        <h1 id="new-title">New analysis</h1>
        <p className="muted" style={{ margin: "4px 0 0" }}>
          Fides finds the balance sheet and the income statement in the PDF, reads every number and checks the totals.
        </p>
      </div>
      <div className="card">
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
          <Upload aria-hidden="true" />
          {file ? <strong>{file.name}</strong> : <strong>Drop a PDF or browse</strong>}
          <span>Financial statements or an annual report, digital or scanned</span>
          <input ref={input} type="file" accept="application/pdf,.pdf" hidden onChange={(e) => pick(e.target.files?.[0])} />
        </div>
        {fileError && <span className="field"><span className="error" role="alert">{fileError}</span></span>}
        <div className="field">
          <span>Statements to read</span>
          <div className="segmented" role="group" aria-label="Statements to read" style={{ justifySelf: "start" }}>
            {(["individual", "consolidated"] as const).map((s) => (
              <button key={s} type="button" aria-pressed={scope === s} onClick={() => setScope(s)}>
                {s === "individual" ? "Individual" : "Consolidated"}
              </button>
            ))}
          </div>
          <span className="hint">Group annual reports hold both sets</span>
        </div>
        <button className="btn primary" type="button" onClick={analyse} aria-disabled={busy} style={{ justifySelf: "start" }}>
          {busy ? "Analysing…" : "Analyse"}
        </button>
      </div>
    </section>
  );
}
