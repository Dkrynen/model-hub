# LAC — Reddit Launch Planning: Session Handoff

Copy-paste everything below into a new Claude Code session to continue.

---

## Context

I'm Duan, and I built **LAC** — a local-LLM manager (repo `C:\Users\User\repos\model-hub`, private Pro plugin `C:\Users\User\repos\lac-pro`). It scans a user's hardware (GPU/VRAM/RAM/CPU), recommends which local LLMs actually fit and run well, installs them via Ollama, and lets you chat with them. It solves the single most common pain point in the local-LLM hobbyist space: **"which model can my machine actually run, and how fast will it go?"**

As of 2026-07-05 the app just went through a full rebrand (Apt/Model-Hub → **LAC**) and a 17-task pre-launch audit-fix pass (security fix, UX bugs, VRAM math bug, etc. — all closed, final whole-branch review = ready to merge). Current state:

- Repo is public and pushed: `github.com/Dkrynen/lac`
- CI is green (found + fixed 2 real bugs that had it silently red since the rebrand)
- Landing page is live: `https://dkrynen.github.io/lac/`
- **Draft** release `v2.3.0` is built with a real Windows installer (`LAC-Setup-2.3.0.exe`) and real release notes — still sitting as a draft, not published yet
- Pro tier is licensed via Polar.sh; a real checkout link is already live on the site (`$3/month billed annually`)

## What's left before I actually post to Reddit (in priority order)

1. **Fix the waitlist form — this is the one real blocker, not a nice-to-have.** `site/index.html`'s waitlist form is currently a `mailto:` action (`<form action="mailto:dkrynen9@gmail.com...">`). That means a visitor's browser has to open their local email client and they have to manually hit send — on mobile (most Reddit traffic) this either does nothing or silently fails for a large fraction of visitors. **Before any public post, swap this for a real form** — Tally.so is free and takes about 5 minutes to set up (no code, just an embed/redirect URL). I need to actually create that account myself; nobody else can do it for me.
2. Smoke-test the installer once — download `LAC-Setup-2.3.0.exe` from the draft release, run it, click through the one UAC prompt, confirm LAC actually launches. (A prior session tried to automate this and hit a hard wall: the installer requires admin, and Windows UAC can't be clicked through non-interactively — this needs an actual human at the keyboard, i.e. me, once.)
3. Once 1+2 pass, publish the `v2.3.0` release (currently a draft).
4. Record a real 10-15s terminal screengrab (`lac scan` → `lac recommend` → `lac pull`) for the README — currently a TODO placeholder. Not blocking a Reddit post, but worth doing before/soon after.

**Good news on scale**: there is no self-hosted backend of mine anywhere in the launch path. The landing page is static (GitHub Pages/CDN), the installer download is GitHub Releases (also CDN), and Pro licensing talks directly to Polar.sh's API — none of that is something I run or could overload. A spike of 100-1000 people hitting the site/downloads/checkout costs me nothing extra and needs zero scaling work from me. The ONLY actual capacity risk is #1 above (a broken lead-capture form, not a broken server) — fix that and the rest takes care of itself.

## Free vs. Pro — get this right before writing any copy

**Free (core, MIT-licensed, `model-hub` repo):**
- Hardware scan (GPU/VRAM/RAM/CPU detection; NVIDIA/AMD/Apple Silicon; multi-GPU tiering)
- Model recommendations — 91-model curated catalog scored by quality/speed/hardware-fit/context; every rec tagged `measured`/`calibrated`/`estimated` so you know how much to trust the number
- Multi-GPU / RAM-spill split-plan computation, with live "what-if" toggles (GPU on/off, allow/deny RAM spill) in the web UI
- Install via Ollama, streaming chat, full TUI
- CLI session save/export/import, workspaces (multiple hardware profiles)

**Pro (`lac-pro`, private plugin, $3/mo billed annually via Polar.sh license key):**
- **Autopilot** — the moment you install a model, Pro benchmarks *and* auto-tunes it (GPU-offload sweep) for your exact rig, with zero manual command. This is what turns a recommendation's tag from `calibrated`/`estimated` (a formula's guess) into `measured` (a real number from your actual hardware).
- `lac pro tune` / `lac pro benchmark` — the same sweep/benchmark, on demand
- `lac pro insights` — tracks your tok/s baseline over time and flags regressions (driver update, background load, model swap)

**The honest positioning right now**: Free already gets someone a complete, working, accurate experience end-to-end — scan, recommend, install, chat all work with zero Pro. Pro's entire pitch is "the same thing, but *measured on your exact machine* instead of estimated, automatically, forever." That's real value, but it's an automation/confidence layer over something a free user could largely approximate manually (by eyeballing tok/s themselves) — **not** the kind of hard capability gate that makes Claude Code Pro/Max feel unavoidable (where Free literally can't do certain things at all). If you want Pro to feel "irresistible" the way Duan described it, that gap is the thing to close — and it's a real product/positioning question, not a five-minute copy tweak. **Use `superpowers:brainstorming` for this before touching any code or copy** — it's a genuine repositioning decision (new capability? usage-limit gate on free? something else?), not a bug fix.

## The actual task for this session

Help me plan my **first Reddit post** for LAC. I want it casual and low-key, not a marketing pitch — think "hey guys, I built this for the biggest annoyance in running local models, figured I'd share" rather than a launch announcement. Genuinely just wants to be helpful to the community and see what people think, not a sales pitch.

Things to work through together (use `superpowers:brainstorming` — this is new creative work):
- Which subreddit(s) — my instinct is r/LocalLLaMA as the obvious first one (the "what can my hardware run" question is asked there constantly), but confirm/challenge that and consider whether a second one makes sense later.
- Actual post title + body draft, in the casual voice above.
- What I actually say about Free vs Pro when people ask "so what's the catch" — needs to be honest and non-salesy, using the breakdown above.
- Whether I mention Pro/pricing in the post at all, or let people discover it themselves after trying Free.
- Timing — anything to line up (the waitlist fix + installer smoke-test + publishing the release all need to happen first, per the checklist above).

Standing conventions from this whole project, still in force: subagent-driven development for any code changes, TDD, fix-don't-redesign, never push to origin without my separate explicit go-ahead each time, `lac-pro` never gets a git remote. None of that likely applies to a Reddit-post-planning session, but the brainstorming discipline for the Pro-repositioning question does.
