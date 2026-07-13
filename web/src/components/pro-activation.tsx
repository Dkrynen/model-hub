import { useState, type ReactNode } from "react";
import { CheckCircle2, ExternalLink, Sparkles } from "lucide-react";
import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import type { ProStatus } from "@/lib/types";
import { proPlanPresentation } from "@/lib/pro-entitlements";
import { PRO_WAITLIST_URL, UnlockList } from "@/components/pro/product-spine";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";

type ActivateResult =
  | { state: "activated" }
  | { state: "install_failed"; message: string; error_type?: string }
  | { state: "activation_failed"; message: string };

type RelaunchResult = { state: "ok" } | { state: "failed"; message: string };

export function ProActivation({ embedded = false }: { embedded?: boolean }) {
  const status = useAsync(() => api.proStatus() as Promise<ProStatus>);

  const [licenseKey, setLicenseKey] = useState("");
  const [activating, setActivating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [celebrating, setCelebrating] = useState(false);
  const [relaunching, setRelaunching] = useState(false);
  const plan = proPlanPresentation(status.data?.plan);

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
    // call typically never resolves; the overlay persists until the window
    // comes back up. The backend can also fail gracefully and resolve instead.
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
      <ProActivationFrame embedded={embedded}>
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
              {plan.label}
            </p>
            <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-[13px]">
              <dt className="text-fg-muted">Plan</dt>
              <dd className="font-mono">{plan.label}</dd>
              <dt className="text-fg-muted">Expires</dt>
              <dd className="font-mono">{status.data.expires_human ?? "-"}</dd>
              <dt className="text-fg-muted">Machine</dt>
              <dd className="font-mono">{status.data.machine ?? "-"}</dd>
              <dt className="text-fg-muted">Last checked</dt>
              <dd className="font-mono">{status.data.checked ?? "-"}</dd>
            </dl>
          </>
        ) : (
          <>
            <p className="mt-0.5 text-[13px] text-fg-muted">
              Local Pro is planned at $36/year, but checkout is not open yet. Already have a
              Local Pro license key? Activate it below.
            </p>
            <div className="mt-3 rounded border border-line bg-panel-2 p-3 text-[12px] leading-relaxed text-fg-muted">
              Free installs ship no Pro code. A valid Local Pro key downloads the private plugin
              locally. Pro Cloud is the planned $20/month higher tier: it includes everything in
              Local Pro, plus encrypted sync and capped hosted agents. It is not yet available.
              Every paid checkout will require a Google or GitHub LAC account; the checkout
              redirect itself will never grant access.
            </div>
            <div className="mt-4">
              <label className="block">
                <span className="mb-1.5 block text-[12px] font-medium text-fg-muted">License key</span>
                <Input
                  value={licenseKey}
                  onChange={(e) => setLicenseKey(e.target.value)}
                  placeholder="LAC-PRO-..."
                />
              </label>
            </div>
            {error && <p className="mt-2 text-[13px] text-danger">{error}</p>}
            <div className="mt-4 flex flex-wrap justify-end gap-2">
              <Button asChild variant="secondary">
                <a href={PRO_WAITLIST_URL} target="_blank" rel="noreferrer">
                  <ExternalLink /> Join waitlist
                </a>
              </Button>
              <Button onClick={activate} disabled={activating || !licenseKey.trim()}>
                <Sparkles /> {activating ? "Activating..." : "Activate key"}
              </Button>
            </div>
          </>
        )}
      </ProActivationFrame>

      {celebrating && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <Card className="w-full max-w-sm p-6 text-center">
            <CheckCircle2 className="mx-auto h-10 w-10 text-verdant" />
            <h3 className="mt-3 text-base font-semibold">You're Pro</h3>
            <p className="mt-1 text-[13px] text-fg-muted">Here's what just unlocked:</p>
            <UnlockList />
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
            <h3 className="mt-3 text-base font-semibold">Activating Pro...</h3>
            <p className="mt-1 text-[13px] text-fg-muted">LAC is restarting to load the Pro plugin.</p>
          </Card>
        </div>
      )}
    </>
  );
}

function ProActivationFrame({
  embedded,
  children,
}: {
  embedded: boolean;
  children: ReactNode;
}) {
  if (embedded) return <div>{children}</div>;
  return <Card className="p-5">{children}</Card>;
}
