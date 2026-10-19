"""Render the static dashboard page (docs/index.html) from the live state. GitHub Pages serves it as-is."""
from __future__ import annotations

import html
import json
from pathlib import Path

START_CASH = 10_000.0

# Fixed categorical order (validated palette): each sleeve owns its color everywhere.
COLORS = {
    "etf_momentum": ("#2a78d6", "#3987e5"),
    "etf_rp_trend": ("#eb6834", "#d95926"),
    "btc_trend": ("#1baf7a", "#199e70"),
    "bench_spy": ("#eda100", "#c98500"),
    "bench_btc": ("#e87ba4", "#d55181"),
}

FINDINGS = [
    ("ETF momentum rotation held up best.",
     "2007-2026: +11.9%/yr vs SPY's +11.0%, worst drop -17% vs -55%. Similar results with top 2/3/4 picks, "
     "with or without QQQ, and in both decades."),
    ("Risk parity + trend is the smoothest.",
     "Only +5.3%/yr, but the worst drop was -7%. Think of it as an alternative to holding bonds."),
    ("BTC trend rules cut crashes, not returns.",
     "2019-2026: about the same return as holding, worst drop -46% vs -77%. It lagged plain holding in 2023-26."),
    ("Rotating between altcoins lost.",
     "Momentum across 12 coins: ~15%/yr with -86% drawdowns. Fees and alt-season collapses eat it."),
    ("ML models were coin flips.",
     "Logistic regression, random forest and gradient boosting, retrained yearly: AUC ~0.50 on SPY and BTC. "
     "On BTC every model lost money; a 200-day average beat them all."),
    ("Polymarket longshot bias didn't last.",
     "Buying NO on 2-10c outcomes earned +2.5%/bet in 2023-24 but lost 1-4% on recent markets."),
]


def _money(x: float) -> str:
    return f"${x:,.0f}"


def render(state: dict, sleeves, backtests: dict, trades: list[dict], poly: dict | None, out: Path) -> None:
    updated = state.get("_meta", {}).get("updated", "")
    cards = []
    for s in sleeves:
        st = state[s.key]
        ret = st["equity"] / START_CASH - 1
        hold = "".join(f"<li><span>{html.escape(a)}</span><span>{w:.0%}</span></li>"
                       for a, w in sorted(st["holdings"].items(), key=lambda kv: -kv[1])) or "<li><span>Cash</span><span>100%</span></li>"
        cards.append(f"""
      <article class="card" style="--c-light:{COLORS[s.key][0]};--c-dark:{COLORS[s.key][1]}">
        <header><span class="swatch{' dashed' if s.benchmark else ''}"></span><h3>{html.escape(s.name)}</h3></header>
        <div class="big">{_money(st['equity'])}</div>
        <div class="ret {'up' if ret >= 0 else 'down'}">{'▲' if ret >= 0 else '▼'} {ret:+.2%} since {st['started'][:10]}</div>
        <p class="desc">{html.escape(s.description)}</p>
        <ul class="holdings">{hold}</ul>
        <p class="meta">Rebalances {'monthly' if s.freq == 'M' else 'weekly'} · last signal {st.get('last_signal')}</p>
      </article>""")

    trade_rows = "".join(
        f"<tr><td>{html.escape(t['time_utc'][:16].replace('T', ' '))}</td><td>{html.escape(t['sleeve'])}</td>"
        f"<td>{html.escape(t['action'])}</td><td>{html.escape(t['asset'])}</td><td class='num'>${float(t['value']):,.0f}</td>"
        f"<td class='num'>{float(t['price']):,.2f}</td></tr>"
        for t in reversed(trades[-30:])) or "<tr><td colspan=6>No trades yet.</td></tr>"

    if poly:
        rows = "".join(
            f"<tr><td><a href='https://polymarket.com/event/{html.escape(p['slug'])}'>{html.escape(p['title'])}</a>"
            f"{'<div class=flag>⚠ ' + html.escape(p['flags']) + '</div>' if p['flags'] else ''}</td>"
            f"<td>{html.escape(p['side'])}</td><td class='num'>{p['edge_cents']:.2f}¢</td>"
            f"<td class='num'>{p['sets']:,.0f}</td><td class='num'>${p['profit']:,.2f}</td></tr>"
            for p in poly["top"]) or "<tr><td colspan=5>Nothing profitable after fees right now.</td></tr>"
        poly_html = f"""
      <p class="sub">Scanned {poly['events']:,} open events at {html.escape(poly['scanned_at'][:16].replace('T', ' '))} UTC:
        {poly['profitable']} full sets profitable after fees, <strong>{poly['clean']} without red flags</strong>.
        Read-only, never trades.</p>
      <div class="scroll"><table><thead><tr><th>Event</th><th>Side</th><th class="num">Edge / set</th>
        <th class="num">Sets</th><th class="num">Profit</th></tr></thead><tbody>{rows}</tbody></table></div>"""
    else:
        poly_html = "<p class='sub'>No scan yet.</p>"

    findings = "".join(f"<li><strong>{html.escape(h)}</strong> {html.escape(b)}</li>" for h, b in FINDINGS)

    payload = {
        "series": [{"key": s.key, "name": s.name, "benchmark": s.benchmark, "market": s.market,
                    "live": [[d, v / START_CASH * 100] for d, v in state[s.key]["history"]],
                    "backtest": backtests.get(s.key, [])} for s in sleeves],
        "colors": COLORS,
    }

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(TEMPLATE.replace("{{UPDATED}}", html.escape(updated[:16].replace("T", " ")))
                   .replace("{{CARDS}}", "".join(cards))
                   .replace("{{TRADES}}", trade_rows)
                   .replace("{{POLY}}", poly_html)
                   .replace("{{FINDINGS}}", findings)
                   .replace("{{DATA}}", json.dumps(payload).replace("</", "<\\/")))


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
main { max-width: 1120px; margin: 0 auto; padding: 32px 16px 64px; }
h1 { font-size: 28px; margin: 0 0 4px; letter-spacing: -0.01em; }
h2 { font-size: 18px; margin: 40px 0 8px; }
h3 { font-size: 15px; margin: 0; }
.sub { color: var(--text-secondary); margin: 0 0 16px; }
.banner { background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px;
  padding: 10px 14px; color: var(--text-secondary); font-size: 14px; margin: 16px 0 0; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; margin-top: 20px; }
