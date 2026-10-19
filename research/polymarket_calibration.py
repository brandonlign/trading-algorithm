"""Are Polymarket prices calibrated? The "favorite-longshot bias" test on resolved markets.

For each resolved yes/no market, look up the YES price N days before it closed and compare
it with what actually happened. If 5¢ longshots win less than 5% of the time, buying NO on
them is a positive-expectation trade (and vice versa).

    .venv/bin/python -m research.polymarket_calibration --markets 3000
"""
from __future__ import annotations

import argparse
import json
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", message=".*OpenSSL.*")
import requests  # noqa: E402

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
BUCKETS = [0, 0.02, 0.05, 0.10, 0.20, 0.35, 0.50, 0.65, 0.80, 0.90, 0.95, 0.98, 1.0]
DAYS_BEFORE = [1, 7, 30]
FEE_RATE = 0.05  # typical taker rate today; fee/share = rate * p * (1 - p)

session = requests.Session()


def resolved_markets(n: int, min_volume: float, recent: bool) -> list[dict]:
    out, cursor = [], None
    while len(out) < n:
        params = {"closed": "true", "limit": 100, "volume_num_min": min_volume}
        if recent:
            params.update(order="closedTime", ascending="false")
        if cursor:
            params["after_cursor"] = cursor
        page = session.get(f"{GAMMA}/markets/keyset", params=params, timeout=30).json()
        for m in page.get("markets") or []:
            try:
                prices = [float(x) for x in json.loads(m.get("outcomePrices") or "[]")]
                outcomes = json.loads(m.get("outcomes") or "[]")
            except (TypeError, ValueError):
                continue
            if (outcomes == ["Yes", "No"] and sorted(prices) == [0.0, 1.0] and m.get("closedTime")
                    and m.get("clobTokenIds") and (m.get("volumeNum") or 0) >= min_volume):
                out.append(m)
        cursor = page.get("next_cursor")
        if not cursor or not page.get("markets"):
            break
    return out[:n]


def history_row(m: dict) -> dict | None:
    token = json.loads(m["clobTokenIds"])[0]
    try:
        hist = session.get(f"{CLOB}/prices-history",
                           params={"market": token, "interval": "max", "fidelity": 720}, timeout=30).json()
    except (requests.RequestException, ValueError):
        return None
    points = hist.get("history") or []
    if len(points) < 3:
        return None
    ts = np.array([p["t"] for p in points])
    ps = np.array([p["p"] for p in points])
    closed = pd.Timestamp(m["closedTime"].replace("+00", "+00:00")).timestamp()
    row = {"id": m["id"], "event": (m.get("events") or [{}])[0].get("id"), "question": m["question"],
           "volume": m.get("volumeNum"), "closed": m["closedTime"][:10],
           "yes_won": int(float(json.loads(m["outcomePrices"])[0]) == 1.0)}
    for d in DAYS_BEFORE:
        i = np.searchsorted(ts, closed - d * 86400, side="right") - 1
        row[f"p_{d}d"] = float(ps[i]) if i >= 0 else np.nan
    return row


def collect(n: int, min_volume: float, refresh: bool, recent: bool) -> pd.DataFrame:
    cache = DATA_DIR / f"polymarket_resolved_{'recent' if recent else 'oldest'}.csv"
    if cache.exists() and not refresh:
        df = pd.read_csv(cache)
        if len(df) >= n * 0.8:
            return df
    markets = resolved_markets(n, min_volume, recent)
    print(f"fetching price history for {len(markets)} resolved markets...")
    with ThreadPoolExecutor(8) as pool:
        rows = [r for r in pool.map(history_row, markets) if r]
    df = pd.DataFrame(rows)
    DATA_DIR.mkdir(exist_ok=True)
    df.to_csv(cache, index=False)
    return df


