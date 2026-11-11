import type { Company, LineDef, Period, ResultView, Sample, StatementPages } from "../types";

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // not JSON: keep the status line
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export const api = {
  samples: () => fetch("/api/samples").then((r) => json<Sample[]>(r)),
  dictionary: () => fetch("/api/dictionary").then((r) => json<LineDef[]>(r)),
  company: (ico: string) => fetch(`/api/company/${encodeURIComponent(ico)}`).then((r) => json<Company>(r)),
  result: (id: string) => fetch(`/api/results/${encodeURIComponent(id)}`).then((r) => json<ResultView>(r)),
  analyse: (file: File, scope: "individual" | "consolidated", pages: StatementPages, signal?: AbortSignal) => {
    const form = new FormData();
    form.append("file", file);
    form.append("scope", scope);
    form.append("balance_sheet_pages", pages.balanceSheet);
    form.append("income_statement_pages", pages.incomeStatement);
    return fetch("/api/analyse", { method: "POST", body: form, signal }).then((r) => json<ResultView>(r));
  },
  /** Confirm or correct one value by hand (CIPW-42); the result comes back computed again. */
  correct: (id: string, body: { key: string; period: Period; value: number | null; reason?: string }) =>
    fetch(`/api/results/${encodeURIComponent(id)}/corrections`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<ResultView>(r)),
  pageUrl: (id: string, page: number) => `/api/results/${encodeURIComponent(id)}/pages/${page}`,
  exportUrl: (id: string) => `/api/results/${encodeURIComponent(id)}/export`,
  reportUrl: (id: string) => `/api/results/${encodeURIComponent(id)}/report.pdf`,
};
