# APT v2 Overhaul — Design Spec

**Date:** 2026-07-03 · **Status:** Approved-pending-Duan's-spec-review · **Supersedes:** v1
launch checklist (HANDOFF.md, ON HOLD — resumes after this overhaul ships)

## 1. Goal & sequencing

APT becomes its own product before anyone sees it. **Overhaul first, launch once** — nothing is
pushed to origin until all four workstreams land; nobody ever sees the Model-Hub-flavored version.
Baseline: model-hub master @ `7c065cf`+ (200 tests green), apt-pro @ `b8ac406`+ (47 tests, no
remote, never gets one). Plans 1–3 of the v1 path (open-core seam, Pro cockpit, LS licensing,
release engineering) are shipped and are the substrate this builds on.

Four workstreams, each its own implementation plan:

| # | Workstream | One-liner |
|---|---|---|
| W1 | Deep-Dive Mode | Global toggle that makes every view show its math. **Free tier.** |
| W2 | Surfaces | Web + Desktop (pywebview) from one codebase; CLI reborn as an agentic chat TUI |
| W3 | Rebrand — "Forge" | Warm graphite + copper, die mark; replaces iris everywhere |
| W4 | Security posture | Supply-chain CI, verified localhost bind, plugin trust, honest update path |
| W5 | Hardware identity & format fitment | Vendor/architecture identity card + vendor-native format awareness (annotate-only) |

**Build order: W3 → W1 → W5 → W2 → W4.** Rationale: brand tokens are upstream of every screen
the other workstreams touch; W5 builds on W1's explain/deep-dive seams; W4 is cheap and must
simply be green before anything is pushed.

## 2. Decisions locked (do not re-litigate)

From the 2026-07-03 session with Duan (handoff `HANDOFF-v2-overhaul.md` + this brainstorm):

1. Overhaul first, launch once. No quiet-publish.
2. Desktop shell = **pywebview** (native window over the existing Flask+React; one PyInstaller
   bundle). Tauri = possible post-revenue graduation, not now.
3. Admin CLI retired as a product. Bare `apt` opens a full-screen **OpenCode-style agentic chat
   TUI**; subcommands demoted to hidden plumbing.
4. Rebrand is **Cursor-inspired, not cloned**: near-black, restrained monochrome, one sharp
   accent, dimensional logo mark. Direction chosen by Duan from three mockups: **C — Forge**.
5. Deep-dive scope: live per-tier VRAM/RAM occupancy · per-model layer/KV-cache/context math ·
   the bandwidth numbers behind every speed estimate · "why ranked here" score breakdowns · raw
   split-plan JSON. Shape chosen by Duan: **B — global toggle, every view enriched in place**.
6. Deep-Dive Mode is **free tier** (Duan, this session): "understanding is free, acting on it is
   Pro." Pro stays: tune cockpit, insights, future automation.
7. Windows code signing **deferred post-revenue** (Duan, this session). Launch unsigned; be
   honest about SmartScreen on the landing page + README; publish SHA-256 checksums per release.
   Azure Trusted Signing (~$10/mo) is the preferred path when revenue justifies it.
8. Name stays **apt** (dist `apt-hub`, command `aptm` on POSIX — Debian collision rule stands).
9. No servers of ours. Distribution load = GitHub CDN; licensing = LemonSqueezy. Their problem.
10. Vendor/format fitment scope = **annotate + identity** (Duan, this session): v2 detects and
    renders hardware identity (vendor, architecture, backend, driver) and annotates
    vendor-native format opportunities (e.g., NVFP4 on Blackwell). APT still executes
    everything through Ollama. **Multi-runtime execution (vLLM/TensorRT-LLM/MLX) is a seeded
    v2.1 candidate — possibly Pro** — not in this overhaul.
11. Vendor-fitment is **shown, not ranked** (Duan, this session): badges + deep-dive
    explanations only; no scoring influence until calibration/benchmarks prove the deltas —
    consistent with the never-fabricate-numbers credo.

## 3. W3 — Rebrand: "Forge"

Silicon and heat. Warm-tinted graphite, one ember-copper accent, dimensional die mark.
Personality: hardware-first, tactile, forged-to-fit. Voice unchanged (sharp, technical, calm).
Tagline unchanged: **"local AI, sorted."**

### 3.1 Tokens (replaces the Iris set in `web/tokens.css`)

Dark (default):