def calibration(df: pd.DataFrame, col: str) -> pd.DataFrame:
    d = df.dropna(subset=[col])
    d = d[(d[col] > 0) & (d[col] < 1)]
    d["bucket"] = pd.cut(d[col], BUCKETS)
    rows = []
    for bucket, g in d.groupby("bucket", observed=True):
        p, won = g[col].mean(), g["yes_won"].mean()
        se = np.sqrt(won * (1 - won) / len(g)) if len(g) > 1 else np.nan
        fee_yes = FEE_RATE * g[col] * (1 - g[col])
        # return per $1 staked, averaged across markets, after taker fee
        ret_yes = (g["yes_won"] / (g[col] + fee_yes) - 1).mean()
        ret_no = ((1 - g["yes_won"]) / (1 - g[col] + fee_yes) - 1).mean()
        rows.append({"price": str(bucket), "markets": len(g), "events": g["event"].nunique(),
                     "avg_price": p, "yes_rate": won, "±2se": 2 * se,
                     "edge": won - p, "buy_yes_ret": ret_yes, "buy_no_ret": ret_no})
    return pd.DataFrame(rows)


def fade_longshots(df: pd.DataFrame, col: str, lo: float = 0.02, hi: float = 0.10) -> dict:
    """The candidate strategy: buy NO whenever YES trades between lo and hi."""
    g = df[(df[col] > lo) & (df[col] <= hi)]
    fee = FEE_RATE * g[col] * (1 - g[col])
    rets = (1 - g["yes_won"]) / (1 - g[col] + fee) - 1
    per_event = rets.groupby(g["event"]).mean()  # one vote per event, so 30-candidate races don't dominate
    t = per_event.mean() / per_event.std() * np.sqrt(len(per_event)) if len(per_event) > 1 else np.nan
    return {"bets": len(g), "events": len(per_event), "no_win_rate": 1 - g["yes_won"].mean(),
            "avg_ret": rets.mean(), "avg_ret_per_event": per_event.mean(), "t_stat": t}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", type=int, default=3000)
    ap.add_argument("--min-volume", type=float, default=10_000)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--recent", action="store_true", help="most recently closed markets instead of the oldest")
    ap.add_argument("--summary", action="store_true", help="only compare the longshot-fade strategy across samples")
    args = ap.parse_args()

    if args.summary:
        print("Buy NO when YES is 2-10c (after taker fees), oldest sample vs most recent sample:")
        for recent in (False, True):
            df = collect(args.markets, args.min_volume, args.refresh, recent)
            for d in DAYS_BEFORE:
                r = fade_longshots(df, f"p_{d}d")
                print(f"  {'recent' if recent else 'oldest'} ({df.closed.min()} -> {df.closed.max()}) {d:>2}d before: "
                      f"{r['bets']:>4} bets / {r['events']:>4} events, NO wins {r['no_win_rate']:.1%}, "
                      f"avg {r['avg_ret']:+.2%}/bet, {r['avg_ret_per_event']:+.2%}/event, t={r['t_stat']:.2f}")
        return

    df = collect(args.markets, args.min_volume, args.refresh, args.recent)
    print(f"{len(df)} resolved markets, volume >= ${args.min_volume:,.0f}, closed {df.closed.min()} -> {df.closed.max()}")
    for d in DAYS_BEFORE:
        t = calibration(df, f"p_{d}d")
        for c in ["avg_price", "yes_rate", "±2se", "edge"]:
            t[c] = t[c].map(lambda x: f"{x:.3f}")
        for c in ["buy_yes_ret", "buy_no_ret"]:
            t[c] = t[c].map(lambda x: f"{x:+.1%}")
        print(f"\n--- YES price {d} day(s) before close vs outcome ---")
        print(t.to_string(index=False))
    print("\nedge = actual YES rate - price. Negative edge on cheap buckets = longshots overpriced (buy NO).")
    print("Markets in the same event are correlated, so 'events' is the more honest sample size.")


if __name__ == "__main__":
    main()
