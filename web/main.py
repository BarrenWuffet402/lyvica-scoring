"""FastAPI web interface for Lyvica Scoring Agent."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import re
import uuid
from typing import Optional

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse

from lyvica.agent import LyvicaAgent, _to_domain
from lyvica.models import JobRequest

logger = logging.getLogger(__name__)

app = FastAPI(title="Lyvica", docs_url=None, redoc_url=None)

DEMO_CAP = 30  # max domains per job on the hosted demo

# ---------------------------------------------------------------------------
# HTML (single-file, no build step)
# ---------------------------------------------------------------------------

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Lyvica — Website Scoring</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/tailwindcss@3.4.17/dist/tailwind.min.css">
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    body { font-family: 'Inter', sans-serif; }
    .gradient-header { background: linear-gradient(135deg, #1e1b4b 0%, #3730a3 60%, #4f46e5 100%); }
    @keyframes spin { to { transform: rotate(360deg); } }
    .spin { animation: spin 0.9s linear infinite; }
    @keyframes fadeIn { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: translateY(0); } }
    .fade-in { animation: fadeIn 0.25s ease-out forwards; }
    .score-bar-fill { transition: width 0.6s ease; }
  </style>
</head>
<body class="bg-slate-50 min-h-screen">

<!-- Header -->
<div class="gradient-header text-white py-10 px-6 text-center shadow-lg">
  <div class="max-w-2xl mx-auto">
    <div class="flex items-center justify-center gap-3 mb-2">
      <svg class="w-8 h-8 text-indigo-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
          d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
      </svg>
      <h1 class="text-3xl font-bold tracking-tight">Lyvica</h1>
    </div>
    <p class="text-indigo-200 text-sm font-medium uppercase tracking-widest">Website Rebuild Opportunity Scorer</p>
    <p class="text-indigo-300 text-sm mt-3">Paste domains or upload a CSV — we score each site for how ripe it is for a rebuild.</p>
  </div>
</div>

<!-- Main -->
<div class="max-w-4xl mx-auto px-4 py-8 space-y-6">

  <!-- Input card -->
  <div class="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden">
    <div class="border-b border-slate-100">
      <div class="flex">
        <button id="tab-paste" onclick="switchTab('paste')"
          class="px-6 py-3 text-sm font-medium border-b-2 border-indigo-600 text-indigo-600 focus:outline-none">
          Paste Domains
        </button>
        <button id="tab-csv" onclick="switchTab('csv')"
          class="px-6 py-3 text-sm font-medium border-b-2 border-transparent text-slate-500 hover:text-slate-700 focus:outline-none">
          Upload CSV
        </button>
      </div>
    </div>

    <div class="p-6 space-y-4">
      <!-- Paste tab -->
      <div id="pane-paste">
        <label class="block text-sm font-medium text-slate-700 mb-1">
          One domain or URL per line
        </label>
        <textarea id="domains-text" rows="7"
          class="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm font-mono text-slate-800 focus:outline-none focus:ring-2 focus:ring-indigo-400 resize-none placeholder-slate-400"
          placeholder="example.com&#10;https://www.oldsite.co.uk&#10;acmecorp.net"
          oninput="updateCount()"></textarea>
        <p id="domain-count" class="text-xs text-slate-400 mt-1">0 domains</p>
      </div>

      <!-- CSV tab -->
      <div id="pane-csv" class="hidden">
        <label class="block text-sm font-medium text-slate-700 mb-1">
          CSV file — needs a column named <code class="bg-slate-100 px-1 rounded">domain</code>,
          <code class="bg-slate-100 px-1 rounded">url</code>, or <code class="bg-slate-100 px-1 rounded">website</code>
        </label>
        <label id="drop-zone"
          class="flex flex-col items-center justify-center border-2 border-dashed border-slate-300 rounded-xl py-10 px-6 cursor-pointer hover:border-indigo-400 hover:bg-indigo-50 transition-colors">
          <svg class="w-10 h-10 text-slate-400 mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"
              d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
          </svg>
          <span id="csv-label" class="text-sm text-slate-500">Drop CSV here or <span class="text-indigo-600 font-medium">browse</span></span>
          <input type="file" id="csv-file" accept=".csv,text/csv" class="hidden" onchange="onFileSelect(this)" />
        </label>
      </div>

      <!-- Settings row -->
      <div class="flex flex-wrap items-center gap-6 pt-2">
        <div class="flex-1 min-w-[180px]">
          <label class="block text-xs font-medium text-slate-500 mb-1">
            Min score to include: <span id="score-val" class="text-indigo-600 font-semibold">50</span>
          </label>
          <input type="range" id="min-score" min="0" max="100" value="50" step="5"
            class="w-full accent-indigo-600"
            oninput="document.getElementById('score-val').textContent = this.value; renderTable();" />
        </div>
        <button id="score-btn" onclick="startScoring()"
          class="px-6 py-2.5 bg-indigo-600 hover:bg-indigo-700 active:bg-indigo-800 text-white text-sm font-semibold rounded-lg shadow transition-colors disabled:opacity-50 disabled:cursor-not-allowed">
          Score Domains
        </button>
      </div>
    </div>
  </div>

  <!-- Progress -->
  <div id="progress-section" class="hidden bg-white rounded-2xl shadow-sm border border-slate-200 p-6">
    <div class="flex items-center gap-3 mb-4">
      <svg id="progress-spinner" class="spin w-5 h-5 text-indigo-600" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"/>
      </svg>
      <span id="progress-label" class="text-sm font-medium text-slate-600">Preparing…</span>
    </div>
    <div class="w-full bg-slate-100 rounded-full h-2">
      <div id="progress-bar" class="bg-indigo-600 h-2 rounded-full score-bar-fill" style="width:0%"></div>
    </div>
    <p id="progress-sub" class="text-xs text-slate-400 mt-2"></p>
  </div>

  <!-- Summary stats -->
  <div id="stats-section" class="hidden grid grid-cols-2 sm:grid-cols-4 gap-4">
    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-4 text-center">
      <p class="text-2xl font-bold text-slate-800" id="stat-total">0</p>
      <p class="text-xs text-slate-500 mt-1">Evaluated</p>
    </div>
    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-4 text-center">
      <p class="text-2xl font-bold text-emerald-600" id="stat-qualified">0</p>
      <p class="text-xs text-slate-500 mt-1">Qualified</p>
    </div>
    <div class="bg-white rounded-xl border border-red-100 shadow-sm p-4 text-center">
      <p class="text-2xl font-bold text-red-600" id="stat-hot">0</p>
      <p class="text-xs text-slate-500 mt-1">🔥 Hot</p>
    </div>
    <div class="bg-white rounded-xl border border-amber-100 shadow-sm p-4 text-center">
      <p class="text-2xl font-bold text-amber-600" id="stat-warm">0</p>
      <p class="text-xs text-slate-500 mt-1">🌡 Warm</p>
    </div>
  </div>

  <!-- Results table -->
  <div id="results-section" class="hidden bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden">
    <div class="px-6 py-4 border-b border-slate-100 flex items-center justify-between">
      <h2 class="text-sm font-semibold text-slate-700">Results</h2>
      <button onclick="downloadCSV()" class="text-xs text-indigo-600 hover:underline font-medium">
        Download CSV
      </button>
    </div>
    <div class="overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr class="bg-slate-50 text-xs text-slate-500 uppercase tracking-wide">
            <th class="px-4 py-3 text-left font-medium">#</th>
            <th class="px-4 py-3 text-left font-medium">Domain</th>
            <th class="px-4 py-3 text-center font-medium">Score</th>
            <th class="px-4 py-3 text-center font-medium">Tier</th>
            <th class="px-4 py-3 text-left font-medium">CMS</th>
            <th class="px-4 py-3 text-center font-medium">HTTPS</th>
            <th class="px-4 py-3 text-center font-medium">Mobile</th>
            <th class="px-4 py-3 text-left font-medium">Top Signal</th>
          </tr>
        </thead>
        <tbody id="results-body" class="divide-y divide-slate-100"></tbody>
      </table>
    </div>
  </div>

  <!-- Error banner -->
  <div id="error-banner" class="hidden bg-red-50 border border-red-200 rounded-xl p-4 text-sm text-red-700"></div>

</div>

<footer class="text-center text-xs text-slate-400 py-8">
  Lyvica &mdash; max <span id="cap-note">""" + str(DEMO_CAP) + """</span> domains per run on the demo
</footer>

<script>
  let activeTab = 'paste';
  let allLeads = [];      // all scored leads, unfiltered
  let completed = 0;
  let total = 0;

  function switchTab(tab) {
    activeTab = tab;
    ['paste','csv'].forEach(t => {
      document.getElementById('pane-' + t).classList.toggle('hidden', t !== tab);
      const btn = document.getElementById('tab-' + t);
      if (t === tab) {
        btn.classList.add('border-indigo-600', 'text-indigo-600');
        btn.classList.remove('border-transparent', 'text-slate-500');
      } else {
        btn.classList.remove('border-indigo-600', 'text-indigo-600');
        btn.classList.add('border-transparent', 'text-slate-500');
      }
    });
  }

  function updateCount() {
    const lines = document.getElementById('domains-text').value
      .split('\\n').map(l => l.trim()).filter(l => l.length > 0);
    document.getElementById('domain-count').textContent = lines.length + ' domain' + (lines.length !== 1 ? 's' : '');
  }

  function onFileSelect(input) {
    const name = input.files[0]?.name || 'No file chosen';
    document.getElementById('csv-label').textContent = '\\u2713 ' + name;
  }

  document.getElementById('drop-zone').addEventListener('click', () => {
    document.getElementById('csv-file').click();
  });

  function scoreColor(score) {
    if (score === null || score === undefined) return 'text-slate-400';
    if (score >= 70) return 'text-red-600';
    if (score >= 50) return 'text-amber-600';
    return 'text-slate-500';
  }

  function tierBadge(tier) {
    if (!tier) return '<span class="text-xs text-slate-400">—</span>';
    const map = {
      hot: 'bg-red-100 text-red-700',
      warm: 'bg-amber-100 text-amber-700',
      cold: 'bg-slate-100 text-slate-500',
    };
    const icons = { hot: '🔥', warm: '🌡', cold: '❄️' };
    return `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${map[tier] || ''}">${icons[tier] || ''} ${tier}</span>`;
  }

  function httpsIcon(val) {
    if (val === true) return '<span class="text-emerald-600 font-bold text-base">✓</span>';
    if (val === false) return '<span class="text-red-500 font-bold text-base">✗</span>';
    return '<span class="text-slate-300">—</span>';
  }

  function addRow(lead, rank) {
    const tbody = document.getElementById('results-body');
    const score = lead.rebuild_opportunity_score;
    const pitchAngle = (lead.pitch_angles && lead.pitch_angles.length > 0)
      ? lead.pitch_angles[0]
      : (lead.status === 'disqualified' ? '<em class="text-slate-400">disqualified</em>' : '—');
    const mobileScore = lead.evidence?.pagespeed_mobile;
    const mobileDisplay = mobileScore !== null && mobileScore !== undefined
      ? `<span class="${mobileScore < 50 ? 'text-red-500' : mobileScore < 80 ? 'text-amber-500' : 'text-emerald-600'}">${Math.round(mobileScore)}</span>`
      : '<span class="text-slate-300">—</span>';

    const tr = document.createElement('tr');
    tr.className = 'hover:bg-slate-50 transition-colors fade-in';
    tr.innerHTML = `
      <td class="px-4 py-3 text-slate-400 text-xs font-mono">${rank}</td>
      <td class="px-4 py-3">
        <a href="${lead.url}" target="_blank" rel="noopener"
           class="font-medium text-indigo-600 hover:underline break-all">${lead.domain}</a>
        ${lead.company_name && lead.company_name !== lead.domain.split('.')[0]
          ? `<p class="text-xs text-slate-400 mt-0.5">${lead.company_name}</p>` : ''}
      </td>
      <td class="px-4 py-3 text-center">
        <span class="text-lg font-bold ${scoreColor(score)}">${score !== null && score !== undefined ? score : '—'}</span>
      </td>
      <td class="px-4 py-3 text-center">${tierBadge(lead.tier)}</td>
      <td class="px-4 py-3 text-xs text-slate-600">${lead.evidence?.cms || '<span class="text-slate-300">—</span>'}</td>
      <td class="px-4 py-3 text-center">${httpsIcon(lead.evidence?.https)}</td>
      <td class="px-4 py-3 text-center text-xs">${mobileDisplay}</td>
      <td class="px-4 py-3 text-xs text-slate-600 max-w-xs">${pitchAngle}</td>
    `;
    tbody.appendChild(tr);
  }

  async function startScoring() {
    // Reset state
    allLeads = [];
    completed = 0;
    document.getElementById('results-body').innerHTML = '';
    document.getElementById('error-banner').classList.add('hidden');
    document.getElementById('results-section').classList.add('hidden');
    document.getElementById('stats-section').classList.add('hidden');

    // Build form data
    const formData = new FormData();
    formData.append('min_score', document.getElementById('min-score').value);
    formData.append('concurrency', '6');

    if (activeTab === 'paste') {
      const text = document.getElementById('domains-text').value.trim();
      if (!text) { showError('Please enter at least one domain.'); return; }
      formData.append('domains_text', text);
    } else {
      const file = document.getElementById('csv-file').files[0];
      if (!file) { showError('Please select a CSV file.'); return; }
      formData.append('csv_file', file);
    }

    // UI into loading state
    const btn = document.getElementById('score-btn');
    btn.disabled = true;
    document.getElementById('progress-section').classList.remove('hidden');
    document.getElementById('progress-label').textContent = 'Connecting…';
    document.getElementById('progress-bar').style.width = '0%';
    document.getElementById('progress-sub').textContent = '';

    try {
      const response = await fetch('/api/score', { method: 'POST', body: formData });

      if (!response.ok) {
        const err = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(err.detail || 'Server error');
      }

      // Read SSE stream
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split('\\n\\n');
        buffer = parts.pop();
        for (const part of parts) {
          if (!part.startsWith('data: ')) continue;
          try {
            const event = JSON.parse(part.slice(6));
            handleEvent(event);
          } catch (_) {}
        }
      }
    } catch (err) {
      showError(err.message || 'An error occurred. Please try again.');
    } finally {
      btn.disabled = false;
      document.getElementById('progress-spinner').classList.add('hidden');
    }
  }

  function renderTable() {
    const minScore = parseInt(document.getElementById('min-score').value, 10);
    const filtered = allLeads
      .filter(l => (l.rebuild_opportunity_score ?? 0) >= minScore)
      .sort((a, b) => (b.rebuild_opportunity_score ?? 0) - (a.rebuild_opportunity_score ?? 0));
    const tbody = document.getElementById('results-body');
    tbody.innerHTML = '';
    filtered.forEach((l, i) => addRow(l, i + 1));
    document.getElementById('stat-total').textContent = completed;
    document.getElementById('stat-qualified').textContent = filtered.filter(l => l.status === 'qualified' || l.status === 'needs_review').length;
    document.getElementById('stat-hot').textContent = filtered.filter(l => l.tier === 'hot').length;
    document.getElementById('stat-warm').textContent = filtered.filter(l => l.tier === 'warm').length;
  }

  function handleEvent(event) {
    if (event.type === 'start') {
      total = event.total;
      document.getElementById('progress-label').textContent = `Scoring ${total} domain${total !== 1 ? 's' : ''}…`;
      document.getElementById('results-section').classList.remove('hidden');
      document.getElementById('stats-section').classList.remove('hidden');
    } else if (event.type === 'result') {
      completed++;
      const lead = event.lead;
      const score = lead.rebuild_opportunity_score ?? 0;
      allLeads.push(lead);
      const pct = Math.round((completed / total) * 100);
      document.getElementById('progress-bar').style.width = pct + '%';
      document.getElementById('progress-label').textContent = `Scored ${completed} / ${total}`;
      document.getElementById('progress-sub').textContent = `Last: ${lead.domain} — score ${score}`;
      renderTable();
    } else if (event.type === 'done') {
      document.getElementById('progress-bar').style.width = '100%';
      const minScore = parseInt(document.getElementById('min-score').value, 10);
      const shown = allLeads.filter(l => (l.rebuild_opportunity_score ?? 0) >= minScore).length;
      document.getElementById('progress-label').textContent = `Done — ${shown} of ${completed} domains above threshold`;
      document.getElementById('progress-sub').textContent = '';
    } else if (event.type === 'error') {
      showError(event.message);
    }
  }

  function showError(msg) {
    const banner = document.getElementById('error-banner');
    banner.textContent = msg;
    banner.classList.remove('hidden');
    document.getElementById('progress-section').classList.add('hidden');
  }

  function downloadCSV() {
    if (!allLeads.length) return;
    const headers = ['rank','domain','score','tier','status','cms','https','mobile_score','pitch_angles'];
    const rows = allLeads.map((l, i) => [
      i + 1,
      l.domain,
      l.rebuild_opportunity_score ?? '',
      l.tier ?? '',
      l.status,
      l.evidence?.cms ?? '',
      l.evidence?.https ?? '',
      l.evidence?.pagespeed_mobile ?? '',
      (l.pitch_angles || []).join(' | '),
    ]);
    const csv = [headers, ...rows].map(r => r.map(v => `"${String(v).replace(/"/g,'""')}"`).join(',')).join('\\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'lyvica-results.csv';
    a.click();
  }
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(_HTML)


@app.post("/api/score")
async def score_domains(
    csv_file: Optional[UploadFile] = File(None),
    domains_text: Optional[str] = Form(None),
    min_score: int = Form(50),
    concurrency: int = Form(6),
) -> StreamingResponse:
    """
    Accept domains via CSV upload or pasted text; stream scored leads back as SSE.

    Event types:
      {"type": "start",  "total": N}
      {"type": "result", "lead": {...}}
      {"type": "done"}
      {"type": "error",  "message": "..."}
    """
    domains = await _parse_domains(csv_file, domains_text)
    if not domains:
        return StreamingResponse(
            _error_stream("No domains found. Paste at least one domain or upload a CSV."),
            media_type="text/event-stream",
        )

    domains = domains[:DEMO_CAP]
    job = JobRequest.model_validate({
        "job_id": str(uuid.uuid4()),
        "icp": {"technologies": []},
        "seed_domains": domains,
        "limits": {"max_candidates": DEMO_CAP, "min_score_to_include": min_score},
        "config": {"concurrency": concurrency},
    })

    return StreamingResponse(
        _stream_scoring(job),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


# ---------------------------------------------------------------------------
# Streaming generator
# ---------------------------------------------------------------------------

async def _stream_scoring(job: JobRequest):
    agent = LyvicaAgent()
    sem = asyncio.Semaphore(job.config.concurrency)
    queue: asyncio.Queue = asyncio.Queue()

    yield f"data: {json.dumps({'type': 'start', 'total': len(job.seed_domains)})}\n\n"

    async def _eval(domain: str) -> None:
        async with sem:
            try:
                lead = await asyncio.wait_for(
                    agent._evaluate_domain(domain, job),
                    timeout=45.0,
                )
            except asyncio.TimeoutError:
                lead = agent._disqualified_lead(
                    domain=domain,
                    source="seed",
                    reason="Evaluation timed out",
                )
            except Exception as exc:  # noqa: BLE001
                lead = agent._disqualified_lead(
                    domain=domain,
                    source="seed",
                    reason=str(exc),
                )
            await queue.put(lead)

    tasks = [asyncio.create_task(_eval(d)) for d in job.seed_domains]

    for _ in job.seed_domains:
        lead = await queue.get()
        yield f"data: {json.dumps({'type': 'result', 'lead': lead.model_dump(mode='json')})}\n\n"

    await asyncio.gather(*tasks, return_exceptions=True)
    yield f"data: {json.dumps({'type': 'done'})}\n\n"


async def _error_stream(message: str):
    yield f"data: {json.dumps({'type': 'error', 'message': message})}\n\n"


# ---------------------------------------------------------------------------
# Domain parsing helpers
# ---------------------------------------------------------------------------

_DOMAIN_COLS = ("domain", "url", "website", "site", "homepage", "link")


async def _parse_domains(
    csv_file: Optional[UploadFile],
    domains_text: Optional[str],
) -> list[str]:
    domains: list[str] = []

    if csv_file and csv_file.filename:
        content = await csv_file.read()
        text = content.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames:
            headers_lower = {h.lower(): h for h in reader.fieldnames}
            col = next(
                (headers_lower[c] for c in _DOMAIN_COLS if c in headers_lower),
                reader.fieldnames[0],
            )
            for row in reader:
                raw = row.get(col, "").strip()
                if raw:
                    domains.append(raw)
        else:
            for row in csv.reader(io.StringIO(text)):
                if row and row[0].strip():
                    domains.append(row[0].strip())

    if domains_text:
        for line in domains_text.splitlines():
            line = re.sub(r"[,;\s]+$", "", line).strip()
            if line and not line.startswith("#"):
                domains.append(line)

    # Normalize and deduplicate
    seen: set[str] = set()
    result: list[str] = []
    for raw in domains:
        d = _to_domain(raw)
        if d and d not in seen:
            seen.add(d)
            result.append(d)
    return result
