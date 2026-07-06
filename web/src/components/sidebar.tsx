import { NavLink, Link } from "react-router-dom";
import {
  LayoutDashboard,
  Search,
  Cpu,
  Boxes,
  MessageSquare,
  Download,
  Sparkles,
  BookOpen,
  Settings,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useAsync } from "@/lib/hooks";
import { api } from "@/lib/api";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/browse", label: "Browse models", icon: Search, end: false },
  { to: "/scan", label: "Scan & recommend", icon: Cpu, end: false },
  { to: "/installed", label: "Installed", icon: Boxes, end: false },
  { to: "/chat", label: "Chat", icon: MessageSquare, end: false },
  { to: "/downloads", label: "Downloads", icon: Download, end: false },
  { to: "/pro", label: "Pro", icon: Sparkles, end: false },
];

export function Sidebar() {
  const { data: version } = useAsync(() => api.version().catch(() => null));
  return (
    <aside className="sticky top-0 flex h-screen w-[232px] shrink-0 flex-col border-r border-line bg-panel">
      <Link to="/" className="block px-4 pb-4 pt-[18px]">
        <div className="font-mono text-[19px] font-semibold leading-none tracking-tight">
          lac<span className="ml-0.5 inline-block h-[16px] w-[7px] translate-y-[2px] animate-blink rounded-[1px] bg-verdant" />
        </div>
        <div className="mt-1.5 text-[10px] uppercase tracking-[0.14em] text-fg-faint">
          Local AI, sorted.
        </div>
      </Link>

      <nav className="flex flex-col gap-0.5 px-2.5">
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-2.5 rounded px-2.5 py-2 text-[13.5px] font-medium transition-colors",
                isActive ? "bg-verdant-soft text-verdant" : "text-fg-muted hover:bg-panel-3 hover:text-fg"
              )
            }
          >
            <item.icon className="h-[15px] w-[15px]" />
            {item.label}
          </NavLink>
        ))}

        <div className="mt-3 px-2.5 pb-1 text-[10px] uppercase tracking-[0.12em] text-fg-faint">
          Library
        </div>
        <NavLink
          to="/docs"
          className={({ isActive }) =>
            cn(
              "flex items-center gap-2.5 rounded px-2.5 py-2 text-[13.5px] font-medium transition-colors",
              isActive ? "bg-verdant-soft text-verdant" : "text-fg-muted hover:bg-panel-3 hover:text-fg"
            )
          }
        >
          <BookOpen className="h-[15px] w-[15px]" />
          Docs
        </NavLink>
        <NavLink
          to="/settings"
          className={({ isActive }) =>
            cn(
              "flex items-center gap-2.5 rounded px-2.5 py-2 text-[13.5px] font-medium transition-colors",
              isActive ? "bg-verdant-soft text-verdant" : "text-fg-muted hover:bg-panel-3 hover:text-fg"
            )
          }
        >
          <Settings className="h-[15px] w-[15px]" />
          Settings
        </NavLink>
      </nav>

      <div className="mt-auto px-3 pb-3">
        <div className="rounded-lg border border-line bg-panel-2 px-3 py-2 text-[11.5px] text-fg-faint">
          LAC {version?.version ? `v${version.version}` : ""}
        </div>
      </div>
    </aside>
  );
}
