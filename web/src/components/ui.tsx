import { AlertTriangle, CheckCircle2, CircleDashed, FlaskConical, XCircle } from "lucide-react";
import type { ReactNode } from "react";
import type { Tone } from "../lib/summary";

const ICONS: Record<Tone, typeof CheckCircle2> = {
  ok: CheckCircle2,
  warn: AlertTriangle,
  bad: XCircle,
  neutral: CircleDashed,
};

/** Status is always an icon and a word, never colour alone. */
export function Status({ tone, children }: { tone: Tone; children: ReactNode }) {
  const Icon = ICONS[tone];
  return (
    <span className={`status ${tone}`}>
      <Icon aria-hidden="true" />
      {children}
    </span>
  );
}

export function Badge({ tone, children, icon }: { tone: Tone; children: ReactNode; icon?: boolean }) {
  const Icon = ICONS[tone];
  return (
    <span className={`badge ${tone}`}>
      {icon && <Icon aria-hidden="true" />}
      {children}
    </span>
  );
}

export function MockBadge() {
  return (
    <span className="badge neutral" title="This step is not implemented yet: the values come from the mock data">
      <FlaskConical aria-hidden="true" />
      Mock data
    </span>
  );
}

export function MockBanner({ what }: { what: string }) {
  return (
    <div className="banner mock" role="note">
      <FlaskConical aria-hidden="true" />
      <span>
        {what} are mock data of one real company (Mondi 2025), the same for every file, until the step is built. Use
        them to review the layout, not the numbers.
      </span>
    </div>
  );
}
