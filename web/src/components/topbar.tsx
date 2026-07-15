import { useState, type RefObject } from "react";
import { useNavigate } from "react-router-dom";
import { Search, Sun, Moon, Activity, Menu } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { useTheme } from "@/components/theme";
import { useAsync, useInterval } from "@/lib/hooks";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

type TopbarProps = {
  mobileOpen?: boolean;
  onMenu?: () => void;
  menuButtonRef?: RefObject<HTMLButtonElement>;
};

export function Topbar({
  mobileOpen = false,
  onMenu = () => undefined,
  menuButtonRef,
}: TopbarProps) {
  const navigate = useNavigate();
  const { theme, toggle } = useTheme();
  const [q, setQ] = useState("");

  const status = useAsync(() => api.ollamaStatus().catch(() => null));
  useInterval(() => status.reload(), 10000);

  const online = status.data?.running;

  return (
    <header className="glass sticky top-0 z-30 flex h-[var(--topbar-h)] min-w-0 items-center gap-2 border-b border-line px-3 sm:gap-3 sm:px-5">
      <Button
        ref={menuButtonRef}
        variant="ghost"
        size="icon"
        className="shrink-0 md:hidden"
        onClick={onMenu}
        aria-label="Open navigation"
        aria-controls="primary-navigation"
        aria-expanded={mobileOpen}
        title="Open navigation"
      >
        <Menu />
      </Button>
      <form
        className="relative min-w-0 max-w-md flex-1"
        onSubmit={(e) => {
          e.preventDefault();
          navigate(`/browse${q.trim() ? `?q=${encodeURIComponent(q.trim())}` : ""}`);
        }}
      >
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-faint" />
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search models…"
          className="h-[var(--control-h)] pl-9 pr-16"
        />
        <kbd className="pointer-events-none absolute right-2.5 top-1/2 hidden -translate-y-1/2 items-center gap-0.5 rounded border border-line bg-panel-3 px-1.5 py-0.5 font-mono text-[10px] text-fg-muted sm:inline-flex">
          /
        </kbd>
      </form>

      <div className="ml-auto flex shrink-0 items-center gap-2">
        {online ? (
          <div
            className={cn(
              "hidden items-center gap-1.5 rounded-pill border px-2.5 py-1 text-[12px] font-medium sm:inline-flex",
              "border-line bg-panel-2 text-fg-muted"
            )}
            title={`Ollama ${status.data?.version ?? ""}`}
          >
            <Activity className="h-3.5 w-3.5" />
            <span className={cn("h-1.5 w-1.5 rounded-full", "bg-success")} />
            Ollama online
          </div>
        ) : (
          <a
            href="https://ollama.com/download"
            target="_blank"
            rel="noreferrer"
            className={cn(
              "hidden items-center gap-1.5 rounded-pill border px-2.5 py-1 text-[12px] font-medium sm:inline-flex",
              "border-warning/30 bg-warning-soft text-warning"
            )}
            title="Ollama offline — click to install"
          >
            <Activity className="h-3.5 w-3.5" />
            <span className={cn("h-1.5 w-1.5 rounded-full", "bg-warning")} />
            Ollama offline — install
          </a>
        )}

        <Button variant="ghost" size="icon" onClick={toggle} aria-label="Toggle theme">
          {theme === "dark" ? <Sun /> : <Moon />}
        </Button>
      </div>
    </header>
  );
}
