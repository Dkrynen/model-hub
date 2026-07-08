// Thin client for the LAC Flask API. In dev Vite proxies /api -> :5050;
// in prod Flask serves the built bundle on the same origin.

export class ApiError extends Error {
  status: number;
  body: unknown;

  constructor(status: number, statusText: string, body: unknown) {
    const message =
      body && typeof body === "object" && "error" in body
        ? String((body as { error?: unknown }).error)
        : `${status} ${statusText}`;
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

async function readErrorBody(res: Response): Promise<unknown> {
  const text = await res.text().catch(() => "");
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export async function getJSON<T>(url: string): Promise<T> {
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new ApiError(res.status, res.statusText, await readErrorBody(res));
  return res.json() as Promise<T>;
}

export async function postJSON<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new ApiError(res.status, res.statusText, await readErrorBody(res));
  return res.json() as Promise<T>;
}

export async function putJSON<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new ApiError(res.status, res.statusText, await readErrorBody(res));
  return res.json() as Promise<T>;
}

/**
 * Parse a Server-Sent-Events stream into an async generator of decoded JSON
 * payloads. Stops at the terminal `data: [DONE]` sentinel.
 */
export async function* sse(
  url: string,
  body: unknown,
  signal?: AbortSignal
): AsyncGenerator<Record<string, unknown>> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(`${res.status} ${res.statusText}`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buf.indexOf("\n\n")) !== -1) {
      const chunk = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      for (const line of chunk.split("\n")) {
        const t = line.trim();
        if (!t.startsWith("data:")) continue;
        const data = t.slice(5).trim();
        if (data === "[DONE]") return;
        try {
          yield JSON.parse(data) as Record<string, unknown>;
        } catch {
          /* skip malformed */
        }
      }
    }
  }
}

