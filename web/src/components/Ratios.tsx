import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import { formatChange, formatNumber, formatRatio } from "../lib/format";
import { ratioLight } from "../lib/summary";
import type { BankruptcyModel, Light, Ratio, ResultView, Zone } from "../types";
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
        {ratio.current.flag === "unreliable" && <Badge tone="warn">Check inputs</Badge>}
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

function ModelCard({ model }: { model: BankruptcyModel }) {
  const { current, prior } = model;
  return (
    <div className="card ratio">
      <div className="name">
        <span>{model.name}</span>
        {current.zone && <Badge tone={ZONE[current.zone].tone} icon>{ZONE[current.zone].word}</Badge>}
        {current.flag === "unreliable" && <Badge tone="warn">Check inputs</Badge>}
      </div>
      <div className="zone">{current.score === null ? "–" : formatNumber(current.score, 2)}</div>
      <div className="change">
        <span className="faint">
          {prior.score === null ? "no prior year" : `${formatNumber(prior.score, 2)} prior year${prior.zone ? ` (${ZONE[prior.zone].word.toLowerCase()})` : ""}`}
        </span>
      </div>
      <div className="formula">
        {current.variables.map((v) => `${v.key} ${v.value === null ? "–" : formatNumber(v.value, 2)}`).join(" · ")}
      </div>
      <div className="faint" style={{ fontSize: 12 }}>{model.zones}</div>
      {current.flag !== "ok" && current.reason && <div className="faint" style={{ fontSize: 12 }}>{current.reason}</div>}
    </div>
  );
}

export function Ratios({ result }: { result: ResultView }) {
  const ratios = result.ratios?.ratios ?? [];
  const b = result.bankruptcy;
  const stub = result.stubbed_stages;
  const mock = stub.includes("ratios")
    ? "Ratios, bankruptcy scores and benchmarks"
    : stub.includes("bankruptcy")
      ? "Bankruptcy scores"
      : null;
  return (
    <div className="stack">
      {mock && <MockBanner what={mock} />}
      {b && (
        <div className="grid-2">
          <ModelCard model={b.in05} />
          <ModelCard model={b.altman_z_prime} />
        </div>
      )}
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
      {result.benchmarks?.source && !(stub.includes("benchmarks") && !stub.includes("ratios")) && <p className="faint" style={{ margin: 0, fontSize: 12 }}>Benchmarks: {result.benchmarks.source}</p>}
    </div>
  );
}
