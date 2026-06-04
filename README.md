# Lyvica Scoring Agent

Finds and scores business websites that are ripe for a rebuild. Paste domains or upload a CSV — each site is scored across mobile, performance, security, tech stack, content freshness, and SEO hygiene.

---

## Setup for a new developer

### 1. Clone the repo

```bash
git clone https://github.com/BarrenWuffet402/lyvica-scoring.git
cd lyvica-scoring
```

### 2. Create a virtual environment and install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .
```

### 3. Create a `.env` file

Copy the example and fill in your keys:

```bash
cp .env.example .env
```

Then open `.env` and set:

```
PAGESPEED_API_KEY=your_key_here
GOOGLE_PLACES_API_KEY=your_key_here
```

Both keys come from the same Google Cloud project:
1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a project (or select an existing one)
3. Enable these two APIs: **PageSpeed Insights API** and **Places API (New)**
4. Go to **Credentials** → **Create credentials** → **API key**
5. You can use the same key for both — paste it into `.env` twice

> The PageSpeed key unlocks mobile + performance scoring (35% of the rubric).
> The Places key powers Step 1 — finding businesses by city + industry via Google Maps.

The other keys (`GATEWAY_API_KEY`, `BUILTWITH_API_KEY`) are optional — the agent runs without them, just with lower signal coverage.

### 4. Run locally

```bash
uvicorn web.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000).

---

## Deploying to Render

1. Push to GitHub
2. Go to [render.com](https://render.com) → **New Web Service** → connect the repo
3. Render picks up `render.yaml` automatically — no extra config needed
4. In the Render dashboard go to **Environment** and add:
   - `PAGESPEED_API_KEY` = your key
5. Hit **Deploy**

---

## How scoring works

Each site is scored 0–100 across seven signals (100 = most outdated):

| Signal | Weight | Source |
|---|---|---|
| Mobile-friendliness | 20% | PageSpeed Insights API |
| Visual datedness | 20% | Vision model (optional) |
| Tech obsolescence | 20% | HTML fingerprinting |
| Page performance | 15% | PageSpeed Insights API |
| Security (HTTPS) | 10% | SSL check |
| Content freshness | 10% | Wayback Machine |
| SEO hygiene | 5% | HTML meta tags |

Score is normalized by whichever signals actually returned data, so a partial result is still meaningful. Confidence (shown in the JSON output) reflects what fraction of the rubric was measured.

**Tiers:** Hot ≥ 70 · Warm 50–69 · Cold < 50

---

## CSV format

Upload any CSV with a column named `domain`, `url`, or `website`. Full URLs are fine — the agent strips the scheme and path automatically.

```
business_name,website
Acme Corp,https://www.acmecorp.com
Old Site Inc,oldsite.net
```
