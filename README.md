<img src="assets/leaf-mark.svg" width="72" alt="LAC vein-leaf mark" />

# LAC — local AI, sorted.

**Scans your hardware. Recommends models that actually fit. LAC Pro adds local benchmark/tuning automation after activation.**

LAC is a local-LLM manager built around one question: *what's the best model this machine can actually run?* It scans your GPU/VRAM/RAM/CPU, ranks models against your real hardware (including multi-GPU and RAM-spill split plans), installs them via [Ollama](https://ollama.com), and tags every recommendation `measured`, `calibrated`, or `estimated` so you always know how much to trust it. **LAC Pro closes the loop** for supported installs after activation, turning recommendations into measured data points on your exact rig.

## Features

- **Hardware scan** — Windows-first GPU, VRAM, RAM, CPU detection, with source/CLI probes for Linux and macOS (NVIDIA, AMD, Apple Silicon, Intel)
- **Fit-aware recommendations** — 91-model curated catalog scored by quality, speed, hardware fit, and context; multi-GPU tiering (dGPU → iGPU → RAM) with per-model split plans
- **Real-speed calibration** — recs are tagged `measured` / `calibrated` / `estimated` with confidence bands; LAC Pro can feed the `measured` tier for supported installs after activation
- **What-if controls** — toggle GPUs on/off, allow/deny RAM spill, and watch the recommendations recompute live in the web UI
- **Model management + chat** — install, run, delete; streaming chat with session persistence; full TUI
- **Studio + Lab** - use configured providers in a project-aware workbench (local Ollama by default), compare exact model identities from the configured Ollama endpoint, and run a reproducible diagnostic protocol

## Install

### Windows (recommended)

Download the latest published `LAC-Setup-x.x.x.exe` from [Releases](https://github.com/Dkrynen/lac/releases) and run it. Local development builds may be ahead of the public Releases page.

### Any platform (CLI via pipx)

```bash
# Requires Python 3.10+ and Ollama (https://ollama.com/download)
pipx install git+https://github.com/Dkrynen/lac
lac scan          # what am I working with?
lac recommend     # what should I run on it?
lac pull llama3.2:3b        # installs it -- LAC Pro can tune supported installs when licensed
lac chat          # TUI chat
```

### macOS & Linux apps

Coming soon — **[join the waitlist](https://dkrynen.github.io/lac/)** and each platform release lands in your inbox.

## Local Pro - the Tuning Cockpit

The free tier is complete and stays free. **Local Pro** adds the paid power tools:

- **Autopilot** - after Pro is installed and licensed, supported model installs can be benchmarked, GPU-offload swept, and tuned to your exact rig
- **`lac pro tune <model>` / `lac pro benchmark <model>`** - manual on-demand re-runs of the same sweep and benchmark steps autopilot uses
- **Private Hugging Face imports** - GGUF-first import for compatible repos, with local token storage for gated/private models
- **Local coding cockpit** - readiness checks and launch guidance for stronger coding models and agent workflows
- **Insights** - calibration history and regression detection ("your tok/s dropped 12% since that driver update")

Local Pro is planned at **$36/year** (the equivalent of $3/month). Checkout is **not open yet**; the [waitlist](https://dkrynen.github.io/lac/) hears first.

**Pro Cloud** is the planned **$20/month** higher tier. It includes everything in Local Pro, plus end-to-end encrypted sync and capped hosted agents. Encrypted sync is designed so LAC cannot read the ciphertext. Hosted processing is a separate, explicit path: only selected job inputs are decrypted for execution and may be sent to approved model providers. The 2.7 desktop now contains the fail-closed Google/GitHub account bridge and a single Account surface for Local, Local Pro, and Cloud state. The production Cloud origin is deliberately unconfigured, and hosted execution remains disabled until the launch gate has approved the service, provider, metering, retention, and operational evidence.

**At launch,** every paid buyer first signs in to a LAC account with Google or GitHub from the desktop Account page. Checkout starts from that authenticated account, and access follows the signed Polar webhook rather than the browser redirect. Polar then provides the Local Pro license key. Run `lac unlock <key>` or use **Settings -> Activate Pro** in the web UI; after activation, model execution and tuning use the configured Ollama endpoint (local by default) while the key-based entitlement periodically revalidates through the configured Pro activation service. Restart LAC so the Pro cockpit mounts cleanly. Free installs ship no Pro code.

## Hardware detection

| GPU | Method | Verified |
|-----|--------|----------|
| NVIDIA | `nvidia-smi` | NVIDIA driver/CLI path |
| AMD (Windows) | `vulkaninfo` → registry → WMI | RX 6800 XT (16 GB) |
| AMD (Linux) | `/sys/class/drm` sysfs | ROCm/Vulkan |
| Apple Silicon | `sysctl` unified memory | M-series |
| Intel | Registry fallback | Arc, UHD, Iris |

## Development

```bash
git clone https://github.com/Dkrynen/lac && cd lac
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt  # or bin/ on POSIX
.venv/Scripts/python server.py        # Flask + web UI on :5050
cd web && npm ci && npm run dev       # Vite dev server (proxies /api)
.venv/Scripts/python -m pytest -q    # test suite
.venv/Scripts/python scripts/installed_launch_smoke.py  # clean installed-exe launch/audit/shutdown proof
.venv/Scripts/python scripts/public_readiness_gate.py --include-live-import --include-launch-smoke --allow-existing-launch  # full gate against an already-running app
.venv/Scripts/python scripts/release_readiness.py  # read-only local/public release check
.venv/Scripts/python scripts/pro_commerce_readiness.py  # read-only Pro checkout/delivery readiness check
.venv/Scripts/python scripts/unified_product_audit.py --pro-root ../lac-pro --cloud-root ../lac-cloud  # real three-repo contract audit
.venv/Scripts/python scripts/installed_app_audit.py  # installed app page/API audit
.venv/Scripts/python scripts/installed_launch_smoke.py --allow-existing  # audit an already-running installed app
.venv/Scripts/python scripts/runtime_smoke.py --model qwen2.5:0.5b  # live installed-app chat/session smoke test
.venv/Scripts/python scripts/live_import_stress.py --preflight-only  # cheap HF/Pro resolver + disk preflight smoke
.venv/Scripts/python scripts/live_import_stress.py  # live HF import + disposable delete stress test
```

Plugins mount via the `lac.plugins` entry-point group — see [docs/PLUGINS.md](docs/PLUGINS.md). Contributions welcome: [CONTRIBUTING.md](CONTRIBUTING.md).

Read the [Security](SECURITY.md) and [Privacy](PRIVACY.md) policies before reporting a vulnerability or changing a network boundary.

Source builds may point the dormant account bridge at an approved staging API with `LAC_CLOUD_API_ORIGIN=https://...`. Frozen releases ignore that environment override and use the reviewed origin compiled into the client; the checked-in placeholder therefore keeps Cloud inert in unapproved builds.

## System requirements

- **OS**: Windows 10+, macOS 13+, Linux (x86_64)
- **Python**: 3.10+ (CLI/source installs)
- **Ollama**: required for model install, chat, and benchmarking
- **GPU**: optional — CPU-only and Apple Silicon fully supported

## License

Core: MIT — see [LICENSE](LICENSE). LAC Pro is a commercial add-on.
