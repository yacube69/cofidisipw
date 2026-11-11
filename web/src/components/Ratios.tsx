import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { formatChange, formatNumber, formatRatio } from "../lib/format";
import { ratioLight } from "../lib/summary";
import type { BankruptcyModel, Company, Light, Ratio, ResultView, Zone } from "../types";
import { Badge, MockBadge, MockBanner } from "./ui";

const GROUPS = [
  ["liquidity", "Liquidity"],
  ["profitability", "Profitability"],
  ["leverage", "Leverage"],
  ["activity", "Activity"],
] as const;

// A weak ratio or a distress zone is a finding about the company, not a failure of Fides: it is marked, not red
const LIGHT: Record<Light, { tone: "neutral" | "warn"; word: string }> = {
  green: { tone: "neutral", word: "Healthy" },
  amber: { tone: "neutral", word: "Watch" },
  red: { tone: "warn", word: "Weak" },
};
const ZONE: Record<Zone, { tone: "neutral" | "warn"; word: string }> = {
  safe: { tone: "neutral", word: "Safe zone" },
  grey: { tone: "neutral", word: "Grey zone" },
  distress: { tone: "warn", word: "Distress zone" },
};

function RatioCard({ result, ratio }: { result: ResultView; ratio: Ratio }) {
  const change = ratio.current.value !== null && ratio.prior.value !== null ? ratio.current.value - ratio.prior.value : null;
  const light = ratioLight(result, ratio);
  const Arrow = change !== null && change > 0 ? ArrowUpRight : ArrowDownRight;
  return (
    <div className="card ratio">
      <div className="name">
        <span>{ratio.name}</span>
        {light && <Badge tone={LIGHT[light].tone} icon={LIGHT[light].tone === "warn"}>{LIGHT[light].word}</Badge>}
        {ratio.current.flag === "unreliable" && <Badge tone="warn" icon>Check inputs</Badge>}
      </div>
      <div className="value">{formatRatio(ratio.current.value, ratio.unit)}</div>
      <div className="change">
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
        {current.zone && <Badge tone={ZONE[current.zone].tone} icon={ZONE[current.zone].tone === "warn"}>{ZONE[current.zone].word}</Badge>}
        {current.flag === "unreliable" && <Badge tone="warn" icon>Check inputs</Badge>}
      </div>
      <div className="zone">{current.score === null ? "–" : formatNumber(current.score, 2)}</div>
      {current.probability !== null && (
        <div>Probability of bankruptcy{model.horizon && ` within ${model.horizon}`}: {formatNumber(current.probability * 100, 1)} %</div>
      )}
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

interface Props {
  result: ResultView;
  ico: string;
  onIcoChange: (ico: string) => void;
}

/** The company of an IČO in ARES (CIPW-44): the result's own one, or looked up when the user enters another IČO. */
function useCompany(result: ResultView, ico: string): { company?: Company; error?: string } {
  const [found, setFound] = useState<{ ico: string; company?: Company; error?: string } | null>(null);
  const own = result.company && result.company.ico === ico ? result.company : null;
  useEffect(() => {
    if (own || !/^\d{8}$/.test(ico)) return;
    let current = true;
    api.company(ico)
      .then((company) => current && setFound({ ico, company }))
      .catch((e: Error) => current && setFound({ ico, error: e.message }));
    return () => {
      current = false;
    };
  }, [ico, own]);
  if (own) return { company: own };
  return found?.ico === ico ? found : {};
}

export function Ratios({ result, ico, onIcoChange }: Props) {
  const { company, error } = useCompany(result, ico);
  const ratios = result.ratios?.ratios ?? [];
  const b = result.bankruptcy;
  const stub = result.stubbed_stages;
  const printedIco = result.statements.metadata.ico;
  const icoInvalid = ico !== "" && !/^\d{8}$/.test(ico);
  const mock = stub.includes("ratios")
    ? "Ratios, bankruptcy scores and benchmarks"
    : stub.includes("bankruptcy")
      ? "Bankruptcy scores"
      : null;
  return (
    <div className="stack">
      {mock && <MockBanner what={mock} />}
      {b && b.models.length > 0 && (
        <div className="grid-2">
          {b.models.map((m) => <ModelCard key={m.key} model={m} />)}
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
      <section className="card" aria-labelledby="benchmarks-title">
        <div className="card-title">
          <h2 id="benchmarks-title">Benchmarks</h2>
          {stub.includes("benchmarks") && <MockBadge />}
        </div>
        <label className="field" style={{ maxWidth: 260 }}>
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
          ) : error ? (
            <span className="error">{error}</span>
          ) : (
            <span className="hint">
              {company
                ? `${company.name ?? "Name unknown"} · ${company.nace_name ?? "industry unknown"}${company.nace ? ` (CZ-NACE ${company.nace})` : ""}`
                : "Read from the statement; ARES gives the industry to compare with"}
              {printedIco && ico !== printedIco && ` · the statement says ${printedIco}`}
            </span>
          )}
        </label>
        {result.benchmarks?.source && !(stub.includes("benchmarks") && !stub.includes("ratios")) && (
          <p className="faint" style={{ margin: "10px 0 0", fontSize: 12 }}>Source: {result.benchmarks.source}</p>
        )}
      </section>
    </div>
  );
}
