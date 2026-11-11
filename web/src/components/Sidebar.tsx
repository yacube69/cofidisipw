import { FileText, FlaskConical, LogOut, Plus } from "lucide-react";
import type { AuthUser, Sample } from "../types";

/** A stored result (CIPW-60) or one analysed just now. */
export interface RecentResult {
  id: string;
  file: string;
  company?: string | null;
  owner?: string | null;
  created_at?: string;
}

interface Props {
  samples: Sample[];
  recent: RecentResult[];
  currentId: string | null;
  analysing: boolean;
  onNew: () => void;
  onOpen: (id: string) => void;
  /** The signed-in user with local accounts (CIPW-62); null without sign-in. */
  user?: AuthUser | null;
  canAnalyse?: boolean;
  onSignOut?: () => void;
}

function when(iso?: string): string {
  return iso ? new Date(iso).toLocaleDateString("cs-CZ") : "";
}

/** Navigation only: start a new analysis, or open a stored result or a sample. */
export function Sidebar({ samples, recent, currentId, analysing, onNew, onOpen, user = null, canAnalyse = true, onSignOut }: Props) {
  return (
    <aside className="sidebar" aria-label="Navigation">
      <a href="/" aria-label="Fides home">
        <img className="logo logo-light" src="/fides-logo.svg" alt="Fides" />
        <img className="logo logo-dark" src="/fides-logo-white.svg" alt="" />
      </a>

      {canAnalyse && (
        <button type="button" className="btn primary block" onClick={onNew} aria-current={currentId === null ? "true" : undefined}>
          <Plus aria-hidden="true" />
          {analysing ? "Analysis running…" : "New analysis"}
        </button>
      )}

      {recent.length > 0 && (
        <nav className="side-section" aria-label="Analysed files">
          <div className="eyebrow">Analysed files</div>
          {recent.map((r) => (
            <button key={r.id} type="button" className="nav-item" aria-current={currentId === r.id ? "true" : undefined} onClick={() => onOpen(r.id)}
              title={[r.file, when(r.created_at), r.owner].filter(Boolean).join(" · ")}>
              <FileText aria-hidden="true" />
              <span>
                {r.company || r.file}
                {(r.created_at || r.owner) && <span className="what">{[when(r.created_at), r.owner].filter(Boolean).join(" · ")}</span>}
              </span>
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

      {user?.name && (
        <div className="side-user">
          <div className="who">
            <span>{user.name}</span>
            <span className="faint">{user.role}</span>
          </div>
          <button type="button" className="btn ghost small" onClick={onSignOut}>
            <LogOut aria-hidden="true" />
            Sign out
          </button>
        </div>
      )}

      <footer className="side-foot">
        <span>Every number, traced to its page.</span>
        <span>Fides {__FIDES_VERSION__} · Cofidis IPW 2026 prototype</span>
      </footer>
    </aside>
  );
}
