import { useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { api } from "../lib/api";
import { formatAmount, formatNumber, parseAmount, unitLabel } from "../lib/format";
import { CONFIDENCE_LABEL, PERIOD_LABEL, type Confidence, type ValueRow } from "../lib/values";
import type { ResultView } from "../types";
import { Status } from "./ui";

interface Props {
  result: ResultView;
  rows: ValueRow[];
  selectedId: string | null;
  onSelect: (rowId: string | null) => void;
  /** Confirm or correct a value by hand (CIPW-42): the amount in CZK; resolves when the new result is shown. */
  onCorrect?: (row: ValueRow, czk: number) => Promise<void>;
}

type Filter = "all" | "review" | "check";
type Zoom = 1 | 1.5 | 2;

const TONE: Record<Confidence, "ok" | "warn" | "bad"> = { high: "ok", check: "warn", review: "bad", manual: "ok" };
const STATEMENT_LABEL = { balance_sheet: "Balance sheet", income_statement: "Income statement" } as const;

function statementPages(result: ResultView): { page: number; label: string }[] {
  const seen = new Set<number>();
  const out: { page: number; label: string }[] = [];
  for (const statement of ["balance_sheet", "income_statement"] as const) {
    for (const page of result.pages[statement] ?? []) {
      if (seen.has(page)) continue;
      seen.add(page);
      out.push({ page, label: `${STATEMENT_LABEL[statement]} · p. ${page}` });
    }
  }
  return out;
}

export function Extraction({ result, rows, selectedId, onSelect, onCorrect }: Props) {
  const pages = useMemo(() => statementPages(result), [result]);
  const selected = rows.find((r) => r.id === selectedId) ?? null;
  const [page, setPage] = useState<number>(selected?.value.page ?? pages[0]?.page ?? 1);
  const [filter, setFilter] = useState<Filter>("all");
  const [zoom, setZoom] = useState<Zoom>(1);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const boxRefs = useRef(new Map<string, SVGRectElement>());
  const rowRefs = useRef(new Map<string, HTMLTableRowElement>());
  const unit = result.statements.metadata.unit_multiplier;

  // a value picked elsewhere (overview, validation) opens its page
  useEffect(() => {
    if (selected?.value.page && selected.value.page !== page) setPage(selected.value.page);
  }, [selected]);

  useEffect(() => {
    if (!selectedId) return;
    boxRefs.current.get(selectedId)?.scrollIntoView({ block: "center", inline: "center", behavior: "smooth" });
    rowRefs.current.get(selectedId)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [selectedId, page, zoom]);

  const onPage = rows.filter((r) => r.value.page === page);
  const counts = { all: onPage.length, review: 0, check: 0 };
  for (const r of onPage) if (r.confidence === "review" || r.confidence === "check") counts[r.confidence] += 1;
  const visible = filter === "all" ? onPage : onPage.filter((r) => r.confidence === filter);
  const size = result.page_sizes[String(page)];

  // a value confirmed or corrected: the new result is shown and the next value to look at on this page is picked
  const save = async (row: ValueRow, czk: number) => {
    if (!onCorrect) return;
    setSaving(true);
    setSaveError("");
    try {
      await onCorrect(row, czk);
      const i = visible.findIndex((r) => r.id === row.id);
      const next = [...visible.slice(i + 1), ...visible.slice(0, Math.max(i, 0))]
        .find((r) => r.confidence === "review" || r.confidence === "check");
      onSelect(next?.id ?? row.id);
    } catch (e) {
      setSaveError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const move = (e: KeyboardEvent) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const i = visible.findIndex((r) => r.id === selectedId);
    const next = e.key === "ArrowDown" ? Math.min(visible.length - 1, i + 1) : Math.max(0, i - 1);
    if (visible[next]) onSelect(visible[next].id);
  };

  if (pages.length === 0) {
    return <div className="empty"><h2>No statement pages</h2><p>The locator found no balance sheet or income statement in this PDF.</p></div>;
  }

  return (
    <div className="extract">
      <section className="viewer" aria-label="Page with extracted values">
        <div className="toolbar">
          <div className="segmented" role="group" aria-label="Page">
            {pages.map((p) => (
              <button key={p.page} type="button" aria-pressed={page === p.page} onClick={() => setPage(p.page)}>
                {p.label}
              </button>
            ))}
          </div>
          <div className="segmented" role="group" aria-label="Zoom">
            {([1, 1.5, 2] as Zoom[]).map((z) => (
              <button key={z} type="button" aria-pressed={zoom === z} onClick={() => setZoom(z)}>
                {z === 1 ? "Fit" : `${z * 100} %`}
              </button>
            ))}
          </div>
        </div>
        <div className="viewer-scroll">
          <div className="page" style={{ width: `${zoom * 100}%`, aspectRatio: size ? `${size[0]} / ${size[1]}` : undefined }}>
            <img src={api.pageUrl(result.id, page)} alt={`Page ${page} of ${result.file}`} />
            {size && (
              <svg viewBox={`0 0 ${size[0]} ${size[1]}`} preserveAspectRatio="none" role="group" aria-label="Value boxes">
                {onPage.map((r) => {
                  const b = r.value.bbox;
                  if (!b) return null;
                  const pad = 1.5;
                  const dim = filter !== "all" && r.confidence !== filter;
                  return (
                    <rect
                      key={r.id}
                      ref={(el) => {
                        if (el) boxRefs.current.set(r.id, el);
                        else boxRefs.current.delete(r.id);
                      }}
                      className={`vbox ${r.confidence}${r.id === selectedId ? " selected" : ""}${dim ? " dim" : ""}`}
                      x={b.x0 - pad}
                      y={b.y0 - pad}
                      width={b.x1 - b.x0 + 2 * pad}
                      height={b.y1 - b.y0 + 2 * pad}
                      rx={1.5}
                      onClick={() => onSelect(r.id)}
                    >
                      <title>{`${r.name} (${PERIOD_LABEL[r.period]}): ${r.value.raw ?? ""}`}</title>
                    </rect>
                  );
                })}
              </svg>
            )}
          </div>
        </div>
        <div className="legend" style={{ marginTop: 10 }}>
          <span><i className="dot high" /> Confident</span>
          <span><i className="dot check" /> Check (OCR 75–90)</span>
          <span><i className="dot review" /> Review (flagged, low OCR or failed check)</span>
          <span><i className="dot manual" /> Verified by hand</span>
        </div>
      </section>

      <section aria-label="Extracted values">
        <div className="toolbar">
          <div className="segmented" role="group" aria-label="Show values">
            {(["all", "review", "check"] as Filter[]).map((f) => (
              <button key={f} type="button" aria-pressed={filter === f} onClick={() => setFilter(f)}>
                {f === "all" ? "All" : CONFIDENCE_LABEL[f]} <span className="faint">{counts[f]}</span>
              </button>
            ))}
          </div>
          <span className="faint" style={{ fontSize: 12 }}>Amounts in {unitLabel(unit)} as printed</span>
        </div>
        <div className="table-wrap" tabIndex={0} onKeyDown={move} aria-label="Values on this page, use arrow keys to move">
          <table className="values">
            <thead>
              <tr>
                <th>Line</th>
                <th>Period</th>
                <th className="r">Value</th>
                <th>Confidence</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((r) => (
                <tr
                  key={r.id}
                  ref={(el) => {
                    if (el) rowRefs.current.set(r.id, el);
                    else rowRefs.current.delete(r.id);
                  }}
                  aria-selected={r.id === selectedId}
                  onClick={() => onSelect(r.id === selectedId ? null : r.id)}
                >
                  <td>
                    {r.name}
                    <span className="sub">{[r.code, r.officialLabel].filter(Boolean).join(" ")}</span>
                  </td>
                  <td>{PERIOD_LABEL[r.period]}</td>
                  <td className="r num">{formatAmount(r.value.value, unit)}</td>
                  <td><Status tone={TONE[r.confidence]}>{CONFIDENCE_LABEL[r.confidence]}</Status></td>
                </tr>
              ))}
              {visible.length === 0 && (
                <tr><td colSpan={4} className="muted">No values in this group on this page.</td></tr>
              )}
            </tbody>
          </table>
        </div>
        {selected ? (
          <Detail row={selected} unit={unit} saving={saving} error={saveError} onSave={onCorrect ? (czk) => save(selected, czk) : undefined} />
        ) : (
          <p className="faint" style={{ fontSize: 13 }}>Pick a value in the table or a box on the page to see where it came from.</p>
        )}
      </section>
    </div>
  );
}

interface DetailProps {
  row: ValueRow;
  unit: number | null;
  saving?: boolean;
  error?: string;
  onSave?: (czk: number) => void;
}

function Detail({ row, unit, saving = false, error = "", onSave }: DetailProps) {
  const v = row.value;
  return (
    <div className="card detail" aria-live="polite">
      <div className="card-title" style={{ marginBottom: 4 }}>
        <h2>{row.name}</h2>
        <Status tone={TONE[row.confidence]}>{CONFIDENCE_LABEL[row.confidence]}</Status>
      </div>
      <div className="big-number">{formatAmount(v.value, unit)} <span className="faint" style={{ fontSize: 14 }}>{unitLabel(unit)}</span></div>
      <dl>
        <dt>Official label</dt><dd>{row.code} {row.officialLabel}</dd>
        <dt>Read on the page</dt><dd>{row.readLabel || "–"}</dd>
        <dt>Printed text</dt><dd className="mono">{v.raw ?? "–"}</dd>
        <dt>Period</dt><dd>{PERIOD_LABEL[row.period]}</dd>
        <dt>In CZK</dt><dd className="num">{v.value === null ? "–" : formatNumber(v.value)}</dd>
        <dt>Source</dt><dd>page {v.page}, {v.source === "manual" ? "checked by hand in the app" : v.source === "ocr" ? `OCR, confidence ${v.ocr_conf?.toFixed(0) ?? "–"}` : "text layer"}</dd>
        <dt>Line match</dt><dd>{v.match_score?.toFixed(0) ?? "–"} / 100</dd>
        {row.failedChecks.length > 0 && (<><dt>Failed checks</dt><dd>{row.failedChecks.join("; ")}</dd></>)}
        {v.confirmed_by && (row.failedChecks.length === 0 || v.source === "manual") && (<><dt>Confirmed by</dt><dd>{v.confirmed_by}</dd></>)}
        {v.note && (<><dt>Note</dt><dd>{v.note}</dd></>)}
        {v.candidates.length > 0 && (<><dt>Other readings</dt><dd className="num">{v.candidates.map((c) => formatNumber(c)).join("; ")}</dd></>)}
        {v.needs_review && (<><dt>Flagged</dt><dd>The parser is not sure about this value</dd></>)}
      </dl>
      {onSave && <ReviewForm key={`${row.id}:${v.value}`} row={row} unit={unit} saving={saving} error={error} onSave={onSave} />}
    </div>
  );
}

/** Confirm the value as read or type the right one (CIPW-42): checks, ratios and the assessment are computed again. */
function ReviewForm({ row, unit, saving, error, onSave }: Required<Omit<DetailProps, "onSave">> & { onSave: (czk: number) => void }) {
  const v = row.value;
  const initial = v.value === null ? "" : formatAmount(v.value, unit);
  const [text, setText] = useState(initial);
  const amount = parseAmount(text);
  const czk = amount === undefined ? undefined : Math.round(amount * (unit || 1));
  const same = czk === v.value;
  const toLook = row.confidence === "review" || row.confidence === "check";
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (czk !== undefined && (!same || toLook)) onSave(czk);
  };
  return (
    <form className="review-form" onSubmit={submit} aria-label={`Check ${row.name}`}>
      <label className="field">
        <span>{toLook ? "Is this the amount on the page?" : "Correct this value"} ({unitLabel(unit)})</span>
        <input value={text} onChange={(e) => setText(e.target.value)} inputMode="decimal" aria-invalid={czk === undefined} />
        {czk === undefined && <span className="error">Enter a number, e.g. 1 234 or −56</span>}
      </label>
      {v.candidates.length > 0 && (
        <div className="readings">
          Other readings:
          {v.candidates.map((c) => (
            <button key={c} type="button" className="btn small" onClick={() => setText(formatAmount(c, unit))}>{formatAmount(c, unit)}</button>
          ))}
        </div>
      )}
      <div className="actions">
        {same ? (
          toLook && <button type="submit" className="btn primary" disabled={saving}>Confirm value</button>
        ) : (
          <>
            <button type="submit" className="btn primary" disabled={saving || czk === undefined}>Save correction</button>
            <button type="button" className="btn ghost" onClick={() => setText(initial)} disabled={saving}>Undo</button>
          </>
        )}
      </div>
      {saving && <p className="faint" style={{ margin: 0, fontSize: 13 }}>Computing the checks and ratios again…</p>}
      {error && <p className="error" role="alert">{error}</p>}
    </form>
  );
}
