# Contributing to APT

Thanks for looking under the hood.

## Dev setup

```bash
git clone https://github.com/Dkrynen/model-hub && cd model-hub
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt pytest pytest-asyncio apispec   # bin/ on POSIX
.venv/Scripts/python server.py          # Flask + web UI on :5050
cd web && npm ci && npm run dev         # Vite dev server (proxies /api)
```

## Before you open a PR

- `python -m pytest -q` — green (live-Ollama tests auto-skip when Ollama is down)
- `cd web && npm run typecheck && npm run build` — both exit 0
- Match the code around you; add tests for behavior you add or change

CI runs the suite on Windows, Ubuntu, and macOS plus the web gates — a PR that's green locally should be green there.

## Plugins

APT is open-core: plugins mount through the `apt.plugins` entry-point group
(CLI subcommands + Flask routes) with per-plugin error isolation. Authoring
guide: [docs/PLUGINS.md](docs/PLUGINS.md).

## Scope notes

- The model catalog (`backend/cookbook/data/models.json`) is curated —
  catalog PRs should cite the Ollama library page for every entry touched.
- Scoring-engine changes need a regression test demonstrating the ranking
  they fix (see `tests/test_recommend.py` for the pattern).
