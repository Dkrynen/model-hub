import { NavLink, Link } from "react-router-dom";
import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type RefObject } from "react";
import {
  LayoutDashboard,
  Search,
  Cpu,
  Boxes,
  MessageSquare,
  Activity,
  Download,
  Sparkles,
  BookOpen,
  Settings,
  UserRound,
  Cloud,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useAsync } from "@/lib/hooks";
import { api } from "@/lib/api";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/browse", label: "Browse models", icon: Search, end: false },
  { to: "/scan", label: "Scan & recommend", icon: Cpu, end: false },
  { to: "/installed", label: "Installed", icon: Boxes, end: false },
  { to: "/studio", label: "Studio", icon: MessageSquare, end: false },
  { to: "/lab", label: "Lab", icon: Activity, end: false },
  { to: "/downloads", label: "Downloads", icon: Download, end: false },
  { to: "/pro", label: "Pro tools", icon: Sparkles, end: false },
  { to: "/cloud", label: "Cloud activity", icon: Cloud, end: false },
  { to: "/account", label: "Account", icon: UserRound, end: false },
];

const DESKTOP_NAV_QUERY = "(min-width: 768px)";
const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

function useDesktopNavigation() {
  const [isDesktop, setIsDesktop] = useState(() => (
    typeof window !== "undefined" && window.matchMedia(DESKTOP_NAV_QUERY).matches
  ));

  useEffect(() => {
    const query = window.matchMedia(DESKTOP_NAV_QUERY);
    const update = () => setIsDesktop(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  return isDesktop;
}

export function Sidebar({ mobileOpen = false, onClose = () => undefined, menuButtonRef }: {
  mobileOpen?: boolean;
  onClose?: () => void;
  menuButtonRef?: RefObject<HTMLButtonElement>;
}) {
  const { data: version } = useAsync(() => api.version().catch(() => null));
  const isDesktop = useDesktopNavigation();
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const mobileDrawerClosed = !isDesktop && !mobileOpen;
  const drawerTabIndex = mobileDrawerClosed ? -1 : undefined;

  const closeMobileDrawer = useCallback(() => {
    if (isDesktop) return;
    onClose();
    menuButtonRef?.current?.focus();
  }, [isDesktop, menuButtonRef, onClose]);

  useEffect(() => {
    if (!isDesktop && mobileOpen) closeButtonRef.current?.focus();
  }, [isDesktop, mobileOpen]);

  const handleDrawerKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (isDesktop || !mobileOpen) return;

    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      closeMobileDrawer();
      return;
    }

    if (event.key !== "Tab") return;
    const focusable = Array.from(
      event.currentTarget.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
    ).filter((element) => element.getAttribute("aria-hidden") !== "true");
    const first = focusable[0];
    const last = focusable.at(-1);
    if (!first || !last) {
      event.preventDefault();
      return;
    }

    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    } else if (!event.currentTarget.contains(document.activeElement)) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <>
      {mobileOpen && !isDesktop && (
        <button
          type="button"
          aria-label="Close navigation"
          className="fixed inset-0 z-40 bg-black/60 md:hidden"
          onClick={closeMobileDrawer}
        />
      )}
      <aside
        id="primary-navigation"
        aria-label="Primary navigation"
        {...(mobileDrawerClosed ? { inert: "", "aria-hidden": true } : {})}
        onKeyDown={handleDrawerKeyDown}
        className={cn(
          "fixed inset-y-0 left-0 z-50 flex h-screen w-[var(--sidebar-w)] shrink-0 flex-col border-r border-line bg-panel transition-transform duration-200 md:sticky md:top-0 md:z-auto md:translate-x-0",
          mobileOpen ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="relative">
          <Link
            to="/"
            tabIndex={drawerTabIndex}
            onClick={closeMobileDrawer}
            className="block px-4 pb-4 pt-[18px]"
          >
            <div className="font-mono text-[19px] font-semibold leading-none tracking-tight">
              lac<span className="ml-0.5 inline-block h-[16px] w-[7px] translate-y-[2px] animate-blink rounded-[1px] bg-verdant" />
            </div>
            <div className="mt-1.5 text-[10px] uppercase tracking-[0.14em] text-fg-faint">
              Local AI, sorted.
            </div>
          </Link>
          <button
            ref={closeButtonRef}
            type="button"
            aria-label="Close navigation"
            title="Close navigation"
            tabIndex={drawerTabIndex}
            className="absolute right-2 top-3 flex h-9 w-9 items-center justify-center rounded text-fg-muted hover:bg-panel-3 hover:text-fg md:hidden"
            onClick={closeMobileDrawer}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

      <nav className="min-h-0 flex-1 overflow-y-auto flex flex-col gap-0.5 px-2.5">
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            tabIndex={drawerTabIndex}
            onClick={closeMobileDrawer}
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
          tabIndex={drawerTabIndex}
          onClick={closeMobileDrawer}
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
          tabIndex={drawerTabIndex}
          onClick={closeMobileDrawer}
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
    </>
  );
}
