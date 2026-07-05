# LAC Pro Custom Model Import — Design Spec

**Date:** 2026-07-05 · **Status:** Approved · **Relates to:** `2026-07-04-lac-pro-autopilot-design.md`

## 1. Why

After shipping Pro Autopilot (auto-benchmark + auto-tune on install), Duan's concern remained:
continuous background optimization is real value, but it's invisible — nobody *feels* a process
they never see running, so it doesn't justify Pro the way a hard capability wall would. The
actual trigger for this spec: Duan personally hit a wall trying to install a 9B parameter model
(`deepreinforce-ai/Ornith-1.0-9B`) straight from Hugging Face and couldn't, because LAC only
installs models Ollama's official library already carries as pre-made GGUF files. That gap —
*"I found something cool on Hugging Face and my tool can't run it"* — is a problem essentially
everyone in LAC's target audience (r/LocalLLaMA) hits regularly, well before a given model ever
lands in Ollama's library, if it ever does. Closing it is a genuine capability Free cannot
replicate at all (not "the same thing, automated" — Free has *zero* path to custom models
today), which is the felt, immediate difference Duan asked for.

## 2. Decisions locked (do not re-litigate)

1. **Whole pipeline is Pro-only.** Paste a Hugging Face repo ID, see the upsell if unlicensed.
   This is a brand-new capability, not something taken from Free — doesn't touch the "Free =
   everything existing, forever" commitment from the v1 launch spec.
2. **One field, one button.** Paste a HF repo ID (e.g. `deepreinforce-ai/Ornith-1.0-9B`), hit
   Import. No browsing Hugging Face's catalog inside LAC, no multi-step wizard — matches the
   "as easy as Cursor" bar Autopilot was already held to.
3. **Auto-pick the best quant for the user's hardware by default, with a manual override.**
   Reuses the existing hardware-scan + VRAM-fit scoring (`backend/cookbook/recommend.py`) that
   already knows how to answer "what quant fits this rig" for the curated catalog — this is
   the same question, just asked about a model that isn't in the catalog yet.
4. **Converted models become full catalog citizens.** Once imported, a custom model is scored,
   tagged (`measured`/`calibrated`/`estimated`), benchmarked + tuned by Autopilot on its first
   install exactly like any of the 91 curated models, and shows up in future `lac recommend`
   output. No second-class "just an installed model" experience — this is explicit: the whole
   point is that custom models get the same treatment as curated ones from here on.
5. **Fail fast, fail specific.** An architecture-compatibility check runs against the HF repo's
   `config.json` *before* any download starts. Four distinct, honest failure states (below) —
   none of them silently pretend success, matching the "no silent lies" discipline the
   pre-launch audit-fixes plan enforced everywhere else in the app.
6. **Not a new inference runtime.** This does not add vLLM/TensorRT-LLM/MLX or any second
   execution engine (that idea — "multi-runtime execution" — stays a separate, much larger,
   unscoped v2.1+ candidate). The output of this pipeline is a normal Ollama-managed GGUF model.
   Everything else LAC already does (scoring, split-plan computation, chat, Autopilot tuning)
   works on it completely unchanged, because as far as the rest of the app is concerned it's
   just another installed model.

## 3. Technical approach

**Core discovery this spec is built on:** Ollama can build a model directly from a local
directory of Hugging Face safetensors weights via a Modelfile's `FROM <local-dir>` instruction,
and `ollama create` accepts a `-q/--quantize` flag to produce a quantized GGUF from FP16/FP32
source weights in one step. This means LAC does **not** need to vendor, bundle, or maintain its
own copy of llama.cpp's `convert_hf_to_gguf.py`/`llama-quantize` tooling — Ollama's own team
already owns keeping that conversion logic current with new model architectures. The pipeline
LAC orchestrates is:

1. Fetch the HF repo's `config.json` only (cheap, no weight download) and check its
   `architectures` field against a known-supported list.
2. If supported: check free disk space against the repo's reported file sizes (via the HF API)
   — need roughly 2x the model's FP16 size free (raw download + Ollama's internal FP16
   intermediate before quantizing).
3. Download the repo's weight files to a scratch directory.
4. Write a minimal Modelfile (`FROM <scratch-dir>`) and run
   `ollama create <name> -q <quant> -f <Modelfile>`, where `<quant>` is either LAC's
   auto-picked choice (via the existing hardware-fit scoring) or the user's manual override.
