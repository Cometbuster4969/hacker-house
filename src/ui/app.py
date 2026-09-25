"""
FastAPI Web Dashboard for Fraud Investigation Agent.
Provides an analyst interface to view cases, evidence, and recommendations.
"""
from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

app = FastAPI(title="Fraud Investigation Agent", version="1.0.0")

# Global state (set by startup)
_cases_dir: str = "cases"
_graph_stats: dict = {}


def create_app(cases_dir: str = "cases") -> FastAPI:
    """Create the FastAPI application."""
    global _cases_dir
    _cases_dir = cases_dir

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return get_dashboard_html()

    @app.get("/api/cases")
    async def list_cases():
        """List all investigated cases."""
        cases_path = Path(_cases_dir)
        if not cases_path.exists():
            return JSONResponse([])

        cases = []
        for f in sorted(cases_path.glob("*.json")):
            try:
                with open(f) as fh:
                    data = json.load(fh)
                    cases.append({
                        "case_id": data.get("case_id", ""),
                        "verdict": data.get("case", {}).get("verdict", ""),
                        "probability": data.get("case", {}).get("fraud_probability", 0),
                        "pattern": data.get("case", {}).get("pattern", ""),
                        "status": data.get("case", {}).get("status", ""),
                        "exposure": data.get("case", {}).get("exposure_usd", 0),
                        "sar_filed": data.get("sar", {}).get("file", False),
                        "tool_calls": data.get("tool_calls", 0),
                        "latency_s": data.get("latency_s", 0),
                    })
            except Exception as e:
                logger.error("Error reading %s: %s", f, e)
        return JSONResponse(cases)

    @app.get("/api/cases/{case_id}")
    async def get_case(case_id: str):
        """Get full details for a specific case."""
        case_path = Path(_cases_dir) / f"{case_id}.json"
        if not case_path.exists():
            raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

        with open(case_path) as f:
            return JSONResponse(json.load(f))

    root = Path(__file__).resolve().parents[2]

    @app.get("/api/traces/{case_id}")
    async def get_trace(case_id: str):
        """Step-by-step agent trace (tool, why, result, probability) for a case."""
        p = root / "traces" / f"{case_id}.json"
        if not p.exists() or "/" in case_id:
            raise HTTPException(status_code=404, detail="no trace")
        return JSONResponse(json.loads(p.read_text()))

    @app.get("/api/benchmark")
    async def benchmark():
        out = {}
        for name in ("backtest", "case_backtest"):
            p = root / "benchmark" / f"{name}.json"
            if p.exists():
                out[name] = json.loads(p.read_text())
        return JSONResponse(out)

    @app.get("/api/monitoring")
    async def monitoring():
        p = root / "monitoring" / "alerts.json"
        return JSONResponse(json.loads(p.read_text()) if p.exists() else {"alerts": []})

    @app.get("/api/graph/stats")
    async def graph_stats():
        """Get graph database statistics."""
        return JSONResponse(_graph_stats)

    return app