.card { background: var(--surface-1); border: 1px solid var(--border); border-radius: 12px; padding: 16px;
  --c: var(--c-light); }
@media (prefers-color-scheme: dark) { :root:where(:not([data-theme="light"])) .card { --c: var(--c-dark); } }
:root[data-theme="dark"] .card { --c: var(--c-dark); }
.card header { display: flex; align-items: center; gap: 8px; }
.swatch { width: 14px; height: 3px; border-radius: 2px; background: var(--c); flex: none; }
.swatch.dashed { background: repeating-linear-gradient(90deg, var(--c) 0 4px, transparent 4px 7px); }
.big { font-size: 28px; font-weight: 650; margin-top: 10px; font-variant-numeric: tabular-nums; }
.ret { font-size: 14px; font-variant-numeric: tabular-nums; }
.ret.up { color: var(--up); } .ret.down { color: var(--down); }
.desc { color: var(--text-secondary); font-size: 13px; margin: 10px 0; }
.meta { color: var(--text-muted); font-size: 12px; margin: 8px 0 0; }
.holdings { list-style: none; padding: 0; margin: 0; font-size: 13px; font-variant-numeric: tabular-nums; }
.holdings li { display: flex; justify-content: space-between; border-top: 1px solid var(--border); padding: 4px 0; }
.panel { background: var(--surface-1); border: 1px solid var(--border); border-radius: 12px; padding: 16px; }
.chart { position: relative; height: 320px; }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
@media (max-width: 760px) { .two { grid-template-columns: 1fr; } }
.scroll { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { text-align: left; color: var(--text-secondary); font-weight: 600; border-bottom: 1px solid var(--border); padding: 6px 8px; }
td { border-bottom: 1px solid var(--border); padding: 6px 8px; vertical-align: top; }
.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
a { color: inherit; }
.flag { color: var(--text-muted); font-size: 12px; }
.findings { padding-left: 18px; } .findings li { margin-bottom: 8px; color: var(--text-secondary); }
.findings strong { color: var(--text-primary); }
footer { color: var(--text-muted); font-size: 12px; margin-top: 40px; }
</style>
</head>
<body>
<main>
  <h1>Paper Trading Desk</h1>
  <p class="sub">Updated {{UPDATED}} UTC · every sleeve started with $10,000 of fake money</p>
  <div class="banner">Simulated trades at real prices with fees. No real money is involved. Not financial advice.</div>

  <div class="grid">{{CARDS}}</div>

  <h2>Live paper performance</h2>
  <p class="sub">Value of each $10,000 sleeve, indexed to 100 at its start.</p>
  <div class="panel"><div class="chart"><canvas id="live" aria-label="Live paper performance chart"></canvas></div></div>

  <h2>Backtests</h2>
  <p class="sub">Same rules run on history with fees. Growth of $1, log scale. Backtests are optimistic; the live chart is the real test.</p>
  <div class="two">
    <div class="panel"><h3>ETFs, 2007 to today</h3><div class="chart"><canvas id="bt-etf" aria-label="ETF backtest chart"></canvas></div></div>
    <div class="panel"><h3>Bitcoin, 2019 to today</h3><div class="chart"><canvas id="bt-crypto" aria-label="Crypto backtest chart"></canvas></div></div>
  </div>

  <h2>Polymarket arbitrage scanner</h2>
  <div class="panel">{{POLY}}</div>

  <h2>Recent paper trades</h2>
  <div class="panel scroll"><table><thead><tr><th>Time (UTC)</th><th>Sleeve</th><th>Action</th><th>Asset</th>
    <th class="num">Value</th><th class="num">Price</th></tr></thead><tbody>{{TRADES}}</tbody></table></div>

  <h2>What the research found</h2>
  <div class="panel"><ul class="findings">{{FINDINGS}}</ul></div>
  <footer>Generated by live/run.py</footer>
</main>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script>
const DATA = {{DATA}};
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const dark = () => css('--surface-1').toLowerCase() === '#1a1a19';
const charts = [];

function build(id, pick, filter, opts) {
  const series = DATA.series.filter(filter);
  const datasets = series.map(s => ({
    label: s.name,
    data: pick(s).map(([x, y]) => ({ x, y })),
    borderColor: DATA.colors[s.key][dark() ? 1 : 0],
    backgroundColor: DATA.colors[s.key][dark() ? 1 : 0],
    borderWidth: 2, borderDash: s.benchmark ? [5, 4] : [],
    pointRadius: pick(s).length < 3 ? 4 : 0, pointHoverRadius: 5, tension: 0,
  }));
  const grid = css('--grid'), ink = css('--text-secondary');
  const c = new Chart(document.getElementById(id), {
    type: 'line',
    data: { datasets },
    options: {
      maintainAspectRatio: false, animation: false,
      interaction: { mode: 'index', intersect: false },
      scales: {
        x: { type: 'category', labels: [...new Set(datasets.flatMap(d => d.data.map(p => p.x)))].sort(),
             ticks: { color: ink, maxTicksLimit: 7, maxRotation: 0 }, grid: { display: false } },
        y: { type: opts.log ? 'logarithmic' : 'linear', ticks: { color: ink, maxTicksLimit: 6,
             callback: v => opts.log ? '$' + Number(v).toFixed(v < 10 ? 1 : 0) : v },
             grid: { color: grid }, border: { display: false } },
      },
      plugins: {
        legend: { position: 'bottom', labels: { color: ink, boxWidth: 14, boxHeight: 2 } },
        tooltip: { callbacks: { label: ctx => `${ctx.dataset.label}: ${opts.log ? '$' + ctx.parsed.y.toFixed(2) : ctx.parsed.y.toFixed(2)}` } },
      },
    },
  });
  charts.push(c);
}

function drawAll() {
  charts.splice(0).forEach(c => c.destroy());
  build('live', s => s.live, () => true, {});
  build('bt-etf', s => s.backtest, s => s.market === 'etf', { log: true });
  build('bt-crypto', s => s.backtest, s => s.market === 'crypto', { log: true });
}
if (window.Chart) { drawAll(); matchMedia('(prefers-color-scheme: dark)').addEventListener('change', drawAll); }
</script>
</body>
</html>
"""
