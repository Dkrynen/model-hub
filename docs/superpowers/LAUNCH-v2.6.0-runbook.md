# LAC v2.6.0 — Launch Runbook + Reddit Post

**Created 2026-07-06.** Everything left between "shipped" and "first Reddit post is live." Work top to bottom.
Status going in: code public + CI green, `v2.6.0` **released** (`github.com/Dkrynen/lac/releases/tag/v2.6.0`),
cockpit Pro **live on R2 + verified**. Remaining = 1 real blocker (waitlist form) + a few checks.

---

## 0. Order of operations (do these before posting)

1. Waitlist form fixed (§1) — the only hard blocker.
2. Product smoke passes (§2).
3. Installer smoke passes (§3).
4. Polar funnel clean (§4).
5. Post to Reddit (§5) — the post text is in §6.

Do NOT post to Reddit until 1–4 are done. A broken waitlist form or a 404-after-payment is the only thing a
traffic spike can actually hurt.

---

## 1. 🔴 Fix the waitlist form (Tally) — the one real blocker

Right now `site/index.html` line ~149 is a `mailto:` form. On mobile (most Reddit traffic) that opens the
visitor's email app or silently does nothing — you lose the lead. Swap it for a real Tally form.

### Set up Tally (≈5 min, free, your account)
1. Go to **https://tally.so** → **Sign up** (Google or email — free plan is plenty).
2. Click **+ Create new form** → choose **Start from scratch** (or "Blank").
3. Name it (top-left): **"LAC — Mac/Linux waitlist"**.
4. Add the fields (type `/` to insert a block, or use the `+`):
   - An **Email** block → mark it **Required**. (This is the only field you truly need.)
   - *(optional)* a **Multiple choice** or short-text block: **"Which platform are you waiting for? (macOS / Linux)"** — nice signal for which binary to ship second.
   - *(optional)* a short-text **"Anything you want us to know? (optional)"**.
5. *(optional)* Click the last screen / **"Thank you"** page and set the message to:
   **"Thanks — you'll hear the moment your platform's build lands."**
6. Top-right → **Publish**.
7. Top-right → **Share** → copy the form's **link** (looks like `https://tally.so/r/abc123`).
8. **Paste that link to me** → I'll replace the `mailto:` form in `site/index.html` with a clean
   "Join the waitlist" button pointing at it (or an inline embed if you prefer — say which), commit, and it
   auto-deploys via GitHub Pages.

*(Submissions email you by default; you can later connect Tally → Google Sheets/Notion if you want, but the
default is fine for launch.)*

---

## 2. 👁️ Product smoke (≈3 min — your eyes)

Your machine already has the new cockpit `.pyd` installed (I swapped it), so this tests the real thing.
1. Open **LAC** (Start menu, or `dist\lac.exe`).
2. Sidebar → **Pro**. You should see the cockpit (status header = active), not the old "enter key" box.
3. In the **Tune** hero: pick **`qwen2.5:0.5b`** → **Run sweep**. Wait for it to finish (~30–60s).
4. Confirm you get a **before→after tok/s** number + a per-config table → click **Apply** on the winner
   (or "auto" if that wins) → it should say it created a `-tuned` variant.
5. Sidebar → **Chat** → pick a model → send "hi". The **first** reply should be near-instant (the warm-up fix).
6. Glance at **Insights / Autopilot / Benchmark / Import** panels — they should render real data, no errors.

If anything looks broken here, stop and ping me before posting.

---

## 3. 👁️ Installer smoke (≈3 min — human-only, can't be automated)

Proves a brand-new user's download → install → launch works (admin install needs a real click-through).
1. `github.com/Dkrynen/lac/releases/latest` → download **`LAC-Setup-2.6.0.exe`**.
2. Run it. **Windows SmartScreen** will warn ("Windows protected your PC") because it's unsigned →
   **More info → Run anyway**. (This is expected; you'll answer it a lot on Reddit — see §5.)
3. Click through the **UAC** prompt (Yes).
4. Let it install → launch LAC → confirm the **native window opens** and you can scan/see models.
   *(Optional but ideal: do this on a second machine or a fresh Windows user account to catch anything
   machine-specific.)*

---

## 4. ⚙️ Polar funnel (≈5 min — your dashboard)

These affect real buyers the moment traffic hits. In the **Polar** dashboard:
1. **Checkout Success URL** (Products → your product → Checkout Link/Settings): either set it to
   **`https://dkrynen.github.io/lac/#pro`** or **clear it** so Polar's own hosted confirmation page (which
   shows the license key) handles it. **Recommended: clear it.** Otherwise buyers 404 after paying (the old
   value points at the pre-rename `/model-hub` page).
2. **License key prefix** (License Keys benefit settings): change `APT--` → `LAC-`. Cosmetic — existing keys
   keep working — but it looks sloppy at launch.
