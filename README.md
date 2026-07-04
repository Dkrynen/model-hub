<img src="assets/leaf-mark.svg" width="72" alt="LAC vein-leaf mark" />

# LAC — local AI, sorted.

**Scans your hardware. Recommends models that actually fit. LAC Pro benchmarks and auto-tunes every one you install.**

LAC is a local-LLM manager built around one question: *what's the best model this machine can actually run?* It scans your GPU/VRAM/RAM/CPU, ranks models against your real hardware (including multi-GPU and RAM-spill split plans), installs them via [Ollama](https://ollama.com), and then — this is the part nobody else does — **calibrates its own predictions against real benchmarks of your rig**, so recommendations get more accurate the more you use it.

<!-- TODO(launch): replace with a 10-15s screengrab GIF showing: lac scan → lac recommend → lac benchmark. Record at 1080p, terminal on dark bg. -->

## Features

- **Hardware scan** — GPU, VRAM, RAM, CPU on Windows, Linux, macOS (NVIDIA, AMD, Apple Silicon, Intel)
- **Fit-aware recommendations** — 91-model curated catalog scored by quality, speed, hardware fit, and context; multi-GPU tiering (dGPU → iGPU → RAM) with per-model split plans
- **Real-speed calibration** — recs are tagged `measured` / `calibrated` / `estimated` with confidence bands; LAC Pro's autopilot feeds the `measured` tier automatically on every install
- **What-if controls** — toggle GPUs on/off, allow/deny RAM spill, and watch the recommendations recompute live in the web UI
- **Model management + chat** — install, run, delete; streaming chat with session persistence; full TUI

## Install

### Windows (recommended)

Download the latest `LAC-Setup-x.x.x.exe` from [Releases](https://github.com/Dkrynen/lac/releases) and run it.

### Any platform (CLI via pipx)

```bash
# Requires Python 3.10+ and Ollama (https://ollama.com/download)
pipx install git+https://github.com/Dkrynen/lac
lac scan          # what am I working with?
lac recommend     # what should I run on it?
lac pull llama3.2:3b        # installs it -- LAC Pro (if licensed) auto-tunes it for your rig
lac chat          # TUI chat
```

### macOS & Linux apps

Coming soon — **[join the waitlist](https://dkrynen.github.io/lac/)** and each platform release lands in your inbox.

## LAC Pro — the Tuning Cockpit

The free tier is complete and stays free. **LAC Pro** adds the power tools:

- **Autopilot** — every model you install is automatically benchmarked, GPU-offload swept, and tuned to your exact rig, with zero commands
- **`lac pro tune <model>` / `lac pro benchmark <model>`** — manual on-demand re-runs of the same sweep and benchmark steps autopilot uses
- **Offload controls** — per-model layer splits, iGPU control, context presets
- **Insights** — calibration history and regression detection ("your tok/s dropped 12% since that driver update")

Cheap subscription, priced to be a no-brainer. Landing soon — the [waitlist](https://dkrynen.github.io/lac/) hears first.

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
git clone https://github.com/Dkrynen/lac && cd lac
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt  # or bin/ on POSIX
.venv/Scripts/python server.py        # Flask + web UI on :5050
cd web && npm ci && npm run dev       # Vite dev server (proxies /api)
.venv/Scripts/python -m pytest -q    # test suite
```

Plugins mount via the `lac.plugins` entry-point group — see [docs/PLUGINS.md](docs/PLUGINS.md). Contributions welcome: [CONTRIBUTING.md](CONTRIBUTING.md).

## System requirements

- **OS**: Windows 10+, macOS 13+, Linux (x86_64)
- **Python**: 3.10+ (CLI/source installs)
- **Ollama**: required for model install, chat, and benchmarking
- **GPU**: optional — CPU-only and Apple Silicon fully supported

## License

Core: MIT — see [LICENSE](LICENSE). LAC Pro is a commercial add-on.
