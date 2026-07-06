import { useState } from "react";
import { CheckCircle2, Sparkles } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { useAsync } from "@/lib/hooks";
import { api } from "@/lib/api";

type ProStatus = {
  licensed: boolean;
  plan?: string | null;
  expires_human?: string | null;
  machine?: string | null;
  checked?: string | null;
};

type ActivateResult =
  | { state: "activated" }
  | { state: "install_failed"; message: string; error_type?: string }
  | { state: "activation_failed"; message: string };

type RelaunchResult = { state: "ok" } | { state: "failed"; message: string };

const UNLOCKED_FEATURES = [
  "Autopilot auto-tuning",
  "Model tuning cockpit",
  "Custom Hugging Face import",
  "Calibration insights",
];

export function ProActivation() {
  const status = useAsync(() => api.proStatus() as Promise<ProStatus>);

  const [licenseKey, setLicenseKey] = useState("");
  const [activating, setActivating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [celebrating, setCelebrating] = useState(false);
  const [relaunching, setRelaunching] = useState(false);

  const activate = async () => {
    setActivating(true);
    setError(null);
    try {
      const result = (await api.activatePro(licenseKey)) as ActivateResult;
      if (result.state === "activated") {
        setCelebrating(true);
      } else {
        setError(result.message ?? "Activation failed.");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setActivating(false);
    }
  };

  const enterPro = async () => {
    setCelebrating(false);
    setRelaunching(true);
    // The desktop process exits mid-request on a successful relaunch, so this
    // call typically never resolves — the overlay just persists until the
    // window comes back up. But the backend can also gracefully fail (still
    // running) and resolve with `{ state: "failed" }` — handle that so the
    // overlay doesn't hang forever with no way out.
    try {
      const bounds = {
        x: Math.round(window.screenX),
        y: Math.round(window.screenY),
        width: Math.round(window.outerWidth),
        height: Math.round(window.outerHeight),
      };
      const result = (await api.appRelaunch("settings", bounds)) as RelaunchResult;
      if (result?.state === "failed") {
        setRelaunching(false);
        setError(result.message ?? "Could not relaunch. Please restart LAC manually.");
      }
    } catch {
      /* process likely already exited; nothing to surface */
    }
  };

  return (
    <>
      <Card className="p-5">
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          <Sparkles className="h-4 w-4 text-verdant" /> LAC Pro
        </h2>

        {status.loading ? (
          <div className="mt-4 space-y-2">
            <Skeleton className="h-9 w-full" />
          </div>
        ) : status.data?.licensed ? (
          <>
            <p className="mt-0.5 flex items-center gap-1.5 text-[13px] text-fg-muted">
              <Badge variant="success">Active</Badge>
              {status.data.plan ?? "LAC Pro"}
            </p>
            <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-[13px]">
              <dt className="text-fg-muted">Plan</dt>
              <dd className="font-mono">{status.data.plan ?? "—"}</dd>
              <dt className="text-fg-muted">Expires</dt>
              <dd className="font-mono">{status.data.expires_human ?? "—"}</dd>
              <dt className="text-fg-muted">Machine</dt>
              <dd className="font-mono">{status.data.machine ?? "—"}</dd>
              <dt className="text-fg-muted">Last checked</dt>
              <dd className="font-mono">{status.data.checked ?? "—"}</dd>
            </dl>
          </>
        ) : (
          <>
            <p className="mt-0.5 text-[13px] text-fg-muted">
              Enter your license key to activate Pro — the plugin is fetched and installed automatically.
            </p>
            <div className="mt-4">
              <label className="block">
                <span className="mb-1.5 block text-[12px] font-medium text-fg-muted">License key</span>
                <Input
                  value={licenseKey}
                  onChange={(e) => setLicenseKey(e.target.value)}
                  placeholder="LAC-PRO-…"
                />
              </label>
            </div>
            {error && <p className="mt-2 text-[13px] text-danger">{error}</p>}
            <div className="mt-4 flex justify-end">
              <Button onClick={activate} disabled={activating || !licenseKey.trim()}>
                <Sparkles /> {activating ? "Activating…" : "Activate Pro"}
              </Button>
            </div>
          </>
        )}
      </Card>

      {celebrating && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <Card className="w-full max-w-sm p-6 text-center">
            <CheckCircle2 className="mx-auto h-10 w-10 text-verdant" />
            <h3 className="mt-3 text-base font-semibold">You're Pro 🎉</h3>
            <p className="mt-1 text-[13px] text-fg-muted">Here's what just unlocked:</p>
            <ul className="mt-4 space-y-1.5 text-left text-[13px]">
              {UNLOCKED_FEATURES.map((f) => (
                <li key={f} className="flex items-center gap-2">
                  <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-verdant" /> {f}
                </li>
              ))}
            </ul>
            <Button onClick={enterPro} className="mt-6 w-full">
              Enter Pro
            </Button>
          </Card>
        </div>
      )}

      {relaunching && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <Card className="w-full max-w-sm p-6 text-center">
            <Sparkles className="mx-auto h-10 w-10 animate-pulse text-verdant" />
            <h3 className="mt-3 text-base font-semibold">Activating Pro…</h3>
            <p className="mt-1 text-[13px] text-fg-muted">LAC is restarting to load the Pro plugin.</p>
          </Card>
        </div>
      )}
    </>
  );
}
