# Changelog

## [Unreleased]

- **One LAC product spine** - Added a versioned Local/Local Pro/Cloud state contract and one desktop Account surface while keeping local execution the default and the private Pro plugin behind the generic entry-point boundary.
- **Fail-closed desktop Cloud account bridge** - Added Google/GitHub PKCE startup, strict `lac://oauth/callback` handling, Windows DPAPI refresh-token storage, bounded public response decoders, refresh rotation, and explicit usage-reset presentation. The production Cloud origin and hosted execution remain disabled pending launch evidence.
- **Launch-gate release scopes** - `enterprise_launch_gate.py` now takes `--release-scope {local,cloud}` (default `cloud`). The `local` scope gates the signed installer release on 5 evidence gates plus the repository and installer lanes and passes with zero cloud evidence; the `cloud` scope keeps all 19 gates fail-closed. Evidence manifests are schema v3 and scope-bound (signatures cover the scope).
- **2.7.0 release candidate** - Staged the two-tier Local Pro/Pro Cloud contracts, mandatory artifact integrity, hardened delivery gate, enterprise CI gates, and fail-closed Authenticode release workflow. This remains unreleased until the IP, signing, paid-platform, audit, beta, and external-review gates pass.
- **Import preflight smoke** - Added a cheap `live_import_stress.py --preflight-only` mode and wired it into the public-readiness live lane so GGUF preflight, Pro resolver, and HF token route shape are checked before any slow import.
- **Live import stress timing** - Raised the slow import stress defaults and public-readiness gate wiring to match the installed-app HF import/delete smoke timing observed on Windows.
- **Strict release match** - `release_readiness.py --strict-public-match` now requires the latest public tag, installer size, and `SHA256SUMS.txt` entry to match the local app build.
- **Public copy truth pass** - Tightened README/site Pro claims and the Pro delivery runbook so public-facing docs distinguish verified local automation from Duan-gated release/payment smoke.

## 2.6.4 (2026-07-09)

- **Model delete fix** - Ollama delete success responses with empty bodies now register as success instead of surfacing a false internal HTTP error.
- **Delete resilience** - Model deletes now get a longer Ollama timeout so large model cleanup has room to finish.
- **Update-check accuracy** - Local patch builds no longer get told to "update" down to an older public release.
- **Download visibility** - Ollama pulls now expose server-side pull status and the Downloads page shows active pulls with sane timestamps.
- **Model storage control** - Settings can configure the user-level `OLLAMA_MODELS` directory for future pulls, with restart guidance and no automatic model-file moves.
- **HF import preflight** - Hugging Face GGUF results now show staging/model-store disk checks before import, matching Pro's non-C-drive scratch path behavior.
- **Install preflight** - Manual Browse installs now classify pasted Ollama/Hugging Face refs before starting, show the selected action/file/store fit, and block obvious disk/compatibility failures.
- **Performance Doctor** - Added a latency diagnostic page with a bounded live probe that separates first-token delay, cold load, prompt prefill, and generation speed.
- **Model-store Doctor** - Settings now surfaces model-drive pressure, Hugging Face import scratch size, stale default-store files, and safe scratch cleanup.
- **Runtime smoke coverage** - Added an installed-app smoke script for live warm/chat/session checks against a real local model.
- **Live import stress coverage** - Added a repeatable installed-app stress script for HF preflight, Pro GGUF import, imported-model chat, and disposable model delete verification.
- **Installed-app audit coverage** - Added a rendered route/API audit for the real Program Files install, including core pages, Pro APIs, storage policy, support bundle, and update checks.
- **Public-readiness gate** - Added a lane-based release gate script that runs source checks, open-core/Pro boundary guards, installed-app audits, and live model smoke checks without committing, pushing, or publishing.
- **Installed launch smoke** - Added a Program Files launch smoke so release QA can prove `lac.exe` starts, serves the app, passes the installed audit, and shuts down cleanly.
- **Release workflow hardening** - Manual GitHub release dispatch now uses the requested version for installer stamping and draft release creation, and checksum generation targets the exact installer version.

## 2.6.3 (2026-07-08)

- **Release readiness verifier** - `scripts/release_readiness.py` now checks the local installer hash/size, running app version, debug-bundle download, Pro plugin discovery, and published GitHub release asset without pushing or publishing anything.
- **Safer release workflow** - GitHub Actions now stamps `installer.iss` by replacing `#define MyAppVersion` directly and uploads `SHA256SUMS.txt` beside the Windows installer for unsigned-download verification.
- **Debug bundle export** - Settings can export a sanitized support bundle with app, Ollama, storage, hardware, plugin, and recent-download state.
- **Pro activation polish** - `lac unlock <key>` now installs and activates the Pro plugin in one flow while keeping the key out of argv.
- **Update/download polish** - Update checks now point directly to the Windows installer asset when GitHub provides one, and the Pro page surfaces degraded cockpit states clearly.

## 2.6.2 (2026-07-07)

- **Settings depth pass** - Settings now has clearer Engine, Appearance, Account & Pro, Diagnostics, and About sections instead of a thin form stack.
- **Richer theming** - Appearance now supports System/Dark/Light theme mode, local accent presets, a live preview, reset controls, and Comfortable/Compact density.
- **Storage clarity** - Settings now shows that models are pulled on demand through Ollama, where Ollama stores model weights, and whether any model-weight files accidentally landed inside the app payload.
- **Polished app chrome** - Density now affects the main page padding, top bar, sidebar width, and shared input/select controls for a tighter Windows desktop feel.
- **Hugging Face import fix** - Pro import now accepts pasted full `https://huggingface.co/...` URLs by normalizing them to `org/model`.
- **Warm Pro model selection** - Tune and Benchmark now warm selected models before measuring so tok/s and TTFT reflect warm-path behavior.

## 2.6.1 (2026-07-06)

- **Much faster launch** — the app no longer re-extracts itself to a temp folder on every start (one-dir packaging). Warm launches drop from several seconds to well under one.
- **Snappier UI** — the hardware probe is now cached for the session instead of re-running a system query on every scan/recommendation, so navigating the app is instant.
- **Autopilot no longer thrashes your GPU** — installing several models in a row now benchmarks/tunes them one at a time instead of all at once in parallel.

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

- **LAC Pro delivery & activation** — prepared the Polar → license-gated download path: `lac unlock <key>` (or **Settings → Activate Pro** in the web UI) installs the compiled Pro plugin, then `lac pro activate <key>` licenses it. The Pro plugin is Nuitka-compiled and served from a private store only to validated license keys; the open-source core stays Pro-logic-unaware (generic licensed-plugin bootstrap, no `lac_pro` import). Hardening against casual bypass — honestly not DRM.
- **Pro Autopilot** — after Pro is installed and licensed, supported model installs can be benchmarked, GPU-offload swept, and tuned to your rig; successful runs feed the `measured` speed tier.
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
