"""One tick of the live paper portfolio: refresh prices, rebalance any sleeve whose period just
ended, mark everything to market, and rebuild the dashboard. Fake money only.

    .venv/bin/python -m live.run             # one tick (what the daily GitHub Action runs)
    .venv/bin/python -m live.run --loop 60   # keep ticking every 60 min on an always-on machine
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

from .dashboard import render
from .sleeves import CRYPTO_SYMBOLS, ETF_TICKERS, SLEEVES

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "live" / "state"
STATE_PATH = STATE_DIR / "portfolio.json"
TRADES_PATH = STATE_DIR / "trades.csv"
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


def tick() -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}

    start = (datetime.now(timezone.utc) - timedelta(days=3 * 365)).strftime("%Y-%m-%d")  # enough for 1y lookbacks
    etf = with_retries(lambda: load_stock_panel(ETF_TICKERS, start=start, fresh=True))
    crypto = with_retries(lambda: load_crypto_panel(CRYPTO_SYMBOLS, years=2, fresh=True))

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

    render(state, SLEEVES, ROOT / "docs" / "index.html")

    for sleeve in SLEEVES:
        s = state[sleeve.key]
        print(f"{sleeve.name:<28} ${s['equity']:>10,.2f}  {s['equity'] / START_CASH - 1:+7.2%}  "
              f"{', '.join(f'{a} {w:.0%}' for a, w in s['holdings'].items()) or 'cash'}")
    print(f"{len(trades)} paper trades this tick; dashboard -> docs/index.html")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--loop", type=float, metavar="MINUTES")
    args = ap.parse_args()
    while True:
        try:
            tick()
        except Exception:
            if not args.loop:
                raise
            traceback.print_exc()  # keep an always-on loop alive through a flaky data source
        if not args.loop:
            break
        time.sleep(args.loop * 60)


if __name__ == "__main__":
    main()
