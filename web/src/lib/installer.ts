import { toast } from "sonner";
import { api } from "@/lib/api";

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
