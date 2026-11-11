import { Download, FileText, Info, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { About } from "./components/About";
import { Extraction } from "./components/Extraction";
import { NewAnalysis } from "./components/NewAnalysis";
import { Overview } from "./components/Overview";
import { Ratios } from "./components/Ratios";
import { SignIn } from "./components/SignIn";
import { Report } from "./components/Report";
import { Sidebar, type RecentResult } from "./components/Sidebar";
import { MockBadge } from "./components/ui";
import { Validation } from "./components/Validation";
import { api, SIGNED_OUT } from "./lib/api";
import { formatDate } from "./lib/format";
import { stepSummaries, type StepId } from "./lib/summary";
import { accountingChecks, valueRows, type ValueRow } from "./lib/values";
import type { AuthState, LineDef, ResultView, Sample, StatementPages } from "./types";

const STEPS: { id: StepId; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "extraction", label: "Extraction" },
  { id: "validation", label: "Validation" },
  { id: "ratios", label: "Ratios" },
  { id: "report", label: "AI report" },
];
const OPEN_NOTES = 2; // a short list of notes is shown open, a long one folded
// notes on what was read (pages, a format Fides does not read, unit, date) stay in view; the rest can be folded
const READ_NOTE = /^(locate|read|parse):/;

function stepFromHash(): StepId {
  const id = window.location.hash.slice(1);
  return STEPS.some((s) => s.id === id) ? (id as StepId) : "overview";
}

