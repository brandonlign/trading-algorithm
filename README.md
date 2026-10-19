# trading-algorithm

Strategy research plus a **live paper portfolio** (fake money, real prices) that runs itself
every day and publishes a dashboard. Free public data, no API keys. **Nothing here places real orders.**

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## Live paper portfolio

```bash
.venv/bin/python -m live.run                   # one tick: rebalance if due, mark to market, scan Polymarket
.venv/bin/python -m live.run --loop 60         # keep running on an always-on machine
open docs/index.html                           # the dashboard
```

Five sleeves, $10,000 of fake money each (defined in `live/sleeves.py`):

| Sleeve | Rule | Rebalances |
| --- | --- | --- |
| ETF Momentum Rotation | Hold the 3 strongest of 9 asset-class ETFs; weak picks go to bonds | monthly |
| ETF Risk Parity + Trend | Equal risk across 8 asset classes, skip anything below its 200-day average | monthly |
| BTC Trend Ensemble | 1/3 BTC per 50/100/200-day average the price is above | weekly |
| SPY buy & hold | benchmark | — |
| BTC buy & hold | benchmark | — |

Signals use only completed daily bars. Trades fill at the latest price with fees
(5 bps ETFs, 40 bps crypto). State lives in `live/state/`, and every fill is in `live/state/trades.csv`.

### Hosting it for free (GitHub Actions + Pages)

`.github/workflows/live.yml` runs a tick every day at 21:30 UTC, after the US close, and
commits the new state and `docs/index.html` back to the repo.

1. Push this folder to a GitHub repo.
2. Settings → Pages → Deploy from branch → `main`, folder `/docs`.
3. Actions tab → "live paper portfolio" → Run workflow, to do the first tick right away.

Public repos get unlimited Actions minutes. A private repo uses about 2 min/day of the free 2,000/month.

## Research (what works and what doesn't)

```bash
.venv/bin/python -m research.etf_strategies          # 9 ETF strategies, 2007-2026, + sensitivity
.venv/bin/python -m research.crypto_strategies       # BTC/alt trend & momentum, 2019-2026
.venv/bin/python -m research.ml_test                 # do ML models help? (walk-forward)
.venv/bin/python -m research.polymarket_calibration --summary   # longshot bias, old vs recent
```

Findings, all out of sample and after costs:

- **ETF momentum rotation** was the best overall: +11.9%/yr vs SPY's +11.0%, with a worst drop of
  −17% vs −55%. It held up across top 2/3/4 picks, without QQQ, and in both decades.
- **Risk parity + trend** was the smoothest: +5.3%/yr, worst drop −7%.
- **BTC trend rules** returned about the same as holding with much smaller crashes (−46% vs −77%),
  but lagged holding in 2023–26.
- **Altcoin momentum rotation** failed: about 15%/yr with −86% drawdowns.
- **ML models** (logistic regression, random forest, gradient boosting) scored AUC ≈ 0.50, a coin flip. On BTC
  every one lost money, while a plain 200-day average rule beat them.
- **Polymarket longshot bias** (buying NO at 2–10¢) earned +2.5%/bet in 2023–24 but lost on recent markets.

## Single-asset tools

| Script | What it does |
| --- | --- |
| `stocks.py` / `crypto.py` | Train/test backtest of 5 simple single-asset strategies |
| `polymarket.py` | Scans neg-risk events for full sets priced below their payout, after fees (`--watch` logs hits) |
| `paper_trade.py` | Forward-tests one single-asset strategy with fake money |

**About Polymarket red flags:** a big "arbitrage" edge usually means the market thinks *no* listed
outcome may win (cancelled election, no IPO), and then every share pays $0.

Not financial advice. Backtests flatter strategies, so the live paper record is the real test.
