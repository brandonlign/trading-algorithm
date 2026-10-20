"""Render the static dashboard (docs/index.html): the live paper portfolio only, as one chart
(each sleeve's value since paper trading began) and one table. GitHub Pages serves it as-is."""
from __future__ import annotations

import html
import json
from pathlib import Path

START_CASH = 10_000.0

# Fixed categorical order (validated palette): each sleeve owns its color. (light, dark)
COLORS = {
    "etf_momentum": ("#2a78d6", "#3987e5"),
    "etf_rp_trend": ("#eb6834", "#d95926"),
    "btc_trend": ("#1baf7a", "#199e70"),
    "funding_carry": ("#6250d6", "#9085e9"),
    "bench_spy": ("#eda100", "#c98500"),
    "bench_btc": ("#e87ba4", "#d55181"),
}


def render(state: dict, sleeves, out: Path) -> None:
    updated = state.get("_meta", {}).get("updated", "")
    live_start = min(state[s.key]["started"][:10] for s in sleeves)
    rows, series = [], []
    for s in sleeves:
        st = state[s.key]
        ret = st["equity"] / START_CASH - 1
        holdings = ", ".join(f"{html.escape(a)} {w:.0%}" for a, w in
                             sorted(st["holdings"].items(), key=lambda kv: -kv[1])) or "Cash"
        rows.append(f"""
        <tr class="{'bench' if s.benchmark else ''}">
          <td><div class="name"><span class="swatch{' dashed' if s.benchmark else ''}"
               style="--c-light:{COLORS[s.key][0]};--c-dark:{COLORS[s.key][1]}"></span>{html.escape(s.name)}</div>
              <div class="rule">{html.escape(s.rule)}</div>
              <div class="rule">Holding now: {holdings}</div></td>
          <td class="num">${st['equity']:,.0f}</td>
          <td class="num {'up' if ret >= 0 else 'down'}">{ret:+.2%}</td>
        </tr>""")
        series.append({"key": s.key, "name": s.name, "benchmark": s.benchmark,
                       "points": st["history"]})

    payload = {"series": series, "colors": COLORS}
    page = (TEMPLATE.replace("{{UPDATED}}", html.escape(updated[:16].replace("T", " ")))
            .replace("{{LIVE_START}}", live_start)
            .replace("{{ROWS}}", "".join(rows))
            .replace("{{DATA}}", json.dumps(payload).replace("</", "<\\/")))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page)


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Paper Trading Desk</title>
<style>
:root {
  color-scheme: light;
  --surface-0: #f4f4f2; --surface-1: #fcfcfb; --border: #e3e2de;
  --text-primary: #0b0b0b; --text-secondary: #52514e; --text-muted: #7a7974;
  --grid: #ecebe7; --up: #008300; --down: #c4302f;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --surface-0: #111110; --surface-1: #1a1a19; --border: #2e2e2c;
    --text-primary: #f0efec; --text-secondary: #c3c2b7; --text-muted: #8f8e86;
    --grid: #262624; --up: #3fb950; --down: #e66767;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --surface-0: #111110; --surface-1: #1a1a19; --border: #2e2e2c;
  --text-primary: #f0efec; --text-secondary: #c3c2b7; --text-muted: #8f8e86;
  --grid: #262624; --up: #3fb950; --down: #e66767;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--surface-0); color: var(--text-primary);
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
main { max-width: 1080px; margin: 0 auto; padding: 32px 16px 48px; }
h1 { font-size: 26px; margin: 0 0 4px; letter-spacing: -0.01em; }
.sub { color: var(--text-secondary); margin: 0 0 20px; font-size: 14px; }
.panel { background: var(--surface-1); border: 1px solid var(--border); border-radius: 12px; padding: 16px; }
.chart { position: relative; height: 440px; }
@media (max-width: 600px) { .chart { height: 320px; } }
.scroll { overflow-x: auto; margin-top: 16px; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th { text-align: left; color: var(--text-secondary); font-weight: 600; font-size: 12px;
  border-bottom: 1px solid var(--border); padding: 8px; vertical-align: bottom; }
td { border-bottom: 1px solid var(--border); padding: 10px 6px; vertical-align: top; }
tr:last-child td { border-bottom: none; }
.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.name { display: flex; align-items: center; gap: 8px; font-weight: 600; white-space: nowrap; }
.rule { color: var(--text-muted); font-size: 12px; margin-left: 22px; }
td:first-child { min-width: 200px; }
.num { padding-left: 4px; }
.bench .name { font-weight: 500; }
.up { color: var(--up); } .down { color: var(--down); }
.swatch { width: 14px; height: 3px; border-radius: 2px; flex: none; background: var(--c-light); }
.swatch.dashed { background: repeating-linear-gradient(90deg, var(--c-light) 0 4px, transparent 4px 7px); }
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) .swatch { background: var(--c-dark); }
  :root:where(:not([data-theme="light"])) .swatch.dashed { background: repeating-linear-gradient(90deg, var(--c-dark) 0 4px, transparent 4px 7px); }
}
:root[data-theme="dark"] .swatch { background: var(--c-dark); }
:root[data-theme="dark"] .swatch.dashed { background: repeating-linear-gradient(90deg, var(--c-dark) 0 4px, transparent 4px 7px); }
footer { color: var(--text-muted); font-size: 12px; margin-top: 16px; }
</style>
</head>
<body>
<main>
  <h1>Paper Trading Desk</h1>
  <p class="sub">Each strategy started with $10,000 of fake money on {{LIVE_START}}. Values after fees,
    updated daily after the US close. Last update {{UPDATED}} UTC.</p>

  <div class="panel">
    <div class="chart"><canvas id="chart" role="img" aria-label="Paper portfolio value for each strategy and benchmark"></canvas></div>
    <div class="scroll">
      <table>
        <thead>
          <tr><th>Strategy</th><th class="num">Value</th><th class="num">Return</th></tr>
        </thead>
        <tbody>{{ROWS}}</tbody>
      </table>
    </div>
  </div>
  <footer>Simulated trades at real prices. Not financial advice.</footer>