| Token | Value | Note |
|---|---|---|
| `--bg` | `#0C0A09` | warm near-black canvas |
| `--surface` | `#141110` | cards, sidebar |
| `--surface-2` | `#1C1815` | inputs, raised rows |
| `--surface-3` | `#241F1B` | hover, active well |
| `--overlay` | `rgba(8,6,5,.60)` | |
| `--border` | `rgba(255,236,224,.08)` | warm-tinted hairlines |
| `--border-strong` | `rgba(255,236,224,.14)` | |
| `--text` | `#EFEAE6` | warm off-white |
| `--text-muted` | `#A39C95` | |
| `--text-faint` | `#6C655F` | |
| `--accent` | `#FF6A3D` | **copper — the one accent** |
| `--accent-hover` | `#FF8763` | |
| `--accent-pressed` | `#E5522A` | |
| `--accent-soft` | `rgba(255,106,61,.13)` | tints, selected rows |
| `--accent-fg` | `#140B07` | text on copper (dark, not white) |
| `--success` | `#3DBE8B` | muted — verdicts must not compete with copper |
| `--warning` | `#D9924C` | warm, clearly distinct from accent |
| `--danger` | `#E5484D` | |
| `--info` | `#6FA8B8` | desaturated teal |

Light theme kept as secondary: warm paper `#FAF7F5` canvas, surfaces white/near-white with the
same warm border logic, accent deepened to `#E5522A` for contrast. Radius/elevation/motion/type
scale unchanged from the current design system (Geist Sans + Geist Mono stay).

**Accent discipline (the rule that makes it Cursor-inspired):** copper appears only where the
instrument speaks — primary actions, focus rings, live/measured numbers, the deep-dive toggle and
its revealed math, the TUI prompt/cursor. Never for decoration, never for large fills.

### 3.2 Logo

**The die** — an isometric chip package (three-face diamond, warm grays) with a single copper
trace terminating in a dot. Meaning: your silicon, mapped. Scales favicon → app icon → README
header → installer. Wordmark: lowercase mono `apt` + copper square dot. In the TUI (no SVG), the
mark degrades to a copper `◆` + `apt` wordmark rendered in truecolor `#FF6A3D`.

Reference mockups (palette, mark geometry, applied dashboard, TUI strip) live locally in
`.superpowers/brainstorm/12717-1783077788/content/brand-directions.html` (direction C — gitignored;
the W3 plan reproduces the mark as committed SVG assets, which then become canonical).

### 3.3 Scope of application

- `web/tokens.css` + `web/DESIGN_SYSTEM.md` (rewrite brand section; component rules unchanged)
- Web app: no layout changes — tokens flow through Tailwind/shadcn automatically; audit for
  hardcoded iris hexes
- `site/index.html` landing page re-skin + honest-SmartScreen note in the download section
- TUI banner + theme (W2 consumes these tokens)
- `installer.iss` branding + app icon, README header, GitHub social preview
- New `assets/` dir: die mark SVG (mono + color), favicon.ico, app icon .ico/.png sizes

## 4. W1 — Deep-Dive Mode (free)

One copper **Deep dive** switch in the web topbar. OFF = today's clean UI, byte-identical
behavior. ON = every existing view tells you its math, in place. No new page, no duplicate views.
Extends the P1 expandable split-plan rows pattern.

### 4.1 What turns on

- **Dashboard — live occupancy strip:** per-tier used/total (dGPU / iGPU / RAM) with per-model
  segments. Data: poll Ollama `/api/ps` (loaded models, `size_vram`) merged with the hardware
  scan's tier map, every 5s while visible. New endpoint `GET /api/occupancy` returns
  `{tiers:[{name, used_gb, total_gb, residents:[{model, gb}]}]}`.
- **Model cards / recommend rows — "why ranked here" expander:** score component breakdown
  (fit × speed × quality/recency × calibration status), layer split (N/M per tier), KV-cache GiB
  at the context used for the estimate, the bandwidth figure and regime (gpu-resident vs spilled,
  incl. the 0.65 spill efficiency), and the calibration tag (measured/calibrated/estimated ±band).
- **Raw split-plan JSON:** collapsible, copy button, inside the same expander.
- **Benchmarks page:** per-run detail already exists; deep-dive adds the fingerprint + regime
  factors currently hidden in `results.jsonl`.

### 4.2 Implementation shape

- Backend: the recommender already computes all of this — `recommend()` gains an
  `explain=true` mode that includes a structured `explain` payload per recommendation (score
  components, split plan, KV/bandwidth numbers, regime, calibration source). No engine changes;
  serialization only. Plus the new `/api/occupancy` endpoint.
- Frontend: `DeepDiveContext` (React context + localStorage persistence), topbar toggle,
  `OccupancyStrip`, `WhyRanked` expander sections, `RawJson` viewer. All render only when the
  toggle is ON.
- TUI (W2): the same `explain` payload backs a `/why <model>` slash command and the
  `explain_recommendation` agent tool — deep-dive is a product concept, not a web-only feature.

