import { useState } from "react";
import { ExternalLink, BookOpen, Code2, Terminal } from "lucide-react";
import { PageHeader, EmptyState } from "@/components/page";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import { useAsync } from "@/lib/hooks";
import { cn } from "@/lib/utils";

interface OpenApiPath {
  [method: string]: { summary?: string; operationId?: string };
}
interface OpenApiSpec {
  paths?: Record<string, OpenApiPath>;
  info?: { title?: string; version?: string };
}

const methodColor: Record<string, string> = {
  get: "bg-info-soft text-info",
  post: "bg-success-soft text-success",
  put: "bg-warning-soft text-warning",
  delete: "bg-danger-soft text-danger",
};

export function Docs() {
  const spec = useAsync(async () => {
    const res = await fetch("/api/openapi.json");
    if (!res.ok) throw new Error(`${res.status}`);
    return (await res.json()) as OpenApiSpec;
  });

  const [q, setQ] = useState("");

  const paths = Object.entries(spec.data?.paths ?? {}).filter(([p]) =>
    p.toLowerCase().includes(q.toLowerCase())
  );

  return (
    <>
      <PageHeader
        title="Docs"
        subtitle="API reference and a quick guide to running LAC from the terminal."
      >
        <a
          href="https://github.com/Dkrynen/lac"
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1.5 text-[13px] text-fg-muted hover:text-fg"
        >
          GitHub <ExternalLink className="h-3.5 w-3.5" />
        </a>
      </PageHeader>

      <Tabs defaultValue="guide">
        <TabsList>
          <TabsTrigger value="guide">
            <BookOpen className="h-3.5 w-3.5" /> Guide
          </TabsTrigger>
          <TabsTrigger value="api">
            <Code2 className="h-3.5 w-3.5" /> API reference
          </TabsTrigger>
        </TabsList>

        <TabsContent value="guide">
          <Card className="prose-chat max-w-none p-5">
            <h2>Quick start</h2>
            <p>
              LAC runs on top of <a href="https://ollama.com">Ollama</a>. Install it, then start the
              server and open the dashboard.
            </p>
            <pre>
              <code>{`# start the engine (one terminal)
python server.py          # serves on http://127.0.0.1:5050

# or use the CLI
python cli.py scan        # hardware scan
python cli.py rec         # recommendations
python cli.py pull llama3.2:3b
python cli.py tui         # terminal UI`}</code>
            </pre>
            <h3>Web app</h3>
            <p>
              This interface is a React app that talks to the Flask API. In development, run{" "}
              <code>npm run dev</code> inside <code>web/</code> (proxies <code>/api</code> to
              Flask). In production it’s built to <code>web/dist</code> and served by Flask.
            </p>
            <h3>Compatibility verdicts</h3>
            <p>
              Every model is tagged <strong>Fits GPU</strong> (≤90% VRAM), <strong>Offload</strong>{" "}
              (partial CPU offload), or <strong>Too large</strong> — matching the colored bars
              across the app.
            </p>
          </Card>
        </TabsContent>

        <TabsContent value="api">
          <input
            placeholder="Filter endpoints…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            className="mb-3 h-9 w-full rounded border border-line bg-panel-2 px-3 text-sm text-fg placeholder:text-fg-faint focus:border-verdant focus:outline-none focus:ring-2 focus:ring-verdant-soft"
          />
          {spec.loading ? (
            <Skeleton className="h-72 w-full" />
          ) : spec.error || paths.length === 0 ? (
            <EmptyState title="Couldn’t load the OpenAPI spec" hint={spec.error ?? "No matching endpoints."} />
          ) : (
            <div className="space-y-1.5">
              {paths.map(([path, ops]) =>
                Object.entries(ops).map(([method, op]) => (
                  <Card key={method + path} className="flex items-center gap-3 p-3">
                    <span
                      className={cn(
                        "w-14 shrink-0 rounded px-2 py-1 text-center font-mono text-[11px] font-semibold uppercase",
                        methodColor[method] ?? "bg-panel-3 text-fg-muted"
                      )}
                    >
                      {method}
                    </span>
                    <code className="font-mono text-[13px] text-fg">{path}</code>
                    <span className="ml-auto truncate text-[12.5px] text-fg-muted">
                      {op.summary ?? op.operationId ?? ""}
                    </span>
                  </Card>
                ))
              )}
            </div>
          )}
          <p className="mt-4 inline-flex items-center gap-1.5 text-[12px] text-fg-faint">
            <Terminal className="h-3.5 w-3.5" /> Full machine-readable spec at{" "}
            <code className="font-mono">/api/openapi.json</code>
          </p>
        </TabsContent>
      </Tabs>
    </>
  );
}
