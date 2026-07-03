// Thin client for the Apt Flask API. In dev Vite proxies /api -> :5050;
// in prod Flask serves the built bundle on the same origin.

export async function getJSON<T>(url: string): Promise<T> {
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
}

export async function postJSON<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
}

export async function putJSON<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
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

  downloads: () => getJSON<import("./types").DownloadEntry[]>("/api/config/downloads"),

  config: () => getJSON<import("./types").AptConfig>("/api/config"),
  saveConfig: (patch: Partial<import("./types").AptConfig>) => putJSON("/api/config", patch),

  version: () => getJSON<import("./types").VersionInfo>("/api/system/version"),

  /** Stream a model pull. Yields progress payloads ({status,completed,total,...} or {error}). */
  pull(model: string, signal?: AbortSignal) {
    return sse("/api/ollama/pull", { model }, signal);
  },
  /** Stream a chat completion. Yields {message:{content}, done} or {error}. */
  chat(model: string, messages: { role: string; content: string }[], signal?: AbortSignal) {
    return sse("/api/ollama/chat", { model, messages }, signal);
  },
  /** Stream a benchmark run. Yields {run,tokens_per_second,...} frames then {done:true,median_tps,runs}. */
  benchmark(model: string, opts: { repeat?: number } = {}, signal?: AbortSignal) {
    return sse("/api/benchmark", { model, repeat: opts.repeat ?? 2 }, signal);
  },
};
