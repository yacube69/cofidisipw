import type { LineDef, ResultView, Sample } from "../types";

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
  result: (id: string) => fetch(`/api/results/${encodeURIComponent(id)}`).then((r) => json<ResultView>(r)),
  analyse: (file: File, scope: "individual" | "consolidated", signal?: AbortSignal) => {
    const form = new FormData();
    form.append("file", file);
    form.append("scope", scope);
    form.append("skip_report", "true");
    return fetch("/api/analyse", { method: "POST", body: form, signal }).then((r) => json<ResultView>(r));
  },
  pageUrl: (id: string, page: number) => `/api/results/${encodeURIComponent(id)}/pages/${page}`,
  exportUrl: (id: string) => `/api/results/${encodeURIComponent(id)}/export`,
};