</main>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script>
const DATA = {{DATA}};
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const isDark = () => css('--surface-1').toLowerCase() === '#1a1a19';
const fmtDay = (t) => new Date(t).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
const fmtDate = (t) => new Date(t).toISOString().slice(0, 10);
let chart;

function draw() {
  if (chart) chart.destroy();
  const ink = css('--text-secondary');
  const few = Math.max(...DATA.series.map(s => s.points.length)) < 15;
  chart = new Chart(document.getElementById('chart'), {
    type: 'line',
    data: { datasets: DATA.series.map(s => {
      const c = DATA.colors[s.key][isDark() ? 1 : 0];
      return { label: s.name, data: s.points.map(([d, v]) => ({ x: Date.parse(d), y: v })),
               borderColor: c, backgroundColor: c, borderWidth: 2, borderDash: s.benchmark ? [6, 4] : [],
               pointRadius: few ? 3 : 0, pointHoverRadius: 4, pointHitRadius: 6, tension: 0 };
    }) },
    options: {
      maintainAspectRatio: false, animation: false,
      interaction: { mode: 'nearest', axis: 'x', intersect: false },
      scales: {
        x: { type: 'linear', grid: { display: false }, offset: true,
             ticks: { color: ink, maxRotation: 0, maxTicksLimit: 8, callback: v => fmtDay(v) },
             afterBuildTicks: a => { if (few) a.ticks = [...new Set(DATA.series.flatMap(s => s.points.map(p => Date.parse(p[0]))))].map(value => ({ value })); } },
        y: { grid: { color: css('--grid') }, border: { display: false }, grace: '2%',
             ticks: { color: ink, maxTicksLimit: 6, callback: v => '$' + Math.round(v).toLocaleString() } },
      },
      plugins: {
        legend: { position: 'bottom', labels: { color: ink, boxWidth: 14, boxHeight: 2, padding: 16 } },
        tooltip: { itemSort: (a, b) => b.parsed.y - a.parsed.y,
                   callbacks: { title: items => fmtDate(items[0].parsed.x),
                                label: i => ` ${i.dataset.label}: $${Math.round(i.parsed.y).toLocaleString()}` } },
      },
    },
  });
}
if (window.Chart) { draw(); matchMedia('(prefers-color-scheme: dark)').addEventListener('change', draw); }
</script>
</body>
</html>
"""
