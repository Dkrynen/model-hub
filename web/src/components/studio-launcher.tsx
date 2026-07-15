import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Bot, Compass, FileText, Hammer, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { studioLaunchNavigation, type StudioMode } from "@/lib/studio-launch";

const MODES: { id: StudioMode; label: string; icon: typeof Bot }[] = [
  { id: "ask", label: "Ask", icon: Bot },
  { id: "plan", label: "Plan", icon: FileText },
  { id: "explore", label: "Explore", icon: Compass },
  { id: "build", label: "Build", icon: Hammer },
];

export function StudioLauncher() {
  const navigate = useNavigate();
  const [prompt, setPrompt] = useState("");
  const [mode, setMode] = useState<StudioMode>("plan");

  const openStudio = () => {
    const navigation = studioLaunchNavigation({ mode, prompt });
    navigate(navigation.path, { state: navigation.state });
  };

  return (
    <Card className="mb-5 overflow-hidden border-line-strong bg-gradient-to-br from-panel via-panel to-verdant-soft/20">
      <div className="grid gap-0 lg:grid-cols-[minmax(0,1fr)_220px]">
        <div className="p-4 sm:p-5">
          <div className="mb-3 flex items-center gap-2">
            <span className="flex h-8 w-8 items-center justify-center rounded-md bg-verdant-soft text-verdant">
              <Bot className="h-4 w-4" />
            </span>
            <div>
              <h2 className="text-[15px] font-semibold">Start in Studio</h2>
              <p className="text-[12px] text-fg-muted">Turn an idea into a project-bound Ollama draft.</p>
            </div>
          </div>

          <label htmlFor="studio-launch-prompt" className="text-[11px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
            What do you want to work on?
          </label>
          <textarea
            id="studio-launch-prompt"
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            onKeyDown={(event) => {
              if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && prompt.trim()) {
                event.preventDefault();
                openStudio();
              }
            }}
            rows={3}
            maxLength={4_000}
            placeholder="Plan a feature, inspect a codebase, or draft the safest implementation path…"
            className="mt-2 w-full resize-y rounded-md border border-line bg-panel-2 px-3 py-2.5 text-[14px] text-fg outline-none placeholder:text-fg-faint focus:border-verdant focus:ring-1 focus:ring-verdant"
          />

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <div role="group" aria-label="Studio mode" className="flex flex-wrap gap-1 rounded-md border border-line bg-panel-2 p-1">
              {MODES.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  aria-pressed={mode === item.id}
                  onClick={() => setMode(item.id)}
                  className={cn(
                    "flex min-h-9 items-center gap-1.5 rounded px-2.5 text-[12px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-verdant",
                    mode === item.id ? "bg-panel-3 text-fg" : "text-fg-muted hover:text-fg",
                  )}
                >
                  <item.icon className="h-3.5 w-3.5" />
                  {item.label}
                </button>
              ))}
            </div>
            <Button className="ml-auto" disabled={!prompt.trim()} onClick={openStudio}>
              Open draft <ArrowRight />
            </Button>
          </div>
        </div>

        <div className="border-t border-line bg-panel-2/60 p-4 lg:border-l lg:border-t-0">
          <div className="flex items-center gap-2 text-[12px] font-semibold text-fg">
            <ShieldCheck className="h-4 w-4 text-success" /> Draft stays private
          </div>
          <p className="mt-2 text-[12px] leading-relaxed text-fg-muted">
            This creates a draft only. Studio never sends or runs it until you select a registered project and confirm the action.
          </p>
          <p className="mt-3 font-mono text-[10.5px] text-fg-faint">Ctrl/⌘ + Enter · open draft</p>
        </div>
      </div>
    </Card>
  );
}
