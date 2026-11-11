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
  it("starts on a new analysis and opens a sample from the navigation", async () => {
    render(<App />);
    expect(await screen.findByRole("heading", { level: 1, name: "New analysis" })).toBeTruthy();
    expect(screen.getByText("Drop a PDF or browse")).toBeTruthy();
    const samples = await screen.findByRole("navigation", { name: "Samples" });
    fireEvent.click(within(samples).getByRole("button", { name: "Mondi" }));
    expect(await screen.findByRole("navigation", { name: "Steps" })).toBeTruthy();
    expect(within(samples).getByRole("button", { name: "Mondi" }).getAttribute("aria-current")).toBe("true");
    // back to the form, and to the result again
    fireEvent.click(screen.getByRole("button", { name: "New analysis" }));
    expect(await screen.findByRole("heading", { level: 1, name: "New analysis" })).toBeTruthy();
    fireEvent.click(within(samples).getByRole("button", { name: "Mondi" }));
    expect(await screen.findByRole("navigation", { name: "Steps" })).toBeTruthy();
  });

  it("opens the sample and renders the overview and every step", async () => {
    render(<App />);
    fireEvent.click(within(await screen.findByRole("navigation", { name: "Samples" })).getByRole("button", { name: "Mondi" }));
    const steps = await screen.findByRole("navigation", { name: "Steps" });
    expect(screen.getByRole("heading", { level: 1 })).toBeTruthy();

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
    // real ratios and bankruptcy scores (CIPW-12, CIPW-13): no mock banner; Mondi 2025 is in distress in both models
    expect(screen.queryByText(/mock data of one real company/)).toBeNull();
    expect(screen.getByText("IN05 (Neumaierová & Neumaier)")).toBeTruthy();
    expect(screen.getByText("Altman Z′ (private companies)")).toBeTruthy();
    expect(screen.getAllByText("Distress zone").length).toBe(2);

    fireEvent.click(within(steps).getByText("AI report"));
    expect(await screen.findByText("Strengths")).toBeTruthy();
    // no AI model: the rule-based assessment says so (CIPW-55); the PDF report downloads from the header (CIPW-54)
    expect(screen.getByText(/Rule-based assessment: no AI model is connected/)).toBeTruthy();
    expect(screen.getByRole("link", { name: /Export PDF/ }).getAttribute("href")).toBe("/api/results/mondi-2025/report.pdf");

    // "About this result": the audit block of the run (CIPW-45)
    fireEvent.click(screen.getByRole("button", { name: /About this result/ }));
    const about = document.querySelector("dialog.about") as HTMLDialogElement;
    expect(about.open).toBe(true);
    const audit = sampleView().audit;
    expect(within(about).getByText(audit.settings_hash)).toBeTruthy();
    expect(within(about).getByText(audit.dictionary_version)).toBeTruthy();
    expect(within(about).getByText("Line dictionary")).toBeTruthy();
  });

  it("sends the statement pages the user entered with the PDF", async () => {
    render(<App />);
    await screen.findByRole("heading", { level: 1 });
    const pdf = new File(["%PDF-1.7"], "annual-report.pdf", { type: "application/pdf" });
    fireEvent.change(document.querySelector('input[type="file"]') as HTMLInputElement, { target: { files: [pdf] } });
    const [balanceSheet, incomeStatement] = screen.getAllByPlaceholderText("auto");

    fireEvent.change(balanceSheet, { target: { value: "75 a 76" } });
    expect(screen.getByText("Page numbers or ranges, e.g. 75-76")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Analyse" }));
    expect(vi.mocked(fetch).mock.calls.some(([url]) => url === "/api/analyse")).toBe(false); // not sent

    fireEvent.change(balanceSheet, { target: { value: "75-76" } });
    fireEvent.change(incomeStatement, { target: { value: "77" } });
    fireEvent.click(screen.getByRole("button", { name: "Analyse" }));
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([url]) => url === "/api/analyse")).toBe(true));
    const [, init] = vi.mocked(fetch).mock.calls.find(([url]) => url === "/api/analyse")!;
    const form = (init as RequestInit).body as FormData;
    expect([form.get("balance_sheet_pages"), form.get("income_statement_pages")]).toEqual(["75-76", "77"]);
  });

  it("signs in with a local account, lists the stored results, and a viewer can only read (CIPW-62)", async () => {
    let user: { name: string; role: string } | null = null;
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(`${init?.method ?? "GET"} ${url}`);
      const ok = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
      if (url === "/api/auth/me") return ok({ mode: "local", user });
      if (url === "/api/auth/login") {
        const { name, password } = JSON.parse(init!.body as string);
        if (password !== "correct horse battery") return ok({ detail: "Wrong name or password" }, 401);
        user = { name, role: "viewer" };
        return ok({ mode: "local", user });
      }
      if (url === "/api/auth/logout") {
        user = null;
        return ok({ mode: "local", user });
      }
      if (!user) return ok({ detail: "Sign in first" }, 401);
      if (url === "/api/results") {
        return ok([{ id: "abc123", file: "26161516_2025_statement.pdf", company: "Mondi Štětí a.s.", ico: "26161516", owner: "pdvorak", created_at: "2026-11-12T09:30:00+01:00", updated_at: "2026-11-12T09:30:00+01:00" }]);
      }
      return ok(url === "/api/samples" ? [{ id: "mondi-2025", label: "Mondi" }] : url === "/api/dictionary" ? dictionary : { ...sampleView(), sample: false, owner: "pdvorak" });
    }));
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeTruthy();
    expect(calls.some((c) => c.includes("/api/samples"))).toBe(false); // nothing loads before sign-in

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "jnovak" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "wrong one" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Wrong name or password")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "correct horse battery" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    // signed in as a viewer: the stored results, no upload, no delete, no correction
    const stored = await screen.findByRole("navigation", { name: "Analysed files" });
    expect(within(stored).getByText("Mondi Štětí a.s.")).toBeTruthy();
    expect(screen.getByText("jnovak")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /New analysis/ })).toBeNull();
    expect(screen.getByRole("heading", { name: "Open an analysis" })).toBeTruthy();
    fireEvent.click(within(stored).getByRole("button", { name: /Mondi Štětí a\.s\./ }));
    expect(await screen.findByRole("navigation", { name: "Steps" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
    fireEvent.click(within(screen.getByRole("navigation", { name: "Steps" })).getByText("Extraction"));
    fireEvent.click((await screen.findByRole("table")).querySelectorAll("tbody tr")[0]);
    expect(screen.queryByRole("button", { name: /Confirm value|Save correction/ })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeTruthy();
    expect(calls).toContain("POST /api/auth/logout");
  });

  it("confirms a value to review and corrects another by hand (CIPW-42)", async () => {
    const view = sampleView();
    const posted: { key: string; period: "current"; value: number }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      let body: unknown = url === "/api/samples" ? [{ id: "mondi-2025", label: "Mondi" }] : url === "/api/dictionary" ? dictionary : view;
      if (url === "/api/results/mondi-2025/corrections") {
        const c = JSON.parse(init!.body as string);
        posted.push(c);
        const fixed = structuredClone(view);
        const item = fixed.statements.balance_sheet[c.key][c.period];
        Object.assign(item, { value: c.value, source: "manual", needs_review: false, confirmed_by: "a person in the app" });
        body = fixed;
      }
      return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    }));
    render(<App />);
    fireEvent.click(within(await screen.findByRole("navigation", { name: "Samples" })).getByRole("button", { name: "Mondi" }));
    fireEvent.click(within(await screen.findByRole("navigation", { name: "Steps" })).getByText("Extraction"));
    const table = await screen.findByRole("table");
    const row = (key: string) => within(table).getAllByRole("row")
      .find((r) => r.querySelector("td")?.firstChild?.textContent === key && r.textContent?.includes("Current year"))!;

    // current assets are flagged (a failed check): the reading is right, so the analyst confirms it
    expect(row("current_assets").textContent).toContain("Review");
    fireEvent.click(row("current_assets"));
    expect(screen.getByText(/Is this the amount on the page\?/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Confirm value" }));
    await waitFor(() => expect(row("current_assets").textContent).toContain("Verified"));
    expect(posted[0]).toEqual({ key: "current_assets", period: "current", value: 4_443_661_000 });

    // a confident value can be corrected too: the amount typed in tis. Kč is sent in CZK
    fireEvent.click(row("Total assets")); // named from the dictionary
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "29 457 7x9" } });
    expect(screen.getByText("Enter a number, e.g. 1 234 or −56")).toBeTruthy();
    fireEvent.change(input, { target: { value: "29 457 799" } });
    fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
    await waitFor(() => expect(posted).toHaveLength(2));
    expect(posted[1]).toEqual({ key: "total_assets", period: "current", value: 29_457_799_000 });
  });

  it("shows ARES' name when OCR misread the printed one, and says what the statement reads", async () => {
    render(<App />);
    fireEvent.click(within(await screen.findByRole("navigation", { name: "Samples" })).getByRole("button", { name: "Mondi" }));
    // the scan's OCR reads "Morxfi Štětí a.s"; ARES names the same company (CIPW-56)
    expect(await screen.findByRole("heading", { level: 1, name: "Mondi Štětí a.s." })).toBeTruthy();
    const head = document.querySelector("header.result-head .meta") as HTMLElement;
    expect(within(head).getByText("Name from ARES; the statement reads “Morxfi Štětí a.s”")).toBeTruthy();
  });

  it("keeps the printed name when ARES names another company for the IČO", async () => {
    const view = { ...sampleView(), display_name: "PLOMA, a.s.", name_source: "statement" };
    view.statements.metadata.company_name = "PLOMA, a.s.";
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const body = url === "/api/samples" ? [{ id: "mondi-2025", label: "Mondi" }] : url === "/api/dictionary" ? dictionary : view;
      return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    }));
    render(<App />);
    fireEvent.click(within(await screen.findByRole("navigation", { name: "Samples" })).getByRole("button", { name: "Mondi" }));
    expect(await screen.findByRole("heading", { level: 1, name: "PLOMA, a.s." })).toBeTruthy();
    expect(screen.queryByText(/Name from ARES/)).toBeNull();
  });

  it("names the company from ARES when the statement does not print its name", async () => {
    const view = sampleView();
    view.statements.metadata.company_name = null;
    view.company = {
      ico: "26161516", name: "Mondi Štětí a.s.", legal_form: "Akciová společnost", address: "Litoměřická 272, 41108 Štětí",
      founded: "2000-03-06", nace: "17120", nace_name: "Výroba papíru a lepenky", nace_section: "C",
      nace_section_name: "Zpracovatelský průmysl", nace_2008: "17120", employees: "1000 - 1499 zaměstnanců",
      source: "snapshot", retrieved: "2026-11-01",
    };
    const ploma = { ...view.company, ico: "25556045", name: "PLOMA, a.s.", nace: "16200", nace_name: "Výroba dřevěných výrobků" };
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.startsWith("/api/company/")) {
        return url.endsWith("25556045")
          ? new Response(JSON.stringify(ploma), { status: 200, headers: { "Content-Type": "application/json" } })
          : new Response(JSON.stringify({ detail: "IČO 27920992 is not in ARES" }), { status: 404 });
      }
      const body = url === "/api/samples" ? [{ id: "mondi-2025", label: "Mondi" }] : url === "/api/dictionary" ? dictionary : view;
      return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    }));
    render(<App />);
    fireEvent.click(within(await screen.findByRole("navigation", { name: "Samples" })).getByRole("button", { name: "Mondi" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Mondi Štětí a.s." })).toBeTruthy();
    const head = document.querySelector("header.result-head .meta") as HTMLElement;
    expect(within(head).getByText("Name from ARES")).toBeTruthy();
    expect(within(head).getByText("Výroba papíru a lepenky")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /About this result/ }));
    const about = document.querySelector("dialog.about") as HTMLDialogElement;
    expect(within(about).getByText("Company in ARES")).toBeTruthy();
    expect(within(about).getByText("Akciová společnost")).toBeTruthy();
    expect(within(about).getByText(/dataset snapshot \(offline\)/)).toBeTruthy();
    about.close();

    // the Ratios step: the result's own company, then the one of an IČO the user corrects
    fireEvent.click(within(screen.getByRole("navigation", { name: "Steps" })).getByText("Ratios"));
    const field = await screen.findByPlaceholderText("26161516");
    expect(screen.getByText(/Mondi Štětí a\.s\. · Výroba papíru a lepenky \(CZ-NACE 17120\)/)).toBeTruthy();
    fireEvent.change(field, { target: { value: "25556045" } });
    expect(await screen.findByText(/PLOMA, a\.s\. · Výroba dřevěných výrobků/)).toBeTruthy();
    fireEvent.change(field, { target: { value: "27920992" } });
    expect(await screen.findByText("IČO 27920992 is not in ARES")).toBeTruthy();
  });

  it("shows a card for every bankruptcy model, with its probability when the model gives one", async () => {
    const view = sampleView();
    const probit = {
      ...view.bankruptcy.models[0], key: "probit_test", name: "12-month default model", horizon: "12 months",
      current: { ...view.bankruptcy.models[0].current, score: 0.4588, probability: 0.6768, zone: "distress" },
    };
    view.bankruptcy.models.push(probit);
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const body = url === "/api/samples" ? [{ id: "mondi-2025", label: "Mondi" }] : url === "/api/dictionary" ? dictionary : view;
      return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    }));
    render(<App />);
    fireEvent.click(within(await screen.findByRole("navigation", { name: "Samples" })).getByRole("button", { name: "Mondi" }));
    fireEvent.click(within(await screen.findByRole("navigation", { name: "Steps" })).getByText("Ratios"));
    expect(await screen.findByText("12-month default model")).toBeTruthy();
    expect(screen.getByText("Probability of bankruptcy within 12 months: 67,7 %")).toBeTruthy();
    expect(screen.getAllByText("Distress zone").length).toBe(3);
  });

  it("says so when the service is not reachable", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("down", { status: 502, statusText: "Bad Gateway" })));
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringContaining("not reachable"));
  });
});
