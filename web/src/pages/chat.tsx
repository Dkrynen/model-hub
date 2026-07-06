import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Send, Square, Trash2, Sparkles, Settings2 } from "lucide-react";
import { toast } from "sonner";
import { PageHeader, EmptyState } from "@/components/page";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Markdown } from "@/components/markdown";
import { useAsync } from "@/lib/hooks";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Msg {
  role: "user" | "assistant" | "system";
  content: string;
}

const SUGGESTIONS = [
  "Explain GPU offloading in one paragraph.",
  "Write a haiku about local models.",
  "What model fits 8 GB VRAM for coding?",
  "Summarize the pros of running LLMs locally.",
];

export function Chat() {
  const [params] = useSearchParams();
  const installed = useAsync(() => api.installed());

  const models = (installed.data ?? []).map((m) => m.name);
  const [model, setModel] = useState(params.get("model") ?? "");
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [system, setSystem] = useState("");
  const [showSystem, setShowSystem] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!model && models.length) setModel(models[0]);
  }, [model, models]);

  useEffect(() => {
    if (model) api.warm(model);
  }, [model]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const send = async (text: string) => {
    if (!model) {
      toast.error("Select a model first");
      return;
    }
    if (!text.trim() || streaming) return;

    const history: Msg[] = system ? [{ role: "system", content: system }, ...messages, { role: "user", content: text }] : [...messages, { role: "user", content: text }];
    setMessages([...messages, { role: "user", content: text }, { role: "assistant", content: "" }]);
    setInput("");
    setStreaming(true);

    const ac = new AbortController();
    abortRef.current = ac;

    try {
      let acc = "";
      for await (const ev of api.chat(model, history as { role: string; content: string }[], ac.signal)) {
        if (ev.error) throw new Error(String(ev.error));
        const delta = (ev.message as { content?: string } | undefined)?.content ?? "";
        if (delta) {
          acc += delta;
          setMessages((prev) => {
            const next = [...prev];
            next[next.length - 1] = { role: "assistant", content: acc };
            return next;
          });
        }
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        toast.error("Chat error", { description: e instanceof Error ? e.message : String(e) });
      }
    } finally {
      setStreaming(false);
      abortRef.current = null;
    }
  };

  const stop = () => abortRef.current?.abort();
  const clear = () => setMessages([]);

  return (
    <>
      <PageHeader title="Chat" subtitle="Stream responses from your local models.">
        <Button variant="ghost" size="sm" onClick={() => setShowSystem((s) => !s)}>
          <Settings2 /> System
        </Button>
        <Button variant="ghost" size="sm" onClick={clear} disabled={!messages.length}>
          <Trash2 /> Clear
        </Button>
      </PageHeader>

      {showSystem && (
        <Card className="mb-3 p-3">
          <Input
            placeholder="System prompt (optional)…"
            value={system}
            onChange={(e) => setSystem(e.target.value)}
          />
        </Card>
      )}

      <div className="flex h-[calc(100vh-220px)] min-h-[360px] flex-col rounded-lg border border-line bg-panel">
        {/* model bar */}
        <div className="flex items-center gap-2 border-b border-line px-3 py-2">
          <span className="text-[12px] uppercase tracking-[0.08em] text-fg-faint">Model</span>
          {installed.loading ? (
            <Skeleton className="h-7 w-40" />
          ) : models.length ? (
            <Select value={model} onValueChange={setModel}>
              <SelectTrigger className="h-8 w-[220px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {models.map((m) => (
                  <SelectItem key={m} value={m}>
                    {m}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <span className="text-[13px] text-fg-muted">No models installed</span>
          )}
        </div>

        {/* messages */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto p-4">
          {messages.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center text-center">
              <Sparkles className="mb-3 h-7 w-7 text-verdant" />
              <p className="text-sm font-medium">Start a conversation</p>
              <div className="mt-4 grid w-full max-w-lg grid-cols-1 gap-2 sm:grid-cols-2">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => send(s)}
                    className="rounded-lg border border-line bg-panel-2 px-3 py-2 text-left text-[13px] text-fg-muted transition-colors hover:border-line-strong hover:text-fg"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="mx-auto max-w-3xl space-y-5">
              {messages.map((m, i) => (
                <Bubble key={i} role={m.role} content={m.content} model={model} />
              ))}
            </div>
          )}
        </div>

        {/* composer */}
        <div className="border-t border-line p-3">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              send(input);
            }}
            className="flex items-center gap-2"
          >
            <Input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={model ? `Message ${model}…` : "Install a model to start chatting"}
              disabled={!model || streaming}
            />
            {streaming ? (
              <Button type="button" variant="secondary" onClick={stop}>
                <Square /> Stop
              </Button>
            ) : (
              <Button type="submit" disabled={!model || !input.trim()}>
                <Send /> Send
              </Button>
            )}
          </form>
        </div>
      </div>
    </>
  );
}

function Bubble({ role, content, model }: { role: string; content: string; model: string }) {
  const user = role === "user";
  return (
    <div className={cn("flex gap-3", user && "flex-row-reverse")}>
      <div
        className={cn(
          "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-pill text-[11px] font-semibold",
          user ? "bg-verdant text-verdant-fg" : "bg-panel-3 text-fg-muted"
        )}
      >
        {user ? "You" : "A"}
      </div>
      <div className={cn("min-w-0 max-w-[80%]", user && "text-right")}>
        <div className={cn("mb-1 text-[11px] text-fg-faint", user && "hidden")}>{model}</div>
        <div
          className={cn(
            "rounded-lg px-3.5 py-2.5 text-[14px]",
            user ? "bg-verdant text-verdant-fg" : "bg-panel-2 text-fg"
          )}
        >
          {user ? content : <Markdown text={content || "…"} />}
        </div>
      </div>
    </div>
  );
}
