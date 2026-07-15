import { useEffect, useRef, useState } from "react";
import { Routes, Route, Navigate, useLocation, useNavigate } from "react-router-dom";
import { Toaster } from "sonner";
import { Sidebar } from "@/components/sidebar";
import { Topbar } from "@/components/topbar";
import { useTheme } from "@/components/theme";
import { cn } from "@/lib/utils";
import { Dashboard } from "@/pages/dashboard";
import { Browse } from "@/pages/browse";
import { Scan } from "@/pages/scan";
import { Installed } from "@/pages/installed";
import { Studio } from "@/pages/studio";
import { Downloads } from "@/pages/downloads";
import { Lab } from "@/pages/lab";
import { Docs } from "@/pages/docs";
import { Settings } from "@/pages/settings";
import { Pro } from "./pages/pro";
import { Account } from "./pages/account";
import { CloudActivity } from "./pages/cloud-activity";
import { LegacyRouteRedirect } from "@/components/legacy-route-redirect";

export default function App() {
  const { theme } = useTheme();
  const navigate = useNavigate();
  const location = useLocation();
  const isWorkbench = location.pathname === "/studio" || location.pathname === "/chat";
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const menuButtonRef = useRef<HTMLButtonElement>(null);

  // On boot, a relaunch after Pro activation passes `?view=<path>` so the
  // window lands back where it left off instead of the dashboard.
  useEffect(() => {
    const view = new URLSearchParams(location.search).get("view");
    if (view) navigate(`/${view}`, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Global keybind: "/" focuses the search box.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (e.key === "/" && tag !== "INPUT" && tag !== "TEXTAREA") {
        const el = document.querySelector<HTMLInputElement>('input[placeholder="Search models…"]');
        if (el) {
          e.preventDefault();
          el.focus();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar
        mobileOpen={mobileNavOpen}
        onClose={() => setMobileNavOpen(false)}
        menuButtonRef={menuButtonRef}
      />
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar
          mobileOpen={mobileNavOpen}
          onMenu={() => setMobileNavOpen(true)}
          menuButtonRef={menuButtonRef}
        />
        <main className="min-w-0 flex-1 overflow-x-hidden overflow-y-auto">
          <div
            className={cn(
              "mx-auto min-w-0 w-full px-[var(--app-pad-x)] py-[var(--app-pad-y)]",
              isWorkbench ? "max-w-none" : "max-w-[1180px]"
            )}
          >
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/browse" element={<Browse />} />
              <Route path="/scan" element={<Scan />} />
              <Route path="/installed" element={<Installed />} />
              <Route path="/studio" element={<Studio />} />
              <Route path="/chat" element={<LegacyRouteRedirect to="/studio" preserveLocation />} />
              <Route path="/lab" element={<Lab />} />
              <Route path="/performance" element={<LegacyRouteRedirect to="/lab" preserveLocation />} />
              <Route path="/downloads" element={<Downloads />} />
              <Route path="/pro" element={<Pro />} />
              <Route path="/account" element={<Account />} />
              <Route path="/cloud" element={<CloudActivity />} />
              <Route path="/docs" element={<Docs />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </div>
        </main>
      </div>
      <Toaster theme={theme} position="bottom-right" richColors closeButton />
    </div>
  );
}
