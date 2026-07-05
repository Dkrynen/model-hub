import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Save, Github, Info, Sparkles } from "lucide-react";
import { PageHeader, ErrorState } from "@/components/page";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useAsync } from "@/lib/hooks";
import { api } from "@/lib/api";
import { useTheme } from "@/components/theme";

export function Settings() {
  const cfg = useAsync(() => api.config());
  const ver = useAsync(() => api.version().catch(() => null));
  const { theme, setTheme } = useTheme();

  const [host, setHost] = useState("");
  const [defaultModel, setDefaultModel] = useState("");
  const [saving, setSaving] = useState(false);
  const [licenseKey, setLicenseKey] = useState("");
  const [unlocking, setUnlocking] = useState(false);

  useEffect(() => {
    if (cfg.data) {
      setHost(cfg.data.ollama_host);
      setDefaultModel(cfg.data.default_model ?? "");
    }
  }, [cfg.data]);

  const save = async () => {
    setSaving(true);
    try {
      await api.saveConfig({
        ollama_host: host,
        default_model: defaultModel,
        theme: theme === "dark" ? "dark" : "light",
      });
      toast.success("Settings saved");
      cfg.reload();
    } catch (e) {
      toast.error("Save failed", { description: e instanceof Error ? e.message : String(e) });
    } finally {
      setSaving(false);
    }
  };

  const unlock = async () => {
    setUnlocking(true);
    try {
      const result = await api.unlockPro(licenseKey);
      if (result.state === "installed") {
        toast.success("Pro activated — restart LAC to use it");
      } else {
        // Surface the installer's honest message, not a generic string.
        toast.error(result.message);
      }
    } catch (e) {
      toast.error("Activation failed", { description: e instanceof Error ? e.message : String(e) });
    } finally {
      setUnlocking(false);
    }
  };

  return (
    <>
      <PageHeader title="Settings" subtitle="Engine connection, appearance, and defaults." />

      <div className="grid max-w-2xl gap-5">
        {/* Connection */}
        <Card className="p-5">
          <h2 className="text-sm font-semibold">Engine</h2>
          <p className="mt-0.5 text-[13px] text-fg-muted">Where LAC talks to Ollama.</p>

          <div className="mt-4 space-y-3">
            <Field label="Ollama host">
              {cfg.loading ? (
                <Skeleton className="h-9 w-full" />
              ) : (
                <Input value={host} onChange={(e) => setHost(e.target.value)} placeholder="http://localhost:11434" />
              )}
            </Field>
            <Field label="Default model">
              <Input
                value={defaultModel}
                onChange={(e) => setDefaultModel(e.target.value)}
                placeholder="e.g. llama3.2:3b"
              />
            </Field>
          </div>

          <div className="mt-4 flex justify-end">
            <Button onClick={save} disabled={saving || cfg.loading}>
              <Save /> {saving ? "Saving…" : "Save"}
            </Button>
          </div>
          {cfg.error && <ErrorState message={cfg.error} onRetry={cfg.reload} />}
        </Card>

        {/* Appearance */}
        <Card className="p-5">
          <h2 className="text-sm font-semibold">Appearance</h2>
          <p className="mt-0.5 text-[13px] text-fg-muted">Theme is stored locally in your browser.</p>
          <div className="mt-4">
            <Field label="Theme">
              <Select value={theme} onValueChange={(v) => setTheme(v as "dark" | "light")}>
                <SelectTrigger className="w-[180px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="dark">Dark</SelectItem>
                  <SelectItem value="light">Light</SelectItem>
                </SelectContent>
              </Select>
            </Field>
          </div>
        </Card>

        {/* LAC Pro */}
        <Card className="p-5">
          <h2 className="flex items-center gap-2 text-sm font-semibold">
            <Sparkles className="h-4 w-4 text-verdant" /> LAC Pro
          </h2>
          <p className="mt-0.5 text-[13px] text-fg-muted">
            Enter your license key to activate Pro — the plugin is fetched and installed automatically.
          </p>
          <div className="mt-4">
            <Field label="License key">
              <Input
                value={licenseKey}
                onChange={(e) => setLicenseKey(e.target.value)}
                placeholder="LAC-PRO-…"
              />
            </Field>
          </div>
          <div className="mt-4 flex justify-end">
            <Button onClick={unlock} disabled={unlocking || !licenseKey.trim()}>
              <Sparkles /> {unlocking ? "Activating…" : "Activate Pro"}
            </Button>
          </div>
        </Card>

        {/* About */}
        <Card className="p-5">
          <h2 className="flex items-center gap-2 text-sm font-semibold">
            <Info className="h-4 w-4 text-fg-muted" /> About
          </h2>
          <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-[13px]">
            <dt className="text-fg-muted">App</dt>
            <dd className="font-mono">LAC v{ver.data?.version ?? "—"}</dd>
            <dt className="text-fg-muted">Workspace</dt>
            <dd className="font-mono">{cfg.data?.workspace ?? "—"}</dd>
            <dt className="text-fg-muted">Source</dt>
            <dd>
              <a
                href={ver.data?.github_url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1.5 text-verdant hover:underline"
              >
                <Github className="h-3.5 w-3.5" /> GitHub
              </a>
            </dd>
          </dl>
        </Card>
      </div>
    </>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-[12px] font-medium text-fg-muted">{label}</span>
      {children}
    </label>
  );
}