export const api = {
  scan: () => getJSON<import("./types").ScanInfo>("/api/scan"),
  recommend: (
    params: { vram?: number; use_case?: string; top_k?: number; gpu_mask?: number[]; allow_spill?: boolean } = {}
  ) => {
    const q = new URLSearchParams();
    if (params.vram) q.set("vram", String(params.vram));
    if (params.use_case) q.set("use_case", params.use_case);
    if (params.top_k) q.set("top_k", String(params.top_k));
    if (params.gpu_mask && params.gpu_mask.length > 0) q.set("gpu_mask", params.gpu_mask.join(","));
    if (params.allow_spill === false) q.set("allow_spill", "0");
    return getJSON<import("./types").RecommendResponse>(`/api/recommend?${q}`);
  },
  catalog: () => getJSON<import("./types").CatalogModel[]>("/api/models"),

  ollamaStatus: () => getJSON<import("./types").OllamaStatus>("/api/ollama/status"),
  installed: () => getJSON<import("./types").InstalledModel[]>("/api/ollama/models"),
  ps: () => getJSON<import("./types").PsResponse>("/api/ollama/ps"),
  delete: (model: string) => postJSON<{ success?: boolean; error?: string }>("/api/ollama/delete", { model }),
  /** Preload a model into VRAM (fire-and-forget) so the first chat message isn't slow. */
  warm: (model: string, wait = false) =>
    fetch("/api/ollama/warm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model, wait }),
    }).then((r) => r.json()).catch(() => undefined),

  library: (params: { q?: string; capability?: string; sort?: string; compatible?: string } = {}) => {
    const q = new URLSearchParams();
    if (params.q) q.set("q", params.q);
    if (params.capability) q.set("capability", params.capability);
    if (params.sort) q.set("sort", params.sort);
    if (params.compatible) q.set("compatible", params.compatible);
    return getJSON<import("./types").LibraryBrowseResponse>(`/api/library/browse?${q}`);
  },
  tags: (name: string) =>
    getJSON<import("./types").TagsResponse>(`/api/library/tags?name=${encodeURIComponent(name)}`),
  hfGgufSearch: (q: string, limit = 12) =>
    getJSON<import("./types").HfGgufSearchResponse>(
      `/api/hf/gguf-search?q=${encodeURIComponent(q)}&limit=${limit}`
    ),

  downloads: () => getJSON<import("./types").DownloadEntry[]>("/api/config/downloads"),

  config: () => getJSON<import("./types").AptConfig>("/api/config"),
  saveConfig: (patch: Partial<import("./types").AptConfig>) => putJSON("/api/config", patch),

  version: () => getJSON<import("./types").VersionInfo>("/api/system/version"),
  storage: () => getJSON<import("./types").StorageInfo>("/api/system/storage"),

  /** Stream a model pull. Yields progress payloads ({status,completed,total,...} or {error}). */
  pull(model: string, signal?: AbortSignal) {
    return sse("/api/ollama/pull", { model }, signal);
  },
  /** Stream a chat completion. Yields {message:{content}, done} or {error}. */
  chat(model: string, messages: { role: string; content: string }[], signal?: AbortSignal) {
    return sse("/api/ollama/chat", { model, messages }, signal);
  },
  /** Poll LAC Pro's autopilot status for a just-installed model. */
  proOptimizeStatus: (model: string) =>
    getJSON<{ state: "idle" | "running" | "done" | "failed_silent" | "not_licensed"; tokens_per_second?: number }>(
      `/api/pro/optimize-status?model=${encodeURIComponent(model)}`
    ),
  /** Kick off a LAC Pro custom Hugging Face model import (background). */
  importModel: (repoId: string, quant?: string, filename?: string) =>
    postJSON<{ accepted?: boolean; state?: string; error?: string }>("/api/pro/import-model", {
      repo_id: repoId,
      quant: quant ?? null,
      filename: filename ?? null,
    }),
  /** Poll a custom-model import's progress. */
  importStatus: (repoId: string) =>
    getJSON<{
      state: string;
      error_type?: string;
      message?: string;
      model_name?: string;
      quant?: string;
      current_file?: string;
      bytes_done?: number;
      bytes_total?: number;
      stage?: string;
    }>(
      `/api/pro/import-status?repo_id=${encodeURIComponent(repoId)}`
    ),
  resolveImport: (repoId: string, quant?: string, filename?: string) => {
    const q = new URLSearchParams({ repo_id: repoId });
    if (quant) q.set("quant", quant);
    if (filename) q.set("filename", filename);
    return getJSON<{
      state: string;
      repo_id?: string;
      strategy?: "gguf" | "safetensors";
      selected_file?: string;
      selected_size?: number;
      quant?: string;
      params_b?: number;
      context?: number;
      suggested_gguf_repos?: string[];
      error_type?: string;
      message?: string;
    }>(`/api/pro/import-resolve?${q}`);
  },
  cancelImport: (repoId: string) =>
    postJSON<{ state: string; message?: string }>("/api/pro/import-cancel", { repo_id: repoId }),
  hfTokenStatus: () => getJSON<{ state: string; configured?: boolean }>("/api/pro/hf-token"),
  saveHfToken: (token: string) =>
    postJSON<{ state: string; configured?: boolean; error?: string }>("/api/pro/hf-token", { token }),
  clearHfToken: () =>
    fetch("/api/pro/hf-token", { method: "DELETE", headers: { Accept: "application/json" } }).then((r) => r.json()),
  /** Activate LAC Pro: send a license key → the core route bootstrap-installs the
   *  plugin. Returns the installer's honest result (200 for both outcomes; the
   *  frontend branches on `state`). */
  unlockPro: (key: string) =>
    postJSON<
      | { state: "installed"; path: string }
      | { state: "failed"; error_type: string; message: string }
    >("/api/pro/unlock", { key }),

  proStatus: async () => {
    const r = await fetch("/api/pro/status");
    if (r.status === 404) return { licensed: false, plan: null, expires_human: null, machine: null, checked: null };
    return r.json();
  },

  activatePro: (key: string) =>
    fetch("/api/pro/activate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key }),
    }).then((r) => r.json()),

  appRelaunch: (view: string, bounds?: { x: number; y: number; width: number; height: number }) =>
    fetch("/api/app/relaunch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ view, bounds }),
    }).then((r) => r.json()),

  proTune: (model: string) =>
    fetch("/api/pro/tune", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model }) }).then((r) => r.json()),
  proTuneStatus: (model: string) => fetch(`/api/pro/tune-status?model=${encodeURIComponent(model)}`).then((r) => r.json()),
  proTuneApply: (model: string, num_gpu: number, num_ctx?: number) =>
    fetch("/api/pro/tune-apply", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model, num_gpu, num_ctx }) }).then((r) => r.json()),
  proInsights: (threshold?: number) => fetch(`/api/pro/insights${threshold != null ? `?threshold=${threshold}` : ""}`).then((r) => r.json()),
  proBenchmark: (model: string) =>
    fetch("/api/pro/benchmark", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model }) }).then((r) => r.json()),
  proBenchmarkHistory: (model: string) => fetch(`/api/pro/benchmark-history?model=${encodeURIComponent(model)}`).then((r) => r.json()),
  proAutopilotLog: () => fetch("/api/pro/autopilot-log").then((r) => r.json()),
  proImportHistory: () => fetch("/api/pro/import-history").then((r) => r.json()),
};