def get_dashboard_html() -> str:
    """Generate the dashboard HTML."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Fraud Investigation Agent — TigerGraph</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
            background: #0a0e1a;
            color: #e0e6f0;
            min-height: 100vh;
        }
        .header {
            background: linear-gradient(135deg, #0d1b2a 0%, #1b2838 100%);
            padding: 20px 30px;
            border-bottom: 1px solid #1e3a5f;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .header h1 {
            font-size: 1.5em;
            color: #00d4aa;
            font-weight: 600;
        }
        .header .subtitle {
            color: #7a8ba8;
            font-size: 0.9em;
        }
        .logo { font-size: 1.8em; margin-right: 12px; }
        .stats-bar {
            display: flex;
            gap: 20px;
            padding: 15px 30px;
            background: #0d1520;
            border-bottom: 1px solid #1e3a5f;
        }
        .stat-card {
            background: #141e30;
            padding: 12px 20px;
            border-radius: 8px;
            border: 1px solid #1e3a5f;
            min-width: 120px;
        }
        .stat-card .label { color: #7a8ba8; font-size: 0.8em; text-transform: uppercase; }
        .stat-card .value { font-size: 1.4em; font-weight: 700; color: #00d4aa; }
        .stat-card .value.red { color: #ff4757; }
        .stat-card .value.yellow { color: #ffa502; }
        .stat-card .value.blue { color: #3b82f6; }
        .main { padding: 20px 30px; }
        .section { margin-bottom: 30px; }
        .section h2 {
            font-size: 1.2em;
            color: #7a8ba8;
            margin-bottom: 15px;
            text-transform: uppercase;
            letter-spacing: 1px;
            font-weight: 500;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            background: #141e30;
            border-radius: 8px;
            overflow: hidden;
        }
        th {
            background: #1a2744;
            padding: 12px 16px;
            text-align: left;
            font-weight: 600;
            color: #7a8ba8;
            font-size: 0.85em;
            text-transform: uppercase;
        }
        td {
            padding: 10px 16px;
            border-top: 1px solid #1e2d45;
            font-size: 0.9em;
        }
        tr:hover { background: #1a2744; }
        tr { cursor: pointer; }
        .verdict {
            padding: 3px 10px;
            border-radius: 12px;
            font-size: 0.85em;
            font-weight: 600;
        }
        .verdict.fraud { background: rgba(255,71,87,0.2); color: #ff4757; }
        .verdict.legitimate { background: rgba(0,212,170,0.2); color: #00d4aa; }
        .verdict.uncertain { background: rgba(255,165,2,0.2); color: #ffa502; }
        .badge {
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 0.8em;
            background: rgba(59,130,246,0.2);
            color: #3b82f6;
        }
        .badge.sar { background: rgba(255,71,87,0.2); color: #ff4757; }
        .prob-bar {
            width: 80px;
            height: 6px;
            background: #1e2d45;
            border-radius: 3px;
            display: inline-block;
            vertical-align: middle;
            margin-left: 8px;
        }
        .prob-fill {
            height: 100%;
            border-radius: 3px;
            transition: width 0.3s;
        }

        /* Case Detail Modal */
        .modal-overlay {
            display: none;
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0,0,0,0.7);
            z-index: 100;
            overflow-y: auto;
            padding: 30px;
        }
        .modal-overlay.active { display: block; }
        .modal {
            background: #0d1520;
            border: 1px solid #1e3a5f;
            border-radius: 12px;
            max-width: 900px;
            margin: 0 auto;
            overflow: hidden;
        }
        .modal-header {
            padding: 20px;
            background: #141e30;
            border-bottom: 1px solid #1e3a5f;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .modal-header h3 { color: #00d4aa; }
        .modal-close {
            background: none;
            border: none;
            color: #7a8ba8;
            font-size: 1.5em;
            cursor: pointer;
        }
        .modal-body { padding: 20px; max-height: 70vh; overflow-y: auto; }
        .detail-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 15px;
            margin-bottom: 20px;
        }
        .detail-card {
            background: #141e30;
            padding: 15px;
            border-radius: 8px;
            border: 1px solid #1e2d45;
        }
        .detail-card .label { color: #7a8ba8; font-size: 0.8em; margin-bottom: 5px; }
        .detail-card .value { font-size: 1.1em; font-weight: 600; }
        .evidence-list { list-style: none; }
        .evidence-list li {
            padding: 10px 15px;
            background: #141e30;
            margin-bottom: 8px;
            border-radius: 6px;
            border-left: 3px solid #3b82f6;
            font-size: 0.9em;
        }
        .evidence-list li .source {
            color: #7a8ba8;
            font-size: 0.8em;
            margin-top: 4px;
        }
        .action-list { list-style: none; }
        .action-list li {
            padding: 8px 12px;
            background: #141e30;
            margin-bottom: 6px;
            border-radius: 6px;
            display: flex;
            align-items: center;
            gap: 10px;
            font-size: 0.9em;
        }
        .action-name { font-weight: 600; color: #e0e6f0; }
        .route {
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 0.8em;
            font-weight: 600;
        }
        .route.auto { background: rgba(0,212,170,0.2); color: #00d4aa; }
        .route.l1 { background: rgba(255,165,2,0.2); color: #ffa502; }
        .route.l2 { background: rgba(255,71,87,0.2); color: #ff4757; }
        .sar-narrative {
            background: #141e30;
            padding: 15px;
            border-radius: 8px;
            border-left: 3px solid #ff4757;
            font-size: 0.9em;
            line-height: 1.6;
            margin-top: 10px;
        }
        .tab-bar {
            display: flex;
            gap: 5px;
            margin-bottom: 15px;
            border-bottom: 1px solid #1e3a5f;
            padding-bottom: 5px;
        }
        .tab {
            padding: 8px 16px;
            background: none;
            border: none;
            color: #7a8ba8;
            cursor: pointer;
            border-bottom: 2px solid transparent;
            font-size: 0.9em;
        }
        .tab.active {
            color: #00d4aa;
            border-bottom-color: #00d4aa;
        }
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        .loading {
            text-align: center;
            padding: 40px;
            color: #7a8ba8;
        }
    </style>
</head>
<body>
    <div class="header">
        <div style="display:flex;align-items:center">
            <span class="logo">🕵️</span>
            <div>
                <h1>Fraud Investigation Agent</h1>
                <div class="subtitle">Powered by TigerGraph + AI</div>
            </div>
        </div>
        <div class="subtitle">HHGOA 2026</div>
    </div>

    <div class="stats-bar" id="stats-bar">
        <div class="stat-card">
            <div class="label">Total Cases</div>
            <div class="value" id="stat-total">—</div>
        </div>
        <div class="stat-card">
            <div class="label">Fraud</div>
            <div class="value red" id="stat-fraud">—</div>
        </div>
        <div class="stat-card">
            <div class="label">Legitimate</div>
            <div class="value" id="stat-legit">—</div>
        </div>
        <div class="stat-card">
            <div class="label">Uncertain</div>
            <div class="value yellow" id="stat-uncertain">—</div>
        </div>
        <div class="stat-card">
            <div class="label">SARs Filed</div>
            <div class="value blue" id="stat-sars">—</div>
        </div>
    </div>

    <div class="main">
        <div class="section">
            <h2>📋 Investigation Cases</h2>
            <table id="cases-table">
                <thead>
                    <tr>
                        <th>Case ID</th>
                        <th>Verdict</th>
                        <th>Probability</th>
                        <th>Pattern</th>
                        <th>Exposure</th>
                        <th>SAR</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody id="cases-body">
                    <tr><td colspan="7" class="loading">Loading cases...</td></tr>
                </tbody>
            </table>
            <div id="extras"></div>
        </div>
    </div>

    <!-- Case Detail Modal -->
    <div class="modal-overlay" id="modal-overlay">
        <div class="modal">
            <div class="modal-header">
                <h3 id="modal-title">Case Details</h3>
                <button class="modal-close" onclick="closeModal()">&times;</button>
            </div>
            <div class="modal-body" id="modal-body"></div>
        </div>
    </div>

    <script>
        let allCases = [];

        async function loadCases() {
            try {
                const resp = await fetch('/api/cases');
                allCases = await resp.json();
                renderCases(allCases);
                renderStats(allCases);
            } catch (e) {
                console.error('Error loading cases:', e);
            }
        }

        function renderStats(cases) {
            const total = cases.length;
            const fraud = cases.filter(c => c.verdict === 'fraud').length;
            const legit = cases.filter(c => c.verdict === 'legitimate').length;
            const uncertain = cases.filter(c => c.verdict === 'uncertain').length;
            const sars = cases.filter(c => c.sar_filed).length;

            document.getElementById('stat-total').textContent = total;
            document.getElementById('stat-fraud').textContent = fraud;
            document.getElementById('stat-legit').textContent = legit;
            document.getElementById('stat-uncertain').textContent = uncertain;
            document.getElementById('stat-sars').textContent = sars;
        }

        function renderCases(cases) {
            const tbody = document.getElementById('cases-body');
            if (!cases.length) {
                tbody.innerHTML = '<tr><td colspan="7" class="loading">No cases found. Run the investigation agent first.</td></tr>';
                return;
            }

            tbody.innerHTML = cases.map(c => `
                <tr onclick="openCase('${c.case_id}')">
                    <td><strong>${c.case_id}</strong></td>
                    <td><span class="verdict ${c.verdict}">${c.verdict}</span></td>
                    <td>
                        ${c.probability.toFixed(2)}
                        <div class="prob-bar">
                            <div class="prob-fill" style="width:${c.probability*100}%;background:${
                                c.probability > 0.7 ? '#ff4757' :
                                c.probability > 0.3 ? '#ffa502' : '#00d4aa'
                            }"></div>
                        </div>
                    </td>
                    <td><span class="badge">${c.pattern || 'none'}</span></td>
                    <td>$${c.exposure.toFixed(2)}</td>
                    <td>${c.sar_filed ? '<span class="badge sar">FILED</span>' : '—'}</td>
                    <td>${c.tool_calls} calls</td>
                </tr>
            `).join('');
        }

        async function openCase(caseId) {
            try {
                const resp = await fetch(`/api/cases/${caseId}`);
                const data = await resp.json();
                renderCaseDetail(data);
                document.getElementById('modal-overlay').classList.add('active');
            } catch (e) {
                console.error('Error loading case:', e);
            }
        }

        function closeModal() {
            document.getElementById('modal-overlay').classList.remove('active');
        }

        function renderCaseDetail(data) {
            const c = data.case || {};
            const sar = data.sar || {};
            const nba = data.next_best_actions || {};
            const evidences = c.evidence || [];
            const requests = data.evidence_requests || [];

            document.getElementById('modal-title').textContent = `Case ${data.case_id}`;

            let html = `
                <div class="detail-grid">
                    <div class="detail-card">
                        <div class="label">Verdict</div>
                        <div class="value"><span class="verdict ${c.verdict}">${c.verdict}</span></div>
                    </div>
                    <div class="detail-card">
                        <div class="label">Fraud Probability</div>
                        <div class="value">${(c.fraud_probability || 0).toFixed(2)}</div>
                    </div>
                    <div class="detail-card">
                        <div class="label">Pattern</div>
                        <div class="value">${c.pattern || 'none'}</div>
                    </div>
                    <div class="detail-card">
                        <div class="label">Exposure</div>
                        <div class="value">$${(c.exposure_usd || 0).toFixed(2)}</div>
                    </div>
                    <div class="detail-card">
                        <div class="label">Status</div>
                        <div class="value">${c.status || 'open'}</div>
                    </div>
                    <div class="detail-card">
                        <div class="label">Tool Calls</div>
                        <div class="value">${data.tool_calls || 0} (${data.latency_s || 0}s)</div>
                    </div>
                </div>

                <div class="tab-bar">
                    <button class="tab active" onclick="showTab('summary')">Summary</button>
                    <button class="tab" onclick="showTab('evidence')">Evidence (${evidences.length})</button>
                    <button class="tab" onclick="showTab('actions')">Actions</button>
                    <button class="tab" onclick="showTab('sar')">SAR</button>
                    <button class="tab" onclick="showTab('memory')">Case Memory</button>
                    <button class="tab" onclick="showTab('trace')">Agent Trace</button>
                </div>

                <div id="tab-summary" class="tab-content active">
                    <p style="line-height:1.6;margin-bottom:15px">${c.summary || 'No summary available.'}</p>
                    ${c.pattern_description ? `<p><strong>Pattern Description:</strong> ${c.pattern_description}</p>` : ''}
                    <p style="margin-top:10px"><strong>Stop Reason:</strong> ${data.stop_reason || 'N/A'}</p>
                </div>

                <div id="tab-evidence" class="tab-content">
                    <ul class="evidence-list">
                        ${evidences.map(e => `
                            <li>
                                ${e.claim}
                                <div class="source">Source: ${e.source} | Ref: ${e.ref}</div>
                            </li>
                        `).join('')}
                    </ul>
                    ${requests.length ? `
                        <h4 style="margin:15px 0 10px;color:#7a8ba8">Evidence Requests</h4>
                        ${requests.map(r => `
                            <div class="detail-card" style="margin-bottom:8px">
                                <div class="label">${r.type} (step ${r.asked_after_step})</div>
                                <div class="value" style="font-size:0.9em">${r.assumed_response}</div>
                            </div>
                        `).join('')}
                    ` : ''}
                </div>

                <div id="tab-actions" class="tab-content">
                    <h4 style="margin-bottom:10px;color:#7a8ba8">Initial Actions</h4>
                    <ul class="action-list">
                        ${(nba.initial || []).map(a => `
                            <li>
                                <span class="action-name">${a.action}</span>
                                <span class="route ${(a.route || '').toLowerCase()}">${a.route}</span>
                                <span style="color:#7a8ba8;font-size:0.85em">${a.reason}</span>
                            </li>
                        `).join('')}
                    </ul>
                    <h4 style="margin:15px 0 10px;color:#7a8ba8">Final Actions</h4>
                    <ul class="action-list">
                        ${(nba.final || []).map(a => `
                            <li>
                                <span class="action-name">${a.action}</span>
                                <span class="route ${(a.route || '').toLowerCase()}">${a.route}</span>
                                <span style="color:#7a8ba8;font-size:0.85em">${a.reason}</span>
                            </li>
                        `).join('')}
                    </ul>
                    <p style="margin-top:10px;color:#7a8ba8"><strong>What Changed:</strong> ${nba.what_changed || 'nothing'}</p>
                </div>

                <div id="tab-sar" class="tab-content">
                    ${sar.file ? `
                        <div class="detail-card" style="margin-bottom:10px;border-left:3px solid #ff4757">
                            <div class="label">SAR Filed</div>
                            <div class="value">${sar.reason}</div>
                        </div>
                        <div class="sar-narrative">${sar.narrative}</div>
                        <div class="detail-grid" style="margin-top:10px">
                            <div class="detail-card">
                                <div class="label">Total Amount</div>
                                <div class="value">$${(sar.total_amount_usd || 0).toFixed(2)}</div>
                            </div>
                            <div class="detail-card">
                                <div class="label">Activity Dates</div>
                                <div class="value">${(sar.activity_dates || []).join(' to ')}</div>
                            </div>
                        </div>
                    ` : '<p style="color:#7a8ba8">No SAR required for this case.</p>'}
                </div>

                <div id="tab-memory" class="tab-content">
                    <p><strong>Similar Prior Cases:</strong> ${(c.similar_prior_cases || []).join(', ') || 'None found'}</p>
                    <p style="margin-top:10px"><strong>Written to Graph:</strong> ${c.written_to_graph ? 'Yes' : 'No'}</p>
                    <p style="margin-top:5px"><strong>Graph Case ID:</strong> ${c.graph_case_id || 'N/A'}</p>
                </div>
            `;

            html += `<div id="tab-trace" class="tab-content"><p style="color:#7a8ba8">Loading trace...</p></div>`;
            document.getElementById('modal-body').innerHTML = html;
            fetch(`/api/traces/${data.case_id}`).then(r => r.ok ? r.json() : null).then(t => {
                const el = document.getElementById('tab-trace');
                if (!t) { el.innerHTML = '<p style="color:#7a8ba8">No trace recorded.</p>'; return; }
                el.innerHTML = `<p style="color:#7a8ba8;margin-bottom:8px">as_of ${t.as_of} &middot; model ${t.p_model} &rarr; initial ${t.p_initial} &rarr; final ${t.p_final} &middot; signals: ${(t.signals||[]).join(', ')}</p>` +
                  t.steps.map(s => `<div class="detail-card" style="margin-bottom:6px">
                      <div class="label">${s.step}. ${s.tool}${s.p_fraud !== null ? ` &middot; p=${s.p_fraud.toFixed(2)}` : ''}</div>
                      <div style="font-size:0.85em;color:#7a8ba8">why: ${s.why}</div>
                      <div class="value" style="font-size:0.9em">${s.result}</div>
                      ${s.p_fraud !== null ? `<div class="prob-bar"><div class="prob-fill" style="width:${s.p_fraud*100}%;background:${s.p_fraud>0.7?'#ff4757':s.p_fraud>0.3?'#ffa502':'#00d4aa'}"></div></div>` : ''}
                    </div>`).join('') +
                  `<h4 style="margin:12px 0 6px;color:#7a8ba8">Graph queries (${t.queries.length})</h4>` +
                  t.queries.map(q => `<div style="font-family:monospace;font-size:0.8em;color:#7a8ba8">${q.query}(${Object.entries(q.params).map(([k,v])=>k+'='+v).join(', ')})</div>`).join('');
            });
        }

        async function loadExtras() {
            try {
                const b = await (await fetch('/api/benchmark')).json();
                const m = await (await fetch('/api/monitoring')).json();
                const el = document.getElementById('extras');
                if (!el) return;
                const tb = b.backtest ? b.backtest.transaction_model_sep_oct : null;
                const cb = b.case_backtest || null;
                el.innerHTML = `
                  <h3 style="margin:20px 0 10px">Honest backtest (no leakage)</h3>
                  <div class="detail-grid">
                    ${tb ? `<div class="detail-card"><div class="label">Txn model ROC-AUC (Sep-Oct)</div><div class="value">${tb.model_roc_auc} vs risk score ${tb.risk_score_roc_auc}</div></div>` : ''}
                    ${cb ? `<div class="detail-card"><div class="label">Case verdict accuracy (Oct, 50/50, decided)</div><div class="value">${cb.verdict_accuracy_balanced_on_decided}</div></div>
                    <div class="detail-card"><div class="label">Episode Jaccard</div><div class="value">${cb.episode_mean_jaccard_on_fraud_found}</div></div>
                    <div class="detail-card"><div class="label">Pattern accuracy</div><div class="value">${cb.pattern_accuracy_on_fraud_verdicts}</div></div>
                    <div class="detail-card"><div class="label">Blocks on cleared cases</div><div class="value">${(cb.block_rate_on_cleared*100).toFixed(1)}%</div></div>` : ''}
                  </div>
                  <h3 style="margin:20px 0 10px">Autonomous monitoring (beyond the 20 cases): ${m.alerts.length} alerts</h3>
                  <table><thead><tr><th>Type</th><th>Element</th><th>Window</th><th>Verdict</th><th>Final actions</th></tr></thead><tbody>
                  ${m.alerts.map(a => `<tr><td>${a.type}</td><td style="font-size:0.85em">${a.element}</td><td style="font-size:0.85em">${(a.first||'').slice(0,16)} &rarr; ${(a.last||'').slice(0,16)}</td><td>${a.verdict} (${a.p})</td><td style="font-size:0.8em">${(a.final_actions||[]).join(', ')}</td></tr>`).join('')}
                  </tbody></table>`;
            } catch (e) { console.error(e); }
        }

        function showTab(tabName) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
            event.target.classList.add('active');
            document.getElementById(`tab-${tabName}`).classList.add('active');
        }

        // Click outside modal to close
        document.getElementById('modal-overlay').addEventListener('click', function(e) {
            if (e.target === this) closeModal();
        });

        loadCases();
        loadExtras();
    </script>
</body>
</html>"""
