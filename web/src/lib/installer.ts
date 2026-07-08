import { toast } from "sonner";
import { ApiError, api } from "@/lib/api";

/** Track active pulls so they can be cancelled from the UI. */
const activePulls = new Map<string, AbortController>();

const PRO_UPSELL_TOAST_KEY = "lac.pro_upsell_toast_shown";
const PRO_AUTOPILOT_EXPLAINER_KEY = "lac.pro_autopilot_explainer_shown";
const OPTIMIZE_POLL_MS = 2000;
const OPTIMIZE_POLL_TIMEOUT_MS = 5 * 60 * 1000;

/**
 * Pull a model from Ollama via the streaming /api/ollama/pull endpoint,
 * surfacing live progress as a Sonner toast with a Cancel button.
 * Calls onDone when complete (not when cancelled).
 */
export function pullWithToast(model: string, onDone?: () => void) {
  // If already pulling this model, ignore duplicate.
  if (activePulls.has(model)) return;

  const controller = new AbortController();
  activePulls.set(model, controller);

  const id = toast.loading(`Pulling ${model}…`, {
    action: {
      label: "Cancel",
      onClick: () => controller.abort(),
    },
  });

  let pct = 0;

  (async () => {
    try {
      for await (const ev of api.pull(model, controller.signal)) {
        if (ev.error) throw new Error(String(ev.error));
        const c = Number(ev.completed ?? 0);
        const t = Number(ev.total ?? 0);
        const status = String(ev.status ?? "");
        if (t > 0) {
          pct = Math.max(pct, Math.round((c / t) * 100));
          toast.loading(`Pulling ${model} — ${pct}%`, {
            id,
            description: status,
            action: {
              label: "Cancel",
              onClick: () => controller.abort(),
            },
          });
        } else {
          toast.loading(`Pulling ${model}…`, {
            id,
            description: status,
            action: {
              label: "Cancel",
              onClick: () => controller.abort(),
            },
          });
        }
      }
      toast.success(`Installed ${model}`, { id });
      onDone?.();
      pollProOptimizeStatus(model);
    } catch (e) {
      if (controller.signal.aborted) {
        toast.info(`Cancelled pull of ${model}`, { id });
      } else {
        toast.error(`Failed to pull ${model}`, {
          id,
          description: e instanceof Error ? e.message : String(e),
        });
      }
    } finally {
      activePulls.delete(model);
    }
  })();
}

/** Cancel all active pulls (e.g. on page unload). */
export function cancelAllPulls() {
  for (const controller of activePulls.values()) {
    controller.abort();
  }
  activePulls.clear();
}

/**
 * Second phase after an install: poll LAC Pro's autopilot (benchmark + sweep
 * + apply, fired by the on_model_installed hook) and surface its result.
 * Free users (no Pro, or Pro unlicensed) get a single one-time upsell toast
 * instead of a polling toast — gated by localStorage so it only ever fires
 * once, per spec decision 3 (this lives entirely in the frontend; core and
 * the hook never know Pro's marketing exists).
 */
async function pollProOptimizeStatus(model: string) {
  const started = Date.now();
  const toastId = toast.loading(`Optimizing ${model}…`);

  while (Date.now() - started < OPTIMIZE_POLL_TIMEOUT_MS) {
    let status: { state: string; tokens_per_second?: number };
    try {
      status = await api.proOptimizeStatus(model);
    } catch {
      // Route unreachable (404 = Pro not installed at all, or transient) --
      // stop silently and offer the upsell, same as an explicit not_licensed.
      toast.dismiss(toastId);
      maybeShowUpsellToast();
      return;
    }

    if (status.state === "not_licensed") {
      toast.dismiss(toastId);
      maybeShowUpsellToast();
      return;
    }
    if (status.state === "done") {
      const tps = Math.round(status.tokens_per_second ?? 0);
      toast.success(`${model}: ${tps} tok/s ✓`, { id: toastId });
      maybeShowAutopilotExplainerToast();
      return;
    }
    if (status.state === "failed_silent") {
      // Never a scary error toast -- the model is already installed and
      // usable; it just stayed at Ollama's default config (spec §6).
      toast.dismiss(toastId);
      return;
    }
    // "idle" or "running" -> keep polling.
    await new Promise((resolve) => setTimeout(resolve, OPTIMIZE_POLL_MS));
  }
  toast.dismiss(toastId);
}

function maybeShowUpsellToast() {
  if (localStorage.getItem(PRO_UPSELL_TOAST_KEY)) return;
  localStorage.setItem(PRO_UPSELL_TOAST_KEY, "1");
  toast.info("LAC Pro auto-benchmarks and tunes every model you install for your exact hardware.", {
    action: {
      label: "Get Pro",
      onClick: () => window.open("https://dkrynen.github.io/lac/#pro", "_blank"),
    },
  });
}

function maybeShowAutopilotExplainerToast() {
  if (localStorage.getItem(PRO_AUTOPILOT_EXPLAINER_KEY)) return;
  localStorage.setItem(PRO_AUTOPILOT_EXPLAINER_KEY, "1");
  toast.info(
    "LAC Pro just optimized this model automatically — benchmarked it, swept GPU-offload configs, and applied the fastest. It'll keep doing this for every model you install, silently, from now on."
  );
}

const PRO_IMPORT_UPSELL_TOAST_KEY = "lac.pro_import_upsell_toast_shown";
const IMPORT_POLL_MS = 3000;
const IMPORT_POLL_TIMEOUT_MS = 30 * 60 * 1000; // conversion can genuinely take many minutes