### 4.3 Error handling

- Ollama down / `/api/ps` unreachable → occupancy strip shows "engine offline", no polling storm
  (backoff to 30s).
- `explain` adds nothing when the recommender lacks data (e.g., no calibration) — fields are
  omitted, UI renders what exists. Never fabricate numbers.

## 5. W2 — Surfaces

### 5.1 Entry points (the product surface after v2)

| Invocation | Result |
|---|---|
| `apt` (TTY) | full-screen agentic chat TUI |
| `apt` (non-TTY) | help text, exit 0 |
| `apt web` | Flask server + browser (today's behavior, unchanged) |
| `apt desktop` | pywebview native window over the same Flask app |
| `apt <subcommand>` | works as today for scripting/plumbing — **hidden** from top-level help; documented in `docs/CLI.md` as the plumbing layer |
| Start-menu shortcut (installer) | `apt desktop` |

### 5.2 The agentic TUI

OpenCode-style chat: transcript pane, status bar (current model · tok/s · session), input with
slash commands. Powered by the **existing** substrate — `backend/agent/runner.py` AgentRunner
(typed events: delta/tool_calls/tool_result/done), permission engine, provider abstraction, MCP
client, `backend/plugin/builtins/tools.py`.

**Wiring is prescribed by `TUI_AGENT_WIRING_FINDINGS.md` (verified against Textual 8.2.8):**
replace the current `@work(thread=True)` + raw-urllib path in `backend/tui/app.py` with an async
`@work()` worker consuming `runner.run_stream()`; permission prompts via
`ModalScreen` + `push_screen(..., wait_for_dismiss=True)`; MCP `connect_all()` in a non-blocking
`@work(exit_on_error=False)` on mount; per-agent history scoping as sketched there. Priorities
P0→P2 in that doc's matrix are the task order.

**APT-domain agent tools (v1 set):** `scan_hardware`, `recommend_models` (with explain),
`list_installed`, `show_model`, `benchmark_model`, `pull_model`, plus Pro-gated `tune` /
`insights` (exit-3 contract → tool returns the upgrade message when unlicensed). Read-only tools
auto-allow; mutating tools (`pull_model`, `benchmark_model` — long/expensive, `tune --apply`)
require the permission modal.

**Model switcher:** `/model` picker (installed models, APT-ranked); default agent model = the
top-ranked installed chat-capable model; none installed → onboarding message suggesting one.
**Slash commands:** `/help /model /scan /recommend /why <model> /bench /pull /clear /quit`.
**Banner:** copper `◆ apt` + "local AI, sorted." + current model — Forge tokens via Textual theme.

### 5.3 Desktop shell (pywebview)

`backend/desktop.py`: start the Flask app on an ephemeral loopback port, open a pywebview native
window (title "apt", die-mark icon, sensible min size), shut the server down on window close.
One PyInstaller bundle serves both (`build.spec` gains the desktop entry; installer shortcut
targets it). Explicit **v1 non-goals:** no tray, no autostart, no auto-update in-window, no
multi-window.

### 5.4 Testing

TUI: Textual `run_test()` pilot harness (exists) — stream events from a `MockAsyncLLMProvider`
through the real AgentRunner; permission modal flow; per-agent history; slash commands.
Desktop: import-time smoke (window construction mocked) + a manual boot check on real hardware.
Suite baseline stays green: 200 core / 47 pro.

## 6. W4 — Security posture

Real attack surface: the public repo's supply chain, the local server's bind, the plugin seam,
and the update path. No servers of ours.

1. **Supply-chain CI** (`.github/workflows/security.yml` + `dependabot.yml`): CodeQL
   (python + javascript), `pip-audit`, `npm audit --audit-level=high` — on PR + weekly schedule.
   Dependabot: pip, npm, github-actions ecosystems. Branch protection on master (Duan-gated
   GitHub setting — documented step, not code).
2. **Localhost bind — VERIFIED this session:** `server.py:14` defaults `HOST = "127.0.0.1"`; only
   an explicit `--host` flag widens it. Hardening: print a prominent warning banner when bound to
   a non-loopback host. pywebview desktop uses the same loopback server.
3. **Plugin-seam trust:** entry-point plugins execute arbitrary code by design. `apt plugins`
   already lists them — add the distribution/origin column; add a security note to
   `docs/PLUGINS.md` and README ("install plugins you trust, same rule as pip packages").
4. **Update path:** releases only via GitHub Releases over HTTPS; release pipeline publishes a
   `SHA256SUMS` asset; README/landing document the checksum verify step. `backend/update.py`
   must point only at the GitHub Releases API (verify; fix if not).
5. **Code signing:** deferred post-revenue (locked, §2.7). Landing page + README state the
   SmartScreen reality plainly.

## 7. W5 — Hardware identity & format fitment (annotate-only)

Make the machine a first-class character in the product: APT knows *what* your silicon is, not
just how big it is — and tells you which model formats are native to it. Execution stays
Ollama/GGUF; this workstream is detection + data + display. Nothing here changes scores (§2.11).

### 7.1 Hardware identity

- **Detection:** extend the existing scan (`backend/cookbook/hardware.py` — `GPUInfo` already
  carries `name/vram_gb/driver/backend/device_index`) with derived identity fields:
  `vendor` (nvidia/amd/apple/intel, derived from name — the same matching the PowerShell probe
  already does) and `architecture` (e.g., RDNA2, RDNA3, Ada, Ampere, Hopper, Blackwell, Apple
  M-series) via a conservative name→architecture lookup table in a new data module. Unknown
  stays `None` — **never guess** (same precedent as the conservative rocm/vulkan backend
  inference in `calibration.detect_stack()`).
- **Rendering:** the dashboard hardware hero and scan page grow an identity card: vendor chip
  (text wordmark chip, NOT vendor logos — trademark-safe), GPU name, architecture, backend
  (CUDA/ROCm/Vulkan/Metal), driver, VRAM. The TUI banner's model line gains the short form
  (e.g., `RX 6800 XT · RDNA2 · vulkan`).

### 7.2 Format fitment (annotate)

- **Data layer:** a static, data-as-code module (`backend/cookbook/formats.py`) mapping
  vendor/architecture capability → native format support: NVFP4 (Blackwell), FP8 (Ada/Hopper/
  Blackwell), AWQ/GPTQ/EXL2 (CUDA-class), MLX (Apple Silicon), GGUF (universal baseline).
  Plus a curated per-model-family flag for families with known official vendor-native releases.
  Conservative and easily updated; entries carry a one-line source note.
- **Display:** where a detected capability intersects a recommended model family, the model
  card gains a neutral **"native path"** badge, and the deep-dive expander (W1) explains it
  honestly — e.g., "Blackwell detected: this family ships an official NVFP4 build; ~native FP4
  throughput is available via TensorRT-LLM/vLLM, outside Ollama. APT runs the GGUF build."
- **Hard rules:** annotation never alters ranking (§2.11); no fabricated throughput claims for
  paths APT can't measure; unknown architecture → no fitment claims at all.

### 7.3 Error handling

Identity fields are optional everywhere: an unmatched GPU name renders today's UI unchanged
(no identity card degradation artifacts, no "unknown" scare-labels — absent means absent).
Format annotations require BOTH a confident architecture match AND a curated family entry.

## 8. Cross-cutting

- **Repos:** core `C:\Users\User\repos\model-hub` (venv `.venv\Scripts\python.exe`); Pro
  `C:\Users\User\repos\apt-pro` (tests with core's venv; NEVER gets a remote). Nothing pushed to
  origin until all four workstreams are done and Duan says push.
- **Suite commands:** core `.venv\Scripts\python.exe -m pytest -q`; web
  `cd web && npm run typecheck && npm run build` (bare — piping masks tsc exit codes); pro from
  apt-pro with core's venv.
- **Process:** subagent-driven build per Duan's standing preference; every dispatch says "work in
  the foreground, do NOT spawn agents". TDD per task.
- **Done means:** all four workstreams green + the v1 launch checklist (HANDOFF.md) resumed with
  v2 branding — that checklist (push, CI, Pages, LS store, waitlist, tag, release, announce)
  remains the launch gate and is *out of scope here*.

## 9. Out of scope (YAGNI, explicit)

Tauri shell · macOS/Linux polished installers (tease strategy stands) · in-app auto-update ·
crowd-benchmark cloud (Phase 3) · renaming away from APT · light-theme-first design · TUI feature
parity with web (the TUI is chat-first; the web is the visual surface) · code signing ·
**multi-runtime execution** (vLLM/TensorRT-LLM/MLX recommend-and-run — seeded v2.1, possibly
Pro) · vendor-fitment scoring influence (waits for measured evidence).

## Changelog

- 2026-07-03: Initial spec from the v2-overhaul brainstorm (brand fork resolved via visual
  companion: Forge; deep-dive shape: global toggle; deep-dive tier: free; signing: deferred).
- 2026-07-03 (later): Added W5 — hardware identity & format fitment (Duan: annotate + identity
  in v2; show-only, rank-later; multi-runtime execution seeded for v2.1). Build order now
  W3 → W1 → W5 → W2 → W4.
