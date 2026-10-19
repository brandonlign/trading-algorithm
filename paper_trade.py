"""Forward-test a strategy with fake money on live prices. Never places a real order.

Each run computes the strategy's signal from the last *completed* daily bar, then rebalances a
simulated portfolio at the current price (paying the same fees/slippage as the backtest).
State lives in paper/<name>.json and every fill is appended to paper/trades.csv.

    .venv/bin/python paper_trade.py crypto BTC/USD sma_cross --param fast=20 --param slow=200
    .venv/bin/python paper_trade.py stocks SPY momentum --param lookback=20
    .venv/bin/python paper_trade.py crypto ETH/USD trend_vol_filter --loop 60   # re-check every 60 min
    .venv/bin/python paper_trade.py --status                                    # all paper portfolios
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from trading.data import load_crypto, load_stock
from trading.strategies import STRATEGIES

PAPER_DIR = Path(__file__).resolve().parent / "paper"
MIN_TRADE_FRACTION = 0.01  # skip rebalances smaller than 1% of equity
COSTS_BPS = {"stocks": 3.0, "crypto": 45.0}


def completed_bars(market: str, close):
    """Drop today's still-moving bar so the signal only uses finished days."""
    if market == "crypto":
        today = datetime.now(timezone.utc).date()
    else:
        now = datetime.now(ZoneInfo("America/New_York"))
        today = now.date() if now.hour < 16 or (now.hour == 16 and now.minute < 15) else None
    return close[close.index.date != today] if today else close


def parse_params(pairs: list[str]) -> dict:
    params = {}
    for pair in pairs:
        key, value = pair.split("=", 1)
        params[key] = float(value) if "." in value else int(value)
    return params


def step(args, params: dict):
    name = f"{args.market}_{args.symbol.replace('/', '-')}_{args.strategy}"
    state_path = PAPER_DIR / f"{name}.json"
    PAPER_DIR.mkdir(exist_ok=True)
    state = (json.loads(state_path.read_text()) if state_path.exists()
             else {"cash": args.cash, "units": 0.0, "start_cash": args.cash, "params": params,
                   "started": datetime.now(timezone.utc).isoformat(timespec="seconds")})

    close = (load_crypto(args.symbol, args.exchange, years=2, fresh=True) if args.market == "crypto"
             else load_stock(args.symbol, years=2, fresh=True))
    price = float(close.iloc[-1])
    bars = completed_bars(args.market, close)
    target = float(STRATEGIES[args.strategy]["fn"](bars, **state["params"]).iloc[-1])

    equity = state["cash"] + state["units"] * price
    trade_value = target * equity - state["units"] * price
    cost_rate = COSTS_BPS[args.market] / 10_000
    action = "HOLD"
    if abs(trade_value) > MIN_TRADE_FRACTION * equity:
        if trade_value > 0:  # buy, with fees coming out of the cash we spend
            spend = min(trade_value, state["cash"])
            units = spend * (1 - cost_rate) / price
            state["cash"] -= spend
            state["units"] += units
            action = "BUY"
        else:
            units = min(-trade_value / price, state["units"])
            state["units"] -= units
            state["cash"] += units * price * (1 - cost_rate)
            action = "SELL"
        with (PAPER_DIR / "trades.csv").open("a", newline="") as f:
            w = csv.writer(f)
            if f.tell() == 0:
                w.writerow(["time_utc", "portfolio", "action", "units", "price"])
            w.writerow([datetime.now(timezone.utc).isoformat(timespec="seconds"), name, action,
                        round(units, 8), price])

    state["last_check"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    state_path.write_text(json.dumps(state, indent=2))
    equity = state["cash"] + state["units"] * price
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] {name}: signal={'IN' if target else 'OUT'} "
          f"(last full bar {bars.index[-1].date()}), price={price:,.2f}, action={action}, "
          f"equity=${equity:,.2f} ({equity / state['start_cash'] - 1:+.2%})")


def status():
    files = sorted(PAPER_DIR.glob("*.json")) if PAPER_DIR.exists() else []
    if not files:
        print("No paper portfolios yet.")
    for f in files:
        s = json.loads(f.read_text())
        print(f"{f.stem}: cash=${s['cash']:,.2f} units={s['units']:.6f} params={s['params']} "
              f"started={s['started']} last_check={s.get('last_check')}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("market", nargs="?", choices=["stocks", "crypto"])
    p.add_argument("symbol", nargs="?")
    p.add_argument("strategy", nargs="?", choices=list(STRATEGIES))
    p.add_argument("--param", action="append", default=[], help="strategy parameter, e.g. fast=20")
    p.add_argument("--cash", type=float, default=10_000, help="starting fake cash (first run only)")
    p.add_argument("--exchange", default="coinbase")
    p.add_argument("--loop", type=float, metavar="MINUTES", help="keep running, re-checking at this interval")
    p.add_argument("--status", action="store_true")
    args = p.parse_args()

    if args.status:
        return status()
    if not (args.market and args.symbol and args.strategy):
        p.error("market, symbol and strategy are required (or use --status)")
    params = parse_params(args.param)
    while True:
        step(args, params)
        if not args.loop:
            break
        time.sleep(args.loop * 60)


if __name__ == "__main__":
    main()
