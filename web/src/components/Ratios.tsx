import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import { formatChange, formatNumber, formatRatio } from "../lib/format";
import { ratioLight } from "../lib/summary";
import type { Light, Ratio, ResultView, Zone } from "../types";
import { Badge, MockBanner } from "./ui";

const GROUPS = [
  ["liquidity", "Liquidity"],
  ["profitability", "Profitability"],
  ["leverage", "Leverage"],
  ["activity", "Activity"],
] as const;

// For these ratios a fall is good news (less debt, faster collection)
const LOWER_IS_BETTER = new Set(["debt_ratio", "debt_to_equity", "receivables_days"]);
const LIGHT: Record<Light, { tone: "ok" | "warn" | "bad"; word: string }> = {
  green: { tone: "ok", word: "Healthy" },
  amber: { tone: "warn", word: "Watch" },
  red: { tone: "bad", word: "Weak" },
};
const ZONE: Record<Zone, { tone: "ok" | "warn" | "bad"; word: string }> = {
  safe: { tone: "ok", word: "Safe zone" },
  grey: { tone: "warn", word: "Grey zone" },
  distress: { tone: "bad", word: "Distress zone" },
};

function RatioCard({ result, ratio }: { result: ResultView; ratio: Ratio }) {
  const change = ratio.current.value !== null && ratio.prior.value !== null ? ratio.current.value - ratio.prior.value : null;
  const light = ratioLight(result, ratio);
  const better = change === null || change === 0 ? null : LOWER_IS_BETTER.has(ratio.key) ? change < 0 : change > 0;
  const Arrow = change !== null && change > 0 ? ArrowUpRight : ArrowDownRight;
  return (
    <div className="card ratio">
      <div className="name">
        <span>{ratio.name}</span>
        {light && <Badge tone={LIGHT[light].tone} icon>{LIGHT[light].word}</Badge>}
      </div>
      <div className="value">{formatRatio(ratio.current.value, ratio.unit)}</div>
      <div className={`change${better === null ? "" : better ? " up" : " down"}`}>
        {change !== null && <Arrow size={14} aria-hidden="true" style={{ verticalAlign: -2 }} />}{" "}
        {formatChange(change, ratio.unit)} <span className="faint">vs {formatRatio(ratio.prior.value, ratio.unit)} prior year</span>
      </div>
      <div className="formula">{ratio.formula}</div>
      {ratio.current.flag !== "ok" && ratio.current.reason && <div className="faint" style={{ fontSize: 12 }}>{ratio.current.reason}</div>}
    </div>
  );
}

export function Ratios({ result }: { result: ResultView }) {
  const ratios = result.ratios?.ratios ?? [];
  const b = result.bankruptcy;
  const mock = result.stubbed_stages.includes("ratios");
  return (
    <div className="stack">
      {mock && <MockBanner what="Ratios, bankruptcy scores and benchmarks" />}
      {b && (b.in05 || b.altman_z_prime) && (
        <div className="grid-2">
          {b.in05 && (
            <div className="card ratio">
              <div className="name"><span>IN05 (Neumaierovi)</span><Badge tone={ZONE[b.in05.zone].tone} icon>{ZONE[b.in05.zone].word}</Badge></div>
              <div className="zone">{formatNumber(b.in05.score, 2)}</div>
              <div className="faint" style={{ fontSize: 12 }}>Czech bankruptcy model: below 0.9 distress, above 1.6 creates value</div>
            </div>
          )}
          {b.altman_z_prime && (
            <div className="card ratio">
              <div className="name"><span>Altman Z′ (private firms)</span><Badge tone={ZONE[b.altman_z_prime.zone].tone} icon>{ZONE[b.altman_z_prime.zone].word}</Badge></div>
              <div className="zone">{formatNumber(b.altman_z_prime.score, 2)}</div>
              <div className="faint" style={{ fontSize: 12 }}>Below 1.23 distress, above 2.9 safe</div>
            </div>
          )}
        </div>
      )}
      {b?.note && <p className="faint" style={{ margin: 0, fontSize: 13 }}>{b.note}</p>}
      {GROUPS.map(([group, title]) => {
        const items = ratios.filter((r) => r.group === group);
        if (items.length === 0) return null;
        return (
          <section key={group} aria-label={title}>
            <h2 className="group-title">{title}</h2>
            <div className="grid-3">{items.map((r) => <RatioCard key={r.key} result={result} ratio={r} />)}</div>
          </section>
        );
      })}
      {ratios.length === 0 && <div className="empty"><h2>No ratios</h2><p>The ratio engine (CIPW-12) has not run on this file.</p></div>}
      {result.benchmarks?.source && <p className="faint" style={{ margin: 0, fontSize: 12 }}>Benchmarks: {result.benchmarks.source}</p>}
    </div>
  );
}
