# Changelog

## [Unreleased]

- **Real-speed calibration loop** — `aptm benchmark` results (per-machine + software-stack fingerprint) now calibrate recommendations; recs tagged `measured`/`calibrated`/`estimated` with confidence bands
- **Web technical controls** — calibration badges, expandable per-model split-plan rows, per-GPU on/off + RAM-spill what-if toggles, browser benchmark dialog with live tok/s streaming
- **Open-core plugin seam** — plugins mount via the `apt.plugins` entry-point group (CLI subcommands + API routes, per-plugin error isolation); `aptm plugins`, `GET /api/plugins`, authoring guide in docs/PLUGINS.md
- **APT Pro (separate add-on)** — Tuning Cockpit: `apt pro tune` offload auto-tuner with `--apply`, license activation, calibration insights
- **Packaging** — pip/pipx-installable (`aptm` console script); release pipeline now bundles the React UI into the Windows exe (previously shipped the legacy UI); tri-OS CI matrix
- Fixes: GPU masking now keyed to real device indices assigned during tier building; `aptm benchmark --export *.csv` no longer crashes (missing import)

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
