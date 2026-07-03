# APT — local AI, sorted.

**Scans your hardware. Recommends models that actually fit. Benchmarks real tok/s — not guesses.**

APT is a local-LLM manager built around one question: *what's the best model this machine can actually run?* It scans your GPU/VRAM/RAM/CPU, ranks models against your real hardware (including multi-GPU and RAM-spill split plans), installs them via [Ollama](https://ollama.com), and then — this is the part nobody else does — **calibrates its own predictions against real benchmarks of your rig**, so recommendations get more accurate the more you use it.

<!-- TODO(launch): hero screenshot / demo GIF here -->

## Features

- **Hardware scan** — GPU, VRAM, RAM, CPU on Windows, Linux, macOS (NVIDIA, AMD, Apple Silicon, Intel)
- **Fit-aware recommendations** — 91-model curated catalog scored by quality, speed, hardware fit, and context; multi-GPU tiering (dGPU → iGPU → RAM) with per-model split plans
- **Real-speed calibration** — `aptm benchmark` measures actual tok/s and feeds a per-machine calibration loop; recs are tagged `measured` / `calibrated` / `estimated` with confidence bands
- **What-if controls** — toggle GPUs on/off, allow/deny RAM spill, and watch the recommendations recompute live in the web UI
- **Model management + chat** — install, run, delete; streaming chat with session persistence; full TUI
- **Benchmark from the browser** — one dialog, live per-run tok/s, recommendations recalibrate on completion

## Install

### Windows (recommended)

Download the latest `Model-Hub-Setup-x.x.x.exe` from [Releases](https://github.com/Dkrynen/model-hub/releases) and run it.

### Any platform (CLI via pipx)

```bash
# Requires Python 3.10+ and Ollama (https://ollama.com/download)
pipx install git+https://github.com/Dkrynen/model-hub
aptm scan          # what am I working with?
aptm recommend     # what should I run on it?
aptm benchmark llama3.2:3b   # real tok/s -> calibrates future recs
aptm chat          # TUI chat
```

> The command is `aptm` (APT-manager) — deliberately not `apt`, your Debian package manager stays untouched.

### macOS & Linux apps

Coming soon — **[join the waitlist](https://dkrynen.github.io/model-hub/)** and each platform release lands in your inbox.

## APT Pro — the Tuning Cockpit

The free tier is complete and stays free. **APT Pro** adds the power tools:

- **`apt pro tune <model>`** — sweeps GPU-offload configurations (auto / all layers / 75% / 50%), benchmarks each on *your* hardware, and bakes the fastest into a ready-to-use `<model>-tuned` variant
- **Offload controls** — per-model layer splits, iGPU control, context presets
- **Insights** — calibration history and regression detection ("your tok/s dropped 12% since that driver update")

Cheap subscription, priced to be a no-brainer. Landing soon — the [waitlist](https://dkrynen.github.io/model-hub/) hears first.

## Hardware detection

| GPU | Method | Verified |
|-----|--------|----------|
| NVIDIA | `nvidia-smi` | All models |
| AMD (Windows) | `vulkaninfo` → registry → WMI | RX 6800 XT (16 GB) |
| AMD (Linux) | `/sys/class/drm` sysfs | ROCm/Vulkan |
| Apple Silicon | `sysctl` unified memory | M-series |
| Intel | Registry fallback | Arc, UHD, Iris |

## Development

```bash
git clone https://github.com/Dkrynen/model-hub && cd model-hub
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt  # or bin/ on POSIX
.venv/Scripts/python server.py        # Flask + web UI on :5050
cd web && npm ci && npm run dev       # Vite dev server (proxies /api)
.venv/Scripts/python -m pytest -q    # test suite
```

Plugins mount via the `apt.plugins` entry-point group — see [docs/PLUGINS.md](docs/PLUGINS.md). Contributions welcome: [CONTRIBUTING.md](CONTRIBUTING.md).

## System requirements

- **OS**: Windows 10+, macOS 13+, Linux (x86_64)
- **Python**: 3.10+ (CLI/source installs)
- **Ollama**: required for model install, chat, and benchmarking
- **GPU**: optional — CPU-only and Apple Silicon fully supported

## License

Core: MIT — see [LICENSE](LICENSE). APT Pro is a commercial add-on.
