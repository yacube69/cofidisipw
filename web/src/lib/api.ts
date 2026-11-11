import type { AuthState, Company, LineDef, Period, ResultView, Sample, StatementPages, StoredResult } from "../types";

/** Fired when the server answers 401: the session ended, the app shows the sign-in (CIPW-62). */
export const SIGNED_OUT = "fides-signed-out";
const NO_SIGN_IN: AuthState = { mode: "none", user: { name: null, role: "admin" } };

async function json<T>(response: Response): Promise<T> {
  if (response.status === 401) window.dispatchEvent(new Event(SIGNED_OUT));
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

const post = (url: string, body?: unknown) =>
  fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });

/** A server before CIPW-62 has no /api/auth/me: it has no sign-in. */
const authState = (a: AuthState) => (a && typeof a.mode === "string" ? a : NO_SIGN_IN);

export const api = {
  me: () => fetch("/api/auth/me").then((r) => json<AuthState>(r)).then(authState),
  login: (name: string, password: string) => post("/api/auth/login", { name, password }).then((r) => json<AuthState>(r)),
  logout: () => post("/api/auth/logout").then((r) => json<AuthState>(r)).then(authState),
  /** The stored results, newest first (CIPW-60). */
  results: () => fetch("/api/results").then((r) => json<StoredResult[]>(r)).then((list) => (Array.isArray(list) ? list : [])),
  remove: (id: string) => fetch(`/api/results/${encodeURIComponent(id)}`, { method: "DELETE" }).then((r) => json<{ deleted: string }>(r)),
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