/**
 * Kick off a LAC Pro custom Hugging Face model import and poll its
 * progress via a toast, the same shape pullWithToast/pollProOptimizeStatus
 * already use. Distinct honest failure messages per state (spec §4) --
 * never a generic "something went wrong".
 *
 * The import routes only exist at all when the lac-pro plugin is loaded, so
 * an unreachable route (404) means "no Pro" exactly like an explicit
 * {state: "not_licensed"} response -- both are routed through the same
 * try/catch-then-upsell handling pollProOptimizeStatus already established.
 */
/** Accept whatever a user pastes for a Hugging Face model and reduce it to the
 * `org/model` the backend expects: a full URL, `huggingface.co/org/model`,
 * a `/tree/main` deep link, or already-bare `org/model` all normalize down. */
export function normalizeRepoId(raw: string): string {
  let s = (raw ?? "").trim();
  s = s.replace(/^https?:\/\//i, "");
  s = s.replace(/^(www\.)?(huggingface\.co|hf\.co)\//i, "");
  s = s.split(/[?#]/)[0]; // drop query/hash
  const parts = s.split("/").filter(Boolean);
  if (parts.length >= 2) s = `${parts[0]}/${parts[1]}`; // org/model, drop /tree/main etc.
  return s.replace(/\/+$/, "");
}

export async function importModelWithToast(
  repoId: string,
  quant: string | undefined,
  onDone?: () => void,
  onSettled?: () => void,
  filename?: string
) {
  repoId = normalizeRepoId(repoId);
  let kickoff: { accepted?: boolean; state?: string; error?: string };
  try {
    kickoff = await api.importModel(repoId, quant, filename);
  } catch (e) {
    // Route unreachable (404 = Pro not installed at all, or transient) --
    // same "no Pro" outcome as an explicit not_licensed, same one-time upsell.
    if (e instanceof ApiError && e.status !== 404) {
      toast.error("Couldn't start import", { description: e.message });
    } else {
      maybeShowImportUpsellToast();
    }
    onSettled?.();
    return;
  }
  if (kickoff.state === "not_licensed") {
    maybeShowImportUpsellToast();
    onSettled?.();
    return;
  }
  if (kickoff.error) {
    toast.error(`Couldn't start import: ${kickoff.error}`);
    onSettled?.();
    return;
  }

  const started = Date.now();
  const toastId = toast.loading(`Importing ${repoId} from Hugging Face…`, {
    description: "This can take several minutes — download, convert, and quantize.",
  });

  while (Date.now() - started < IMPORT_POLL_TIMEOUT_MS) {
    let status: {
      state: string;
      error_type?: string;
      message?: string;
      model_name?: string;
      quant?: string;
      current_file?: string;
      bytes_done?: number;
      bytes_total?: number;
      stage?: string;
    };
    try {
      status = await api.importStatus(repoId);
    } catch (e) {
      if (e instanceof ApiError && e.status !== 404) {
        toast.error("Import status failed", { id: toastId, description: e.message });
      } else {
        toast.dismiss(toastId);
        maybeShowImportUpsellToast();
      }
      onSettled?.();
      return;
    }
    if (status.state === "not_licensed") {
      toast.dismiss(toastId);
      maybeShowImportUpsellToast();
      onSettled?.();
      return;
    }
    if (status.state === "done") {
      toast.success(`Imported ${status.model_name} (${status.quant})`, { id: toastId });
      onDone?.();
      onSettled?.();
      return;
    }
    if (status.state === "cancelled") {
      toast.info(`Cancelled import of ${repoId}`, { id: toastId });
      onSettled?.();
      return;
    }
    if (status.state === "failed") {
      toast.error(importFailureTitle(status.error_type), {
        id: toastId,
        description: status.message ?? status.error_type,
      });
      onSettled?.();
      return;
    }
    toast.loading(`Importing ${repoId} - ${status.state}...`, {
      id: toastId,
      description: importProgressDescription(status),
      action: {
        label: "Cancel",
        onClick: () => {
          api.cancelImport(repoId).catch(() => {});
        },
      },
    });
    await new Promise((resolve) => setTimeout(resolve, IMPORT_POLL_MS));
  }
  toast.dismiss(toastId);
  onSettled?.();
}

function importProgressDescription(status: {
  current_file?: string;
  bytes_done?: number;
  bytes_total?: number;
  stage?: string;
}) {
  const file = status.current_file;
  const total = Number(status.bytes_total ?? 0);
  const done = Number(status.bytes_done ?? 0);
  const stage = status.stage ? `${status.stage}: ` : "";
  if (!file) return undefined;
  if (total > 0) return `${stage}${file} - ${formatBytes(done)} / ${formatBytes(total)}`;
  return `${stage}${file}`;
}

function importFailureTitle(errorType?: string) {
  switch (errorType) {
    case "auth_required":
      return "Hugging Face access required";
    case "architecture_unsupported":
      return "Unsupported model architecture";
    case "no_importable_files":
      return "No importable model files";
    case "insufficient_disk":
      return "Not enough disk space";
    case "quant_unsupported":
      return "No compatible quant for this hardware";
    case "conversion_failed":
      return "Ollama conversion failed";
    case "invalid_repo_id":
      return "Invalid Hugging Face repo";
    default:
      return "Import failed";
  }
}

function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n < 0) return "";
  if (n === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = n;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 10 || unit === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[unit]}`;
}

function maybeShowImportUpsellToast() {
  if (localStorage.getItem(PRO_IMPORT_UPSELL_TOAST_KEY)) return;
  localStorage.setItem(PRO_IMPORT_UPSELL_TOAST_KEY, "1");
  toast.info("Importing custom Hugging Face models is a LAC Pro feature.", {
    action: { label: "Get Pro", onClick: () => window.open("https://dkrynen.github.io/lac/#pro", "_blank") },
  });
}
