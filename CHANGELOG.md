# Changelog

## [Unreleased]

## 2.6.0 (2026-07-06)

- **Native desktop app** — LAC now opens as a real native window (Windows WebView2), not a browser tab: single-instance (no more stacked orphan servers), proper taskbar identity, and no console-window flashes on launch or navigation. If the WebView2 runtime is ever missing, it degrades gracefully to your browser.
- **Self-serve Pro activation** — activate Pro entirely from the app: **Settings → Activate Pro** installs the plugin, writes the license grant, shows a celebration of what unlocked, and LAC relaunches into Pro (landing back on the same view). No CLI step required. Settings now shows your live Pro status instead of always prompting for a key.
- **Pro cockpit (`/pro`)** — the Pro tools are now a premium surface: **tune** any model to your exact hardware with before→after tok/s proof and one-click apply of the winning offload config; plus **calibration insights** (measured speed history + regression detection), on-demand **benchmarking**, an **autopilot** log of what was auto-tuned on install, and elevated **custom Hugging Face import** with a quant picker and history.
- **Faster first message** — Chat now preloads the selected model into VRAM off the send path, so the first reply is near-instant (measured ~5s → ~0.2s) instead of paying a cold model-load penalty; the model is also kept warm between messages.
- **System safety** — LAC will only ever terminate a process it actually owns (never a foreign process, even to reclaim its own port), routes every shell-out through a console-hiding wrapper, and confines writes to `~/.model-hub`.

## 2.5.0 (2026-07-06)

- **Pro license encrypted at rest** — the license grant in `~/.model-hub/license.json` is now sealed with AES-256-GCM under a key derived (HKDF) from a stable machine id, instead of sitting in plaintext. The key never lands on disk in the clear, hand-editing the grant is detected (GCM auth tag), and a grant copied to another machine won't decrypt. Existing plaintext grants keep working and transparently upgrade to the encrypted form on the next write. Honest casual-bypass hardening — the client-side check isn't DRM.
- **Custom-import input validation** — `lac pro import <repo_id>` now validates the Hugging Face id against HF's real `org/model` grammar at the entrypoint, rejecting traversal/URL/injection payloads before any network or filesystem work.
- **Dev override compiled out of releases** — `LAC_PRO_DEV=1` is honored only in source/dev builds; release builds ignore it and no longer advertise it. Your source venv is unchanged.
- **Build** — the shipped executable now bundles `cryptography` (with its native backend) so the Pro plugin's at-rest encryption works in the packaged app.

## 2.4.0 (2026-07-06)

- **LAC Pro delivery & activation** — buy on Polar → receive the compiled Pro plugin through a license-gated download: `lac unlock <key>` (or **Settings → Activate Pro** in the web UI) installs it, then `lac pro activate <key>` licenses it. The Pro plugin is Nuitka-compiled and served from a private store only to validated license keys; the open-source core stays Pro-logic-unaware (generic licensed-plugin bootstrap, no `lac_pro` import). Hardening against casual bypass — honestly not DRM.
- **Pro Autopilot** — every model you install is automatically benchmarked, GPU-offload swept, and tuned to your rig with zero commands; feeds the `measured` speed tier on every install.
- **Custom model import (Pro)** — paste a Hugging Face repo ID and LAC downloads, architecture-checks, quantizes, and installs it via Ollama, then registers it as a full catalog citizen and benchmarks it.
- **LAC rebrand** — renamed from APT to **LAC** (Local AI Companion): CLI command `lac`, PyPI dist `lac-ai`, Undergrowth visual identity (near-black + single green accent, vein-leaf mark) replaces the Iris palette across web, landing page, and TUI theme values; committed SVG/icon assets (`assets/`); installer/exe renamed to `LAC-Setup-x.x.x.exe` / `lac.exe`
- **Real-speed calibration loop** — `lac benchmark` results (per-machine + software-stack fingerprint) now calibrate recommendations; recs tagged `measured`/`calibrated`/`estimated` with confidence bands
- **Web technical controls** — calibration badges, expandable per-model split-plan rows, per-GPU on/off + RAM-spill what-if toggles, browser benchmark dialog with live tok/s streaming
- **Open-core plugin seam** — plugins mount via the `lac.plugins` entry-point group (CLI subcommands + API routes, per-plugin error isolation); `lac plugins`, `GET /api/plugins`, authoring guide in docs/PLUGINS.md
- **LAC Pro (separate add-on)** — Tuning Cockpit: `lac pro tune` offload auto-tuner with `--apply`, license activation, calibration insights
- **Packaging** — pip/pipx-installable (`lac` console script); release pipeline now bundles the React UI into the Windows exe (previously shipped the legacy UI); tri-OS CI matrix
- Fixes: GPU masking now keyed to real device indices assigned during tier building; `lac benchmark --export *.csv` no longer crashes (missing import)

