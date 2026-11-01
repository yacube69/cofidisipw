import { FileText, FlaskConical, Plus } from "lucide-react";
import type { Sample } from "../types";

export interface RecentResult {
  id: string;
  file: string;
}

interface Props {
  samples: Sample[];
  recent: RecentResult[];
  currentId: string | null;
  analysing: boolean;
  onNew: () => void;
  onOpen: (id: string) => void;
}

/** Navigation only: start a new analysis, or go back to a result of this session or a sample. */
export function Sidebar({ samples, recent, currentId, analysing, onNew, onOpen }: Props) {
  return (
    <aside className="sidebar" aria-label="Navigation">
      <a href="/" aria-label="Fides home">
        <img className="logo logo-light" src="/fides-logo.svg" alt="Fides" />
        <img className="logo logo-dark" src="/fides-logo-white.svg" alt="" />
      </a>

      <button type="button" className="btn primary block" onClick={onNew} aria-current={currentId === null ? "true" : undefined}>
        <Plus aria-hidden="true" />
        {analysing ? "Analysis running…" : "New analysis"}
      </button>

      {recent.length > 0 && (
        <nav className="side-section" aria-label="Analysed files">
          <div className="eyebrow">Analysed files</div>
          {recent.map((r) => (
            <button key={r.id} type="button" className="nav-item" aria-current={currentId === r.id ? "true" : undefined} onClick={() => onOpen(r.id)}>
              <FileText aria-hidden="true" />
              <span>{r.file}</span>
            </button>
          ))}
        </nav>
      )}

      {samples.length > 0 && (
        <nav className="side-section" aria-label="Samples">
          <div className="eyebrow">Samples</div>
          {samples.map((s) => (
            <button key={s.id} type="button" className="nav-item" aria-current={currentId === s.id ? "true" : undefined} onClick={() => onOpen(s.id)}>
              <FlaskConical aria-hidden="true" />
              <span>{s.label}</span>
            </button>
          ))}
        </nav>
      )}

      <footer className="side-foot">
        <span>Every number, traced to its page.</span>
        <span>Fides {__FIDES_VERSION__} · Cofidis IPW 2026 prototype</span>
      </footer>
    </aside>
  );
}