5. On success: delete the scratch directory (raw safetensors + any intermediate files) —
   only the final Ollama-managed quantized model remains on disk.
6. Register the model as a catalog entry (see §2.4) and hand off to the existing Autopilot hook
   exactly as any other fresh install does.

**Open technical risk, flagged for the implementation plan's first task to validate before
anything else is built**: step 4's `ollama create -q ... -f <Modelfile with FROM local-dir>`
behavior is based on Ollama's published docs, not yet verified hands-on in this project. The
first implementation task must be a real spike — pick a small, known-supported HF model (a few
hundred MB, not a multi-GB download), run this exact pipeline by hand, and confirm the resulting
model actually loads and generates correctly — before any of the surrounding orchestration,
UI, or error-handling code gets built on top of an unverified assumption.

**Known limitation, communicated honestly, not hidden**: not every Hugging Face model is
convertible this way — only architectures llama.cpp (and therefore Ollama's internal
conversion) supports. This is precisely why `Ornith-1.0-9B` failed originally: it pairs
safetensors weights with a vision-projector head, an architecture llama.cpp doesn't handle.
This feature closes most of the "cool new HF model, not yet in Ollama's library" gap, not
literally all of it — the pre-check in §2.5/§4 exists specifically so that boundary is hit
cleanly and early, not as a silent failure 20 minutes into a download.

## 4. Error handling — four distinct, honest states

1. **Architecture unsupported** (pre-check, before any download) — clear message naming the
   actual architecture found in `config.json` and stating it isn't convertible yet.
2. **Insufficient disk space** (pre-check, before download) — states how much free space is
   needed vs. available, does not start downloading.
3. **Download failed** (network) — retryable, clear network-error message, no partial scratch
   files left behind.
4. **Conversion/quantization failed** (`ollama create` itself errors) — surfaces Ollama's own
   error text, cleans up the scratch directory regardless of outcome.

None of these degrade to a generic "something went wrong" — each is a distinct, named failure
mode a user (or Duan, when someone reports a bug) can act on.

## 5. UX flow

A new Pro-gated surface (naming/exact placement — Settings? a new "Import" page? — is an
implementation-time detail, not locked here) with:

- One text input (HF repo ID) + one button.
- Unlicensed users see the field but get the Pro upsell on submit (or the field is visibly
  Pro-gated from the start) — exact treatment matches how Autopilot's existing upsell moment
  in `installer.ts`/`pullWithToast` already works, for consistency.
- Progress surfaced the same way model pulls already are (the existing toast/polling shape from
  `on_model_installed`/`optimize-status` — no new progress-UI pattern needed, this is another
  phase of the same "something is happening in the background, tell me when it's done" shape
  LAC already has).
- On success: the model appears in Installed + Browse/Recommend exactly like any other model,
  with no visual distinction marking it as "custom" (per §2.4 — it's just a model now).

## 6. Testing approach

- Unit tests for the architecture pre-check against a table of known-supported and
  known-unsupported `config.json` architecture values (no network calls).
- Unit tests for the disk-space pre-check math.
- Integration test for the quant auto-pick, reusing the existing hardware-fit scoring test
  fixtures — confirm it picks the same quant the recommend engine would for an equivalent
  curated-catalog model at the same param count.
- The actual `ollama create -q ... -f <Modelfile>` step is exercised live (marked `@pytest.mark.live`,
  matching the existing convention for tests that need a running Ollama daemon) against one
  small real HF model, not mocked — this is the one piece of the pipeline this spec is least
  certain about (see §3's flagged risk), so it needs a real, not mocked, proof it works.
- Cleanup-on-failure tests: confirm the scratch directory is removed after each of the four
  failure states in §4, not just the success path.

## 7. Out of scope

- Any second inference runtime (vLLM/TensorRT-LLM/MLX) — stays a separate, unscoped, much
  larger v2.1+ candidate (§2.6).
- Browsing/searching Hugging Face's catalog from inside LAC — the entry point is pasting a
  known repo ID, not discovery.
- Re-quantizing or re-converting a model already imported this way (treat it as a fresh import
  if the user wants a different quant — no in-place re-conversion flow in this scope).
- Any change to Autopilot, Insights, or the existing curated-catalog scoring logic itself — this
  spec is entirely additive: a new way for a model to *enter* the catalog, not a change to how
  models already in it are scored or tuned.
- The separate "quick copy win" (Pro landing-page/positioning pass) Duan also asked for — that's
  intentionally a different, much smaller piece of work, not bundled into this spec.
