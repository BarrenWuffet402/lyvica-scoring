# Lyvica — Website Scoring Agent

Lyvica is the **scoring module** of a three-stage sales pipeline:

```
[Stage 1: Sourcing] → [Stage 2: Scoring ← you are here] → [Stage 3: Sales outreach]
```

Given a list of business domains, it scores each site 0–100 for rebuild opportunity (100 = most outdated). It runs a local web UI where you can source businesses from Google Maps, paste domains, or upload a CSV — and stream scored results in real time.

---

## Architecture

```
web/
  main.py          FastAPI app — UI (inline HTML/JS) + SSE streaming endpoint
  sourcer.py       Google Places API wrapper — finds businesses by city + industry

src/lyvica/
  agent.py         Async orchestrator — runs all tools in parallel per domain
  scorer.py        Pure scoring logic — weights, normalization, tier assignment
  models.py        Pydantic models — JobRequest, Lead, Evidence, Subscores
  config.py        Settings loaded from .env via pydantic-settings

  tools/
    http_fetch.py  Fetches HTML, parses signals (viewport, CMS hints, SEO tags)
    builtwith.py   Tech detection from HTML/headers (no API required)
    pagespeed.py   Google PageSpeed Insights API v5 (Lighthouse)
    ssl_check.py   HTTPS + cert validity + mixed content check
    wayback.py     Wayback Machine CDX API — last significant content change
    screenshot.py  Headless screenshot capture (optional, for vision scoring)
    vision.py      Vision model — rates visual datedness from screenshot
```

### Request flow (per domain)

```
domain
  │
  ├── fetch_page()          → raw HTML + response headers
  ├── parse_html_signals()  → viewport tag, CMS hints, SEO meta tags, footer year
  ├── domain_lookup()       → detected tech stack, CMS + version
  ├── run_psi()             → Lighthouse performance score, viewport audit
  ├── check_ssl()           → HTTPS, cert valid, mixed content
  ├── get_last_change()     → last Wayback Machine snapshot with content change
  └── rate_visual_datedness() → optional vision model score (0–100)
         │
         ▼
    compute subscores  →  score_lead()  →  Lead (score, tier, pitch angles)
```

All tool calls for a domain run concurrently via `asyncio.gather`. Results stream back to the browser as Server-Sent Events as each domain finishes.

---

## Scoring logic

Each site receives a **rebuild opportunity score** 0–100. Higher = more outdated/ripe for rebuild.

| Signal | Weight | Source | How it scores |
|---|---|---|---|
| Mobile-friendliness | 20% | PageSpeed viewport audit | No `<meta name="viewport">` → 100. Has it → signal skipped. |
| Visual datedness | 20% | Vision model (optional) | 0–100 rating of screenshot, 0 = modern |
| Tech obsolescence | 20% | HTML fingerprinting | jQuery 1.x / Flash / PHP 5 → 80–100. Modern stack (React, Next.js, etc.) → 0. |
| Page performance | 15% | PageSpeed Lighthouse | Only penalises slow sites (Lighthouse score < 50). Fast old sites don't get credit. |
| Security | 10% | SSL check | No HTTPS → 100. Invalid cert → 80. Mixed content → 40. |
| Content freshness | 10% | Wayback Machine | Last change > 4 years ago → 100. < 1 year → 0. Interpolated between. |
| SEO hygiene | 5% | HTML meta tags | Missing meta description / Open Graph / schema.org each add ~34 points. |

### Normalization

Scores are normalized by the weight of signals that actually returned data:

```
score = sum(weight[k] * subscore[k] for measured signals) / sum(weight[k] for measured signals)
```

A site where PageSpeed times out and vision isn't configured still gets a meaningful score from the remaining signals. The `confidence` field (0.0–1.0) shows what fraction of the total rubric weight was measured.

### Tiers