## 2.1.0 (2026-07-01)

- **Critical bugfix: `OLLAMA_HOST` NameError** — undefined variable in `ollama()` and `ollama_stream()` error handlers now correctly call `get_host()`
- **Critical bugfix: `/api/show` HTTP method** — changed from GET to POST (Ollama requires POST for model info)
- **Critical bugfix: `/model` slash command** — model switching now actually updates the active model variable
- **Session persistence** — new SQLite-backed `cookbook.db` with `/save <name>`, `/load <name>`, `/list`, `/delete <name>` CLI slash commands; sessions auto-save on exit
- **Web chat persistence** — chat history saved to localStorage and auto-restored on page load; session management UI (list/load sessions)
- **CLI branding** — version banner on startup, clean ANSI, dead code removed
- **Backend API** — `_ollama_request` now returns error context instead of bare `None`; `ollama_check_install` actually checks for Ollama; session CRUD endpoints (`/api/sessions`)
- **`cmd_browse`** — falls back to listing installed models when library cache is unavailable
- **Chore** — unused ANSI color constants removed, code cleanup

## 2.0.0 (2026-06-30)

- Full CLI client (`model-hub chat|list|pull|delete|ps|inspect|scan|recommend|browse`)
- Interactive chat shell with streaming, history, system prompts, and slash commands
- Cross-platform batch launcher (`model-hub.bat`)
- Colorized terminal output with ANSI throughout
- Direct Ollama API integration (no web server needed for CLI commands)

## 1.3.0 (2026-06-30)

- Per-variant model browser (420 entries, one per size/quant family)
- VRAM requirements at Q4_K_M, Q8_0, and F16 for every model
- System compatibility badges on every card: ✅ Fits GPU / ⚠️ Offload / ❌ Too large
- "Fits my GPU" filter — shows only models that fit your VRAM
- Sort by VRAM (low-high) and parameters (high-low)
- System specs bar showing your GPU VRAM with compatibility legend
- Parameter counts, context windows, and architecture info on every model card
- Smart VRAM estimation for non-curated models using quantization formulas

## 1.2.0 (2026-06-30)

- Full Ollama library browser (235+ models with descriptions, capabilities, pull counts)
- Search, filter by capability (vision, tools, thinking, embedding), and sort models
- Paginated model grid with one-click install
- Model tag explorer — click "Tags" on any model to see all available variants/quantizations
- Manual install input on Browse and Downloads pages (type any model:tag)
- Running models panel on Installed page with auto-polling
- New sidebar tab layout: Dashboard → Browse Models → Scan & Recommend → Installed → Downloads → Chat
- Backend: cached Ollama library scraper with 1-hour TTL, `/api/library/browse`, `/api/library/tags`, `/api/ollama/ps` endpoints

## 1.1.0 (2026-06-30)

- Markdown rendering in chat (code blocks, bold, italic, lists)
- Model detail modal instead of inline append
- Toast notification system for installs, errors, and export
- Sort recommendations by score, VRAM, context, or name
- Export recommendations to CSV
- Keyboard shortcuts (Ctrl+Enter to send chat, Esc to close modals)
- Auto-load chat models when navigating to chat page
- Live Ollama status polling (every 10s)
- Search/filter installed models
- Mobile-responsive sidebar with toggle button
- Loading spinners for async operations
- Clear chat button with confirmation
- Security: XSS hardening with escHtml() throughout
- Security: Removed all inline onclick handlers
- Security: Fixed ollama:// protocol (now copies run command)
- Bugfix: Download progress now updates the correct item's title
- Bugfix: Fixed GitHub URLs from anomalyco → Dkrynen

## 1.0.0 (2026-06-30)

- Initial public release
- Hardware scanning (GPU, VRAM, RAM, CPU) for Windows/Linux/macOS
- 65 curated models across all major families
- Smart recommendation engine with quality/speed/fit/context scoring
- Ollama integration for model pull, list, delete, and chat
- Built-in streaming chat interface
- Dashboard with system overview and quick picks
