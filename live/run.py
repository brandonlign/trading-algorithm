"""One tick of the live paper portfolio: refresh prices, rebalance any sleeve whose period just
ended, mark everything to market, scan Polymarket, and rebuild the dashboard. Fake money only.

    .venv/bin/python -m live.run                    # one tick (what the daily GitHub Action runs)
    .venv/bin/python -m live.run --loop 60          # keep ticking every 60 min on an always-on machine
    .venv/bin/python -m live.run --skip-polymarket  # faster, portfolio only
"""
from __future__ import annotations

import argparse
import csv
import json
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from trading.allocations import park_cash
from trading.data import load_crypto_panel, load_stock_panel
from trading.portfolio import run_portfolio

from .dashboard import render
from .sleeves import CRYPTO_SYMBOLS, ETF_TICKERS, SLEEVES

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "live" / "state"
STATE_PATH = STATE_DIR / "portfolio.json"
TRADES_PATH = STATE_DIR / "trades.csv"
POLY_PATH = STATE_DIR / "polymarket.json"
START_CASH = 10_000.0
NY = ZoneInfo("America/New_York")


def with_retries(fn, attempts: int = 3):
    for i in range(attempts):
        try:
            result = fn()
            if len(result):
                return result
        except Exception:
            if i == attempts - 1:
                raise
        time.sleep(5 * (i + 1))
    raise RuntimeError("data source returned nothing")


def market_today(market: str):
    return datetime.now(NY).date() if market == "etf" else datetime.now(timezone.utc).date()


def last_complete_bar(px: pd.DataFrame, market: str) -> pd.Timestamp:
    """Latest bar whose close is final. Today's crypto candle is still open until 00:00 UTC;
    today's ETF bar is final only after the 4pm New York close (plus a buffer)."""
    last = px.index[-1]
    if last.date() < market_today(market):
        return last
    if market == "etf" and datetime.now(NY).time() >= datetime.strptime("16:15", "%H:%M").time():
        return last
    return px.index[-2]


def signal_row(weights: pd.DataFrame, freq: str, market: str, last_bar: pd.Timestamp) -> pd.Timestamp:
    """The newest rebalance date whose period is actually over (month/week fully closed)."""
    today = pd.Timestamp(market_today(market))
    p = "M" if freq == "M" else "W"
    for d in reversed(weights.index):
        finished = (d < last_bar or d.to_period(p) != today.to_period(p)
                    or d.to_period(p) != (d + timedelta(days=1)).to_period(p))
        if finished:
            return d
    return weights.index[0]


def tick_sleeve(sleeve, px_all: pd.DataFrame, state: dict, now: str, trades: list) -> None:
    s = state.setdefault(sleeve.key, {"cash": START_CASH, "units": {}, "started": now,
                                      "last_signal": None, "history": []})
    last_bar = last_complete_bar(px_all, sleeve.market)
    complete = px_all.loc[:last_bar]
    weights = sleeve.weights(complete)
    if sleeve.cash_asset:
        weights = park_cash(weights, sleeve.cash_asset)
    row = signal_row(weights, sleeve.freq, sleeve.market, last_bar)
    target = weights.loc[row].fillna(0.0)
    target = target[target > 1e-9]
    prices = px_all.ffill().iloc[-1]  # trade at the latest price, even if today's bar is still moving

    equity = s["cash"] + sum(u * prices[a] for a, u in s["units"].items())
    if s["last_signal"] != str(row.date()):
        current = {a: s["units"].get(a, 0.0) * prices[a] for a in set(s["units"]) | set(target.index)}
        turnover = sum(abs(target.get(a, 0.0) * equity - v) for a, v in current.items())
        cost = turnover * sleeve.cost_bps / 10_000
        investable = equity - cost
        new_units = {a: target[a] * investable / prices[a] for a in target.index}
        for a in sorted(current):
            delta = new_units.get(a, 0.0) - s["units"].get(a, 0.0)
            if abs(delta * prices[a]) >= 1:
                trades.append([now, sleeve.key, "BUY" if delta > 0 else "SELL", a, round(abs(delta), 8),
                               round(float(prices[a]), 4), round(abs(delta) * float(prices[a]), 2)])
        s["units"] = new_units
        s["cash"] = investable - sum(new_units[a] * prices[a] for a in new_units)
        s["last_signal"] = str(row.date())
        equity = investable

    s["equity"] = equity
    s["holdings"] = {a: round(u * float(prices[a]) / equity, 4) for a, u in s["units"].items() if u > 0}
    s["prices_as_of"] = str(px_all.index[-1].date())
    day = now[:10]
    s["history"] = [h for h in s["history"] if h[0] != day] + [[day, round(equity, 2)]]


