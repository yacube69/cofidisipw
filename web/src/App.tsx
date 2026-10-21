import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { About } from "./components/About";
import { Extraction } from "./components/Extraction";
import { Overview } from "./components/Overview";
import { Ratios } from "./components/Ratios";
import { Report } from "./components/Report";
import { Sidebar } from "./components/Sidebar";
import { Badge, MockBadge } from "./components/ui";
import { Validation } from "./components/Validation";
import { api } from "./lib/api";
import { formatDate } from "./lib/format";
import { stepSummaries, type StepId } from "./lib/summary";
import { accountingChecks, valueRows } from "./lib/values";
import type { LineDef, ResultView, Sample } from "./types";

const STEPS: { id: StepId; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "extraction", label: "Extraction" },
  { id: "validation", label: "Validation" },
  { id: "ratios", label: "Ratios" },
  { id: "report", label: "AI report" },
];

function stepFromHash(): StepId {
  const id = window.location.hash.slice(1);
  return STEPS.some((s) => s.id === id) ? (id as StepId) : "overview";
}

export default function App() {
  const [samples, setSamples] = useState<Sample[]>([]);
  const [dictionary, setDictionary] = useState<LineDef[]>([]);
  const [result, setResult] = useState<ResultView | null>(null);
  const [step, setStep] = useState<StepId>(stepFromHash);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [ico, setIco] = useState("");
  const [busy, setBusy] = useState<{ file: string; started: number } | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState("");
  const abort = useRef<AbortController | null>(null);

  const show = useCallback((r: ResultView) => {
    setResult(r);
    setSelectedId(null);
    setIco(r.statements.metadata.ico ?? "");
    setError("");
  }, []);

  useEffect(() => {
    Promise.all([api.samples(), api.dictionary()])
      .then(([s, d]) => {
        setSamples(s);
        setDictionary(d);
        if (s.length) return api.result(s[0].id).then(show);
      })
      .catch((e: Error) => setError(`The Fides service is not reachable (${e.message}). Is the container running?`));
  }, [show]);

  useEffect(() => {
    const onHash = () => setStep(stepFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    if (!busy) return;
    const timer = window.setInterval(() => setElapsed(Math.round((Date.now() - busy.started) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [busy]);

  const go = (next: StepId, rowId?: string) => {
    if (rowId) setSelectedId(rowId);
    window.location.hash = next;
    setStep(next);
    window.scrollTo({ top: 0 });
  };

  const openSample = (id: string) => {
    api.result(id).then(show).catch((e: Error) => setError(e.message));
  };

  const analyse = (file: File, scope: "individual" | "consolidated") => {
    if (busy) return;
    abort.current = new AbortController();
    setBusy({ file: file.name, started: Date.now() });
    setElapsed(0);
    setError("");
    api
      .analyse(file, scope, abort.current.signal)
      .then((r) => {
        show(r);
        go("overview");
      })
      .catch((e: Error) => setError(e.name === "AbortError" ? "" : `${file.name} could not be analysed: ${e.message}`))
      .finally(() => setBusy(null));
  };

  const rows = useMemo(() => (result ? valueRows(result, dictionary) : []), [result, dictionary]);
  const summaries = useMemo(() => (result ? stepSummaries(result, rows) : []), [result, rows]);
  const meta = result?.statements.metadata;
  const counts: Partial<Record<StepId, { n: number; tone: string }>> = {};
  for (const s of summaries) {
    const n = s.id === "extraction" ? rows.filter((r) => r.confidence === "review").length
      : s.id === "validation" ? accountingChecks(result?.validation?.checks ?? []).filter((c) => !c.passed && c.severity === "error").length : 0;
    if (n) counts[s.id] = { n, tone: s.tone };
  }

  return (
    <div className="shell">
      <Sidebar
        samples={samples}
        result={result}
        busy={busy !== null}
        onOpenSample={openSample}
        onAnalyse={analyse}
        ico={ico}
        onIcoChange={setIco}
      />
      <main className="main">
        {error && <div className="banner error" role="alert" style={{ marginBottom: 16 }}>{error}</div>}
        {busy ? (
          <div className="empty" role="status">
            <div className="spinner" aria-hidden="true" />
            <h2>Reading {busy.file}</h2>
            <p>
              Finding the statements, reading every number and checking the totals. A digital PDF takes seconds, a
              scan about 20 seconds per page. {elapsed > 0 && `${elapsed} s so far.`}
            </p>
            <button type="button" className="btn" onClick={() => abort.current?.abort()}>Cancel</button>
          </div>
        ) : !result ? (
          !error && <div className="empty"><div className="spinner" aria-hidden="true" /><p>Loading…</p></div>
        ) : (
          <>
            <header className="result-head">
              <div>
                <div className="eyebrow">{result.file}</div>
                <h1>{meta?.company_name || (ico ? `Company IČO ${ico}` : "Company name not printed")}</h1>
                <div className="meta">
                  {!meta?.company_name && <span>Name not printed on the statement</span>}
                  <span>IČO {ico || "–"}</span>
                  <span>Balance sheet date {formatDate(meta?.period_end ?? null)}</span>
                  <span>{meta?.scope === "abbreviated" ? "Abbreviated" : meta?.scope === "full" ? "Full" : "Unknown"} statement</span>
                  {result.consolidation && <span>{result.consolidation === "individual" ? "Individual" : "Consolidated"}</span>}
                  <span>
                    {Object.values(meta?.extraction_source ?? {}).includes("ocr") ? "Scan, read with OCR" : "Digital, text layer"}
                  </span>
                </div>
              </div>
              <div className="head-badges">
                {result.report && (
                  <Badge tone={result.report.rating.value === "high" ? "bad" : result.report.rating.value === "medium" ? "warn" : "ok"} icon>
                    {result.report.rating.value[0].toUpperCase() + result.report.rating.value.slice(1)} risk
                  </Badge>
                )}
                {result.stubbed_stages.length > 0 && <MockBadge />}
                <About result={result} />
              </div>
            </header>
            {result.warnings.map((w) => <div key={w} className="banner warn" style={{ marginBottom: 12 }}>{w}</div>)}
            {result.errors.length > 0 && (
              <div className="banner error" style={{ marginBottom: 12 }}>{result.errors[0].split("\n")[0]}</div>
            )}

            <nav className="steps" aria-label="Steps">
              {STEPS.map((s) => (
                <button key={s.id} type="button" aria-current={step === s.id ? "page" : undefined} onClick={() => go(s.id)}>
                  {s.label}
                  {counts[s.id] && <span className={`count ${counts[s.id]!.tone}`}>{counts[s.id]!.n}</span>}
                </button>
              ))}
            </nav>

            {step === "overview" && <Overview result={result} rows={rows} summaries={summaries} onOpen={go} />}
            {step === "extraction" && <Extraction key={result.id} result={result} rows={rows} selectedId={selectedId} onSelect={setSelectedId} />}
            {step === "validation" && <Validation key={result.id} result={result} rows={rows} onShowValue={(id) => go("extraction", id)} />}
            {step === "ratios" && <Ratios result={result} />}
            {step === "report" && <Report result={result} />}
          </>
        )}
      </main>
    </div>
  );
}
