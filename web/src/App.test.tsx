import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

// The saved sample result as the API serves it (src/api.py _view): without page_words, plus id and page sizes
function sampleView() {
  const raw = JSON.parse(readFileSync(resolve(__dirname, "../../data/fixtures/result_26161516_2025.json"), "utf8"));
  const pageSizes = Object.fromEntries(raw.page_words.map((pw: { page: number; width: number; height: number }) => [pw.page, [pw.width, pw.height]]));
  const stubbed = raw.stages.filter((s: { status: string }) => s.status === "stub").map((s: { stage: string }) => s.stage);
  delete raw.page_words;
  return { ...raw, id: "mondi-2025", page_sizes: pageSizes, stubbed_stages: stubbed };
}

const dictionary = [
  { key: "total_assets", statement: "balance_sheet", side: "assets", code: "", label: "AKTIVA CELKEM", en: "Total assets" },
];

beforeEach(() => {
  window.location.hash = "";
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const body = url === "/api/samples" ? [{ id: "mondi-2025", label: "Mondi" }]
      : url === "/api/dictionary" ? dictionary
      : sampleView();
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
  Element.prototype.scrollIntoView = vi.fn();
  HTMLDialogElement.prototype.showModal ??= function (this: HTMLDialogElement) { this.open = true; };
  HTMLDialogElement.prototype.close ??= function (this: HTMLDialogElement) { this.open = false; };
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("App", () => {
  it("opens the sample and renders the overview and every step", async () => {
    render(<App />);
    expect(await screen.findByRole("heading", { level: 1 })).toBeTruthy();
    const steps = screen.getByRole("navigation", { name: "Steps" });

    // overview: one card per step and the attention list
    for (const title of ["Extraction", "Validation", "Ratios", "AI report"]) {
      expect(screen.getAllByText(title).length).toBeGreaterThan(0);
    }
    expect(screen.getByText("Needs attention")).toBeTruthy();

    fireEvent.click(within(steps).getByText("Extraction"));
    const table = await screen.findByRole("table");
    expect(within(table).getAllByText("Total assets").length).toBeGreaterThan(0);
    // clicking a value highlights its box on the page and shows where it came from
    fireEvent.click(within(table).getAllByRole("row")[1]);
    await waitFor(() => expect(document.querySelector(".vbox.selected")).not.toBeNull());
    expect(screen.getByText("Printed text")).toBeTruthy();

    fireEvent.click(within(steps).getByText("Validation"));
    expect(await screen.findByText("Checks passed")).toBeTruthy();

    fireEvent.click(within(steps).getByText("Ratios"));
    expect(await screen.findByText("Liquidity")).toBeTruthy();
    expect(screen.getByText(/mock data of one real company/)).toBeTruthy();

    fireEvent.click(within(steps).getByText("AI report"));
    expect(await screen.findByText("Strengths")).toBeTruthy();

    // "About this result": the audit block of the run (CIPW-45)
    fireEvent.click(screen.getByRole("button", { name: /About this result/ }));
    const about = document.querySelector("dialog.about") as HTMLDialogElement;
    expect(about.open).toBe(true);
    const audit = sampleView().audit;
    expect(within(about).getByText(audit.settings_hash)).toBeTruthy();
    expect(within(about).getByText(audit.dictionary_version)).toBeTruthy();
    expect(within(about).getByText("Line dictionary")).toBeTruthy();
  });

  it("says so when the service is not reachable", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("down", { status: 502, statusText: "Bad Gateway" })));
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringContaining("not reachable"));
  });
});