def backtest_curves(etf_long: pd.DataFrame, crypto_long: pd.DataFrame) -> dict:
    curves = {}
    for sleeve in SLEEVES:
        px = etf_long if sleeve.market == "etf" else crypto_long
        w = sleeve.weights(px)
        if sleeve.cash_asset:
            w = park_cash(w, sleeve.cash_asset)
        res = run_portfolio(px.loc[sleeve.backtest_start:], w.loc[sleeve.backtest_start:], sleeve.cost_bps)
        weekly = res["equity"].resample("W").last().dropna()
        curves[sleeve.key] = [[str(d.date()), round(float(v), 4)] for d, v in weekly.items()]
    return curves


def scan_polymarket() -> dict:
    import polymarket

    n_events, results = polymarket.scan(include_augmented=False, near=0.02)
    keep = [r for r in results if r["profit"] > 0]
    return {
        "scanned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "events": n_events,
        "profitable": len(keep),
        "clean": sum(1 for r in keep if not r["flags"]),
        "top": [{"title": r["event"]["title"], "slug": r["event"]["slug"], "side": r["side"],
                 "outcomes": len(r["markets"]), "edge_cents": round(r["top_edge"] * 100, 2),
                 "sets": round(r["sets"], 1), "profit": round(r["profit"], 2), "flags": r["flags"]}
                for r in keep[:10]],
    }


def tick(skip_polymarket: bool) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}

    etf = with_retries(lambda: load_stock_panel(ETF_TICKERS, start="2003-01-01", fresh=True))
    crypto = with_retries(lambda: load_crypto_panel(CRYPTO_SYMBOLS, years=8, fresh=True))

    trades: list = []
    for sleeve in SLEEVES:
        tick_sleeve(sleeve, etf if sleeve.market == "etf" else crypto, state, now, trades)
    state["_meta"] = {"updated": now}
    STATE_PATH.write_text(json.dumps(state, indent=1))

    if trades:
        new = not TRADES_PATH.exists()
        with TRADES_PATH.open("a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["time_utc", "sleeve", "action", "asset", "units", "price", "value"])
            w.writerows(trades)

    if not skip_polymarket:
        try:
            POLY_PATH.write_text(json.dumps(scan_polymarket(), indent=1))
        except Exception:
            traceback.print_exc()

    poly = json.loads(POLY_PATH.read_text()) if POLY_PATH.exists() else None
    trade_rows = list(csv.DictReader(TRADES_PATH.open())) if TRADES_PATH.exists() else []
    render(state, SLEEVES, backtest_curves(etf, crypto), trade_rows, poly, ROOT / "docs" / "index.html")

    for sleeve in SLEEVES:
        s = state[sleeve.key]
        print(f"{sleeve.name:<28} ${s['equity']:>10,.2f}  {s['equity'] / START_CASH - 1:+7.2%}  "
              f"{', '.join(f'{a} {w:.0%}' for a, w in s['holdings'].items()) or 'cash'}")
    print(f"{len(trades)} paper trades this tick; dashboard -> docs/index.html")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--loop", type=float, metavar="MINUTES")
    ap.add_argument("--skip-polymarket", action="store_true")
    args = ap.parse_args()
    while True:
        try:
            tick(args.skip_polymarket)
        except Exception:
            if not args.loop:
                raise
            traceback.print_exc()  # keep an always-on loop alive through a flaky data source
        if not args.loop:
            break
        time.sleep(args.loop * 60)


if __name__ == "__main__":
    main()