export default function App() {
  const [auth, setAuth] = useState<AuthState | null>(null);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [dictionary, setDictionary] = useState<LineDef[]>([]);
  const [result, setResult] = useState<ResultView | null>(null);
  const [recent, setRecent] = useState<RecentResult[]>([]);
  const [newAnalysis, setNewAnalysis] = useState(true);
  const [step, setStep] = useState<StepId>(stepFromHash);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [ico, setIco] = useState("");
  const [busy, setBusy] = useState<{ file: string; started: number } | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState("");
  const abort = useRef<AbortController | null>(null);

  const show = useCallback((r: ResultView) => {
    setResult(r);
    setNewAnalysis(false);
    setSelectedId(null);
    setIco(r.statements.metadata.ico ?? "");
    setError("");
  }, []);

  // CIPW-62: who is signed in; with local accounts nothing loads before sign-in
  const signedIn = auth !== null && (auth.mode === "none" || auth.user !== null);
  const role = auth?.user?.role ?? "admin";
  const canAnalyse = role !== "viewer";
  const unreachable = (e: Error) => setError(`The Fides service is not reachable (${e.message}). Is the container running?`);

  useEffect(() => {
    api.me().then(setAuth).catch(unreachable);
    const signedOut = () => {
      setAuth((a) => (a && a.mode !== "none" ? { ...a, user: null } : a));
      setResult(null);
      setNewAnalysis(true);
    };
    window.addEventListener(SIGNED_OUT, signedOut);
    return () => window.removeEventListener(SIGNED_OUT, signedOut);
  }, []);

  useEffect(() => {
    if (!signedIn) return;
    Promise.all([api.samples(), api.dictionary()])
      .then(([s, d]) => {
        setSamples(s);
        setDictionary(d);
      })
      .catch(unreachable);
    // the stored results (CIPW-60); an older server without them lists nothing
    api.results().then((list) => setRecent(list)).catch(() => setRecent([]));
  }, [signedIn, auth?.user?.name]);

  const signOut = () => {
    api.logout().then(setAuth).catch(() => setAuth((a) => (a ? { ...a, user: null } : a)));
    setResult(null);
    setRecent([]);
    setNewAnalysis(true);
  };

  const remove = () => {
    if (!result || !window.confirm(`Delete ${result.file} with its corrections? This cannot be undone.`)) return;
    api.remove(result.id)
      .then(() => {
        setRecent((list) => list.filter((r) => r.id !== result.id));
        setResult(null);
        setNewAnalysis(true);
      })
      .catch((e: Error) => setError(e.message));
  };

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

  // CIPW-42: a value confirmed or corrected by hand; the API computes everything after the reading again
  const correct = useCallback(async (row: ValueRow, czk: number) => {
    if (!result) return;
    setResult(await api.correct(result.id, { key: row.key, period: row.period, value: czk }));
  }, [result]);

  const go = (next: StepId, rowId?: string) => {
    if (rowId) setSelectedId(rowId);
    window.location.hash = next;
    setStep(next);
    window.scrollTo({ top: 0 });
  };

  const open = (id: string) => {
    if (id === result?.id) {
      setNewAnalysis(false);
      return;
    }
    api.result(id).then((r) => {
      show(r);
      go("overview");
    }).catch((e: Error) => setError(e.message));
  };

  const analyse = (file: File, scope: "individual" | "consolidated", pages: StatementPages) => {
    if (busy) return;
    abort.current = new AbortController();
    setBusy({ file: file.name, started: Date.now() });
    setElapsed(0);
    setError("");
    api
      .analyse(file, scope, pages, abort.current.signal)
      .then((r) => {
        show(r);
        setRecent((list) => [
          { id: r.id, file: file.name, company: r.display_name, owner: r.owner, created_at: new Date().toISOString() },
          ...list.filter((x) => x.id !== r.id),
        ]);
        go("overview");
      })
      .catch((e: Error) => setError(e.name === "AbortError" ? "" : `${file.name} could not be analysed: ${e.message}`))
      .finally(() => setBusy(null));
  };

  const rows = useMemo(() => (result ? valueRows(result, dictionary) : []), [result, dictionary]);
  const summaries = useMemo(() => (result ? stepSummaries(result, rows) : []), [result, rows]);
  const meta = result?.statements.metadata;
  const company = result?.company ?? null;
  const printed = result?.statements.metadata.company_name ?? null;
  const name = result?.display_name ?? (printed || company?.name || null);
  const nameFromAres = result?.name_source ? result.name_source === "ares" : !printed && !!company?.name;
  // a count next to a step only for what needs a person: values to review, failed checks
  const counts: Partial<Record<StepId, number>> = {
    extraction: rows.filter((r) => r.confidence === "review").length,
    validation: accountingChecks(result?.validation?.checks ?? []).filter((c) => !c.passed && c.severity === "error").length,
  };
  const rating = result?.report?.rating.value;
  const readNotes = (result?.warnings ?? []).filter((w) => READ_NOTE.test(w));
  const otherNotes = (result?.warnings ?? []).filter((w) => !READ_NOTE.test(w));

  if (auth === null) {
    return error ? <main className="signin"><div className="banner error" role="alert">{error}</div></main> : null;
  }
  if (!signedIn) return <SignIn onSignedIn={setAuth} />;
  const canDelete = result?.sample === false && (role === "admin" || (!!result.owner && result.owner === auth.user?.name));

  return (
    <div className="shell">
      <Sidebar
        samples={samples}
        recent={recent}
        currentId={newAnalysis || busy ? null : (result?.id ?? null)}
        analysing={busy !== null}
        onNew={() => setNewAnalysis(true)}
        onOpen={open}
        user={auth.mode === "local" ? auth.user : null}
        canAnalyse={canAnalyse}
        onSignOut={signOut}
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
        ) : (newAnalysis || !result) && !canAnalyse ? (
          <div className="empty">
            <h2>Open an analysis</h2>
            <p>Pick an analysed file or a sample on the left. Your role, viewer, can read results and download them.</p>
          </div>
        ) : newAnalysis || !result ? (
          <NewAnalysis busy={busy !== null} onAnalyse={analyse} />
        ) : (
          <>
            <header className="result-head">
              <div>
                <div className="eyebrow">{result.file}</div>
                <h1>{name || (ico ? `Company IČO ${ico}` : "Company name not printed")}</h1>
                <div className="meta">
                  {nameFromAres && !printed && <span>Name from ARES</span>}
                  {nameFromAres && printed && printed !== name && <span>Name from ARES; the statement reads “{printed}”</span>}
                  {!nameFromAres && !printed && <span>Name not printed on the statement</span>}
                  <span>IČO {ico || "–"}</span>
                  {company?.nace && <span title={`CZ-NACE ${company.nace}, section ${company.nace_section ?? "–"}`}>{company.nace_name ?? `CZ-NACE ${company.nace}`}</span>}
                  <span>Balance sheet date {formatDate(meta?.period_end ?? null)}</span>
                  <span>{meta?.scope === "abbreviated" ? "Abbreviated" : meta?.scope === "full" ? "Full" : "Unknown"} statement</span>
                  {result.consolidation && <span>{result.consolidation === "individual" ? "Individual" : "Consolidated"}</span>}
                  <span>
                    {Object.values(meta?.extraction_source ?? {}).includes("ocr") ? "Scan, read with OCR" : "Digital, text layer"}
                  </span>
                </div>
                {(rating || result.stubbed_stages.length > 0) && (
                  <div className="head-tags">
                    {rating && (
                      <span className="badge">
                        {rating[0].toUpperCase() + rating.slice(1)} risk ({result.report?.source === "rules" ? "rules" : "AI report"})
                      </span>
                    )}
                    {result.stubbed_stages.length > 0 && <MockBadge />}
                  </div>
                )}
              </div>
              <div className="head-actions">
                <About result={result} />
                <a className="btn" href={api.reportUrl(result.id)} download>
                  <FileText aria-hidden="true" />
                  Export PDF
                </a>
                <a className="btn" href={api.exportUrl(result.id)} download>
                  <Download aria-hidden="true" />
                  Export JSON
                </a>
                {canDelete && (
                  <button type="button" className="btn ghost" onClick={remove}>
                    <Trash2 aria-hidden="true" />
                    Delete
                  </button>
                )}
              </div>
            </header>
            {result.errors.length > 0 && (
              <div className="banner error" role="alert" style={{ marginBottom: 16 }}>{result.errors[0].split("\n")[0]}</div>
            )}
            {readNotes.length > 0 && (
              <div className="notes" role="note">
                <ul className="read">
                  {readNotes.map((w) => <li key={w}><Info aria-hidden="true" />{w}</li>)}
                </ul>
              </div>
            )}
            {otherNotes.length > 0 && (
              <details className="notes" open={otherNotes.length <= OPEN_NOTES}>
                <summary>
                  <Info aria-hidden="true" />
                  {otherNotes.length === 1 ? "1 note" : `${otherNotes.length} notes`} from the checks and the re-read
                </summary>
                <ul>{otherNotes.map((w) => <li key={w}>{w}</li>)}</ul>
              </details>
            )}

            <nav className="steps" aria-label="Steps">
              {STEPS.map((s) => (
                <button key={s.id} type="button" aria-current={step === s.id ? "page" : undefined} onClick={() => go(s.id)}>
                  {s.label}
                  {counts[s.id] ? <span className="count bad" title="Needs a person">{counts[s.id]}</span> : null}
                </button>
              ))}
            </nav>

            {step === "overview" && <Overview result={result} rows={rows} summaries={summaries} onOpen={go} />}
            {step === "extraction" && (
              <Extraction key={result.id} result={result} rows={rows} selectedId={selectedId} onSelect={setSelectedId} onCorrect={canAnalyse ? correct : undefined} />
            )}
            {step === "validation" && <Validation key={result.id} result={result} rows={rows} onShowValue={(id) => go("extraction", id)} />}
            {step === "ratios" && <Ratios result={result} ico={ico} onIcoChange={setIco} />}
            {step === "report" && <Report result={result} />}
          </>
        )}
      </main>
    </div>
  );
}
