import { useEffect } from "react";
import { Routes, Route, Navigate, useNavigate } from "react-router-dom";
import { Toaster } from "sonner";
import { Sidebar } from "@/components/sidebar";
import { Topbar } from "@/components/topbar";
import { useTheme } from "@/components/theme";
import { Dashboard } from "@/pages/dashboard";
import { Browse } from "@/pages/browse";
import { Scan } from "@/pages/scan";
import { Installed } from "@/pages/installed";
import { Chat } from "@/pages/chat";
import { Downloads } from "@/pages/downloads";
import { Docs } from "@/pages/docs";
import { Settings } from "@/pages/settings";
import { Pro } from "./pages/pro";

export default function App() {
  const { theme } = useTheme();
  const navigate = useNavigate();

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
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar />
        <main className="flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-[1180px] px-6 py-6">
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/browse" element={<Browse />} />
              <Route path="/scan" element={<Scan />} />
              <Route path="/installed" element={<Installed />} />
              <Route path="/chat" element={<Chat />} />
              <Route path="/downloads" element={<Downloads />} />
              <Route path="/pro" element={<Pro />} />
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