| Tier | Score | Meaning |
|---|---|---|
| Hot 🔥 | ≥ 70 | Strong rebuild candidate — lead multiple pitch angles |
| Warm 🌡 | 50–69 | Worth a conversation |
| Cold ❄️ | < 50 | Site is likely modern enough, deprioritise |

A site with ≥ 2 unmeasured signals is capped at Warm even if the score would be Hot, to avoid overconfidence.

---

## Local setup

### 1. Clone

```bash
git clone https://github.com/BarrenWuffet402/lyvica-scoring.git
cd lyvica-scoring
```

### 2. Python environment

Requires Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .
```

### 3. Environment variables

```bash
cp .env.example .env
```

Open `.env` and fill in:

```
PAGESPEED_API_KEY=your_key_here
GOOGLE_PLACES_API_KEY=your_key_here
```

The app runs without these keys — PageSpeed and Places sourcing will simply be unavailable, and the score will be based on the remaining signals.

### 4. Run

```bash
uvicorn web.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000).

---

## Google API keys

Both keys come from the same Google Cloud project. One key can be reused for both.

1. Go to [console.cloud.google.com](https://console.cloud.google.com) and create or select a project
2. **APIs & Services → Library** — enable both:
   - **PageSpeed Insights API**
   - **Places API (New)** — must be the "(New)" variant, not the legacy Places API
3. **Credentials → Create credentials → API key**
4. If your key has API restrictions set, add both APIs to the allowed list

> **Important:** The key must not have HTTP referrer restrictions if you're deploying to a server — server-side requests don't send a `Referer` header. Use IP restrictions or no restrictions instead.

---

## Deploying to Render

The repo includes `render.yaml` so Render picks up the config automatically.

1. Push to GitHub
2. [render.com](https://render.com) → **New Web Service** → connect the repo
3. In **Environment**, add:
   - `PAGESPEED_API_KEY`
   - `GOOGLE_PLACES_API_KEY`
4. Deploy

Build command: `pip install -e .`
Start command: `uvicorn web.main:app --host 0.0.0.0 --port $PORT`
Python version: 3.11

> The free Render tier has 512 MB RAM and spins down after inactivity. Concurrency is set to 3 simultaneous domain evaluations to stay within memory limits.

---

## Input formats

### Paste domains

One domain or URL per line — scheme and path are stripped automatically:

```
example.com
https://www.oldsite.co.uk/home
acmecorp.net
```

### CSV upload

Any CSV with a column named `domain`, `url`, `website`, `site`, `homepage`, or `link`. If none match, the first column is used.

```csv
business_name,website
Acme Corp,https://www.acmecorp.com
Old Site Inc,oldsite.net
```

### Google Places sourcing (Step 1)

Enter a city/area and pick an industry — the app queries the Places API and returns up to 60 businesses with their websites. Click **Score these** to feed them directly into the scorer.

Requires `GOOGLE_PLACES_API_KEY` with Places API (New) enabled.

---

## Optional signals

| Signal | What to configure | Effect |
|---|---|---|
| Visual datedness | Set `GATEWAY_BASE_URL` + `GATEWAY_API_KEY` + `VISION_MODEL` in `.env` | Adds 20% weight from a vision model rating screenshots |
| BuiltWith tech data | Set `BUILTWITH_API_KEY` | Richer tech stack detection; without it the agent falls back to free HTML/header fingerprinting |

---

## Key design decisions

**No LLM in the scoring path.** All subscores are deterministic — rule-based tech detection, API calls, HTML parsing. LLMs are only used optionally for the visual datedness signal via a screenshot. This keeps scoring fast, cheap, and reproducible.

**Normalization over hard zeros.** Missing signals (PSI timeout, no vision key) are excluded from the weighted average rather than counted as zero. A site with 4 out of 7 signals measured still gets a calibrated score.

**Performance only penalises slow sites.** Lighthouse performance ≥ 50 is excluded from scoring — a simple 2005 HTML site can load in 0.1s and should not be rewarded for it. Only genuinely slow sites (score < 50) contribute a performance problem score.