3. *(optional)* Add a **monthly** price if you want a monthly option; otherwise annual-only is fine. If you
   leave it annual-only, make sure the landing/site doesn't imply a monthly toggle that doesn't exist.
4. *(optional)* Storefront branding (logo/colors) if you're linking people to the Polar storefront.

---

## 5. 📣 Post to Reddit (r/LocalLLaMA)

**Where:** `r/LocalLLaMA` first (they ask "what can my rig run + how fast" constantly — perfect fit). Consider
a second sub later (r/Ollama, r/selfhosted) once you see how the first lands — don't spray day one.

**Before you post:**
- Read r/LocalLLaMA's rules once (they tolerate "I built this" if it's genuinely useful + you're transparent
  it's yours + it's not pure marketing — the post in §6 is written to fit that).
- **Timing:** post on a **weekday, US morning–midday (≈9am–1pm ET)** — that's when the sub is busiest, and the
  first hour of engagement decides reach.
- Pick a **flair** if offered (something like *Resources* / *Tutorial | Guide* / *Discussion* — whatever fits;
  not "New Model").
- Post it as a **text/self post** (not a link post) — paste the body from §6, keep the link **in the body**.

**After you post — the first hour matters:**
- **Reply fast** to every comment. Be humble; the sub rewards builders who take feedback, punishes defensiveness.
- Have the **SmartScreen answer** ready (you'll get it a lot): *"Yeah — it's unsigned, code signing is deferred
  until there's revenue to justify the cert. It's fully open source, so you can read every line before running
  it: [repo link]."*
- If someone's rec/tok-s number looks off for their rig, **ask for their `lac scan` output** — that's gold
  feedback and shows you're serious about accuracy.
- Don't argue about the $3 Pro. If someone says free is enough — agree; that's the honest pitch.

---

## 6. The Reddit post (copy-paste)

**Title:**
> I built LAC — it scans your hardware and tells you which local LLMs will actually run (and how fast), then installs + benchmarks them. Free, open-core, Windows-first.

**Body:**

Every week someone here asks *"I've got a [GPU] and [X]GB RAM — what can I actually run?"* and the answer is
usually a pile of guesswork. I got tired of guessing on my own rig, so I built **LAC** to answer it properly.

**What it does (all free):**
- **Scans your hardware** — GPU(s), VRAM, RAM, CPU, and how a model would split across them (dGPU → iGPU → RAM spill).
- **Recommends** which models will actually fit and run well on *your* machine, ranked, with an honest
  confidence tag on every speed number: `measured` (benchmarked on your hardware), `calibrated` (adjusted from
  your past runs), or `estimated` (a formula's guess). No made-up tok/s.
- **Installs** the one you pick via Ollama and lets you **chat** with it — in a real native app, not a browser tab.

The whole scan → recommend → install → chat loop is free, no account, nothing phones home.

**The technical bit (since it's this sub):** the recommender doesn't just compare model size to VRAM. It builds
compute tiers from your actual devices, models the offload split + the RAM-spill penalty, and — once you've run
a benchmark or two — calibrates its predictions to *your* measured tok/s and software stack. Ollama's own
tooling won't predict speed; this does, and tells you how much to trust each number.

**Free vs Pro, honestly:** Free already gives you the complete working experience end to end. **Pro ($3/mo)** is
the *measured-on-your-exact-rig* layer + a cockpit: it auto-benchmarks and GPU-offload-tunes every model you
install (with before→after tok/s you can see and apply), tracks your speed history and flags regressions, lets
you import any Hugging Face model, and pre-warms models so the first chat reply is instant instead of a cold
load. It's an automation/confidence layer over stuff a free user could approximate by hand — not a capability
paywall. If it's not worth $3 to you, free genuinely has you covered.

**Honest caveats:**
- **Windows-first.** The CLI works anywhere via `pipx install git+https://github.com/Dkrynen/lac`; the native
  installer is Windows-only for now (Mac/Linux are on a waitlist on the site).
- **Unsigned** — Windows SmartScreen will warn you (*More info → Run anyway*). Signing is deferred until there's
  revenue. It's open source, so you can read every line first.
- Built **on top of Ollama** (you'll need it installed).
- **Local + no telemetry** — nothing leaves your machine except a GitHub version check and, if you buy Pro,
  license validation.

Repo + download: **https://github.com/Dkrynen/lac**

Would genuinely love feedback from this crowd — especially whether the `measured` numbers match what you
actually see on your hardware. Roast it.

---

## 7. Nice-to-have (NOT blockers — after launch)

- **Landing copy predates the cockpit** — `site/index.html` doesn't mention tuning/insights/import at all, which
  is your strongest Pro pitch. Worth a refresh (I can do it) but not before posting.
- **Custom domain** for the gate/landing (e.g. `gate.acend.online`) instead of the `refersal.workers.dev` /
  `github.io` URLs — polish, one-line config change.
- **Second-machine install test** to catch anything hardware-specific.
