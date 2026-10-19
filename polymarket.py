"""Scan Polymarket for multi-outcome events whose prices don't add up.

In a "negative risk" event (e.g. "Who wins X?") exactly one outcome resolves YES, so:
  * buying 1 YES of every outcome always pays $1   -> free money if the asks sum to < $1
  * buying 1 NO  of every outcome always pays $n-1 -> free money if the NO asks sum to < $n-1
    (equivalently: the YES bids sum to > $1)

Gamma's cached best bid/ask is used to shortlist events, then live order books are walked to
see how many full sets you could actually buy, after taker fees, before the edge disappears.
Read-only: it never places an order.

    .venv/bin/python polymarket.py               # one scan, print top opportunities
    .venv/bin/python polymarket.py --watch 60    # rescan every 60s and log hits to data/polymarket_arbs.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore", message=".*OpenSSL.*")
import requests  # noqa: E402

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
LOG_PATH = Path(__file__).resolve().parent / "data" / "polymarket_arbs.csv"

session = requests.Session()


def fetch_open_events() -> list[dict]:
    events, cursor = [], None
    while True:
        params = {"closed": "false", "limit": 500}
        if cursor:
            params["after_cursor"] = cursor
        page = session.get(f"{GAMMA}/events/keyset", params=params, timeout=30).json()
        events += page.get("events") or []
        cursor = page.get("next_cursor")
        if not cursor or not page.get("events"):
            return events


def taker_fee(market: dict, price: float) -> float:
    """Polymarket taker fee per share: rate * (p * (1 - p)) ** exponent. Makers pay nothing."""
    sched = market.get("feeSchedule") or {}
    if not market.get("feesEnabled") or not sched:
        return 0.0
    return float(sched.get("rate", 0)) * (price * (1 - price)) ** float(sched.get("exponent", 1))


def tradeable(m: dict) -> bool:
    return (m.get("active") and not m.get("closed") and m.get("acceptingOrders")
            and m.get("enableOrderBook") and m.get("clobTokenIds"))


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def shortlist(events: list[dict], include_augmented: bool, near: float) -> list[dict]:
    """Cheap first pass on Gamma's cached prices. Keeps events within `near` of breaking even."""
    out = []
    for e in events:
        if not e.get("negRisk"):
            continue  # outcomes aren't guaranteed mutually exclusive
        if e.get("negRiskAugmented") and not include_augmented:
            continue  # new outcomes can be added later, so "every outcome" isn't a fixed set
        all_markets = e.get("markets") or []
        markets = [m for m in all_markets if tradeable(m)]
        live = [m for m in all_markets if m.get("active") and not m.get("closed")]
        if len(markets) < 2 or len(markets) != len(live):
            continue  # some live outcome can't be traded, so a full set can't be bought
        asks = [_f(m.get("bestAsk")) for m in markets]
        bids = [_f(m.get("bestBid")) for m in markets]
        n = len(markets)
        if all(a is not None for a in asks) and 1 - sum(asks) > -near:
            out.append({"event": e, "markets": markets, "side": "BUY ALL YES", "payoff": 1.0})
        if all(b is not None and b > 0 for b in bids) and sum(bids) - 1 > -near:
            out.append({"event": e, "markets": markets, "side": "BUY ALL NO", "payoff": float(n - 1)})
    return out


def leg_tokens(candidate: dict) -> list[str]:
    leg = 0 if candidate["side"] == "BUY ALL YES" else 1  # clobTokenIds = [YES, NO]
    return [json.loads(m["clobTokenIds"])[leg] for m in candidate["markets"]]


def fetch_asks(token_ids: list[str]) -> dict[str, list[tuple[float, float]]]:
    """Live ask ladders (cheapest first), fetched in batches."""
    asks = {}
    for i in range(0, len(token_ids), 100):
        chunk = [{"token_id": t} for t in token_ids[i:i + 100]]
        for book in session.post(f"{CLOB}/books", json=chunk, timeout=30).json():
            asks[book["asset_id"]] = sorted((float(o["price"]), float(o["size"])) for o in book.get("asks", []))
    return asks


def walk_books(candidate: dict, asks: dict) -> dict:
    """Buy full sets level by level until a set costs more (incl. fees) than it pays out."""
    markets = candidate["markets"]
    books = [asks.get(t, []) for t in leg_tokens(candidate)]
    if any(not b for b in books):
        return {**candidate, "top_edge": None, "sets": 0.0, "profit": 0.0, "cost": 0.0}

    idx = [0] * len(books)
    remaining = [b[0][1] for b in books]
    top_edge, sets, cost = None, 0.0, 0.0
    while True:
        prices = [books[i][idx[i]][0] for i in range(len(books))]
        per_set = sum(p + taker_fee(m, p) for p, m in zip(prices, markets))
        edge = candidate["payoff"] - per_set
        if top_edge is None:
            top_edge = edge
        if edge <= 0:
            break
        qty = min(remaining)
        sets += qty
        cost += qty * per_set
        for i in range(len(books)):
            remaining[i] -= qty
            if remaining[i] <= 1e-9:
                idx[i] += 1
                if idx[i] >= len(books[i]):
                    return _done(candidate, top_edge, sets, cost)
                remaining[i] = books[i][idx[i]][1]
    return _done(candidate, top_edge, sets, cost)


def _done(candidate, top_edge, sets, cost):
    return {**candidate, "top_edge": top_edge, "sets": sets, "cost": cost,
            "profit": sets * candidate["payoff"] - cost, "flags": red_flags(candidate["event"], top_edge)}


SUSPICIOUS_EDGE = 0.03


def red_flags(event: dict, top_edge: float) -> str:
    """Reasons a "risk-free" set may not be. A big edge usually means the market thinks
    there's a real chance NONE of the listed outcomes wins (cancelled, postponed, no IPO, ...)."""
    flags = []
    end = event.get("endDate")
    if end and datetime.fromisoformat(end.replace("Z", "+00:00")) < datetime.now(timezone.utc):
        flags.append("PAST END DATE")
    if top_edge is not None and top_edge > SUSPICIOUS_EDGE:
        flags.append("EDGE TOO BIG: can every outcome lose? read the rules")
    return "; ".join(flags)


def scan(include_augmented: bool, near: float) -> tuple[int, list[dict]]:
    events = fetch_open_events()
    candidates = shortlist(events, include_augmented, near)
    asks = fetch_asks(sorted({t for c in candidates for t in leg_tokens(c)}))
    results = [walk_books(c, asks) for c in candidates]
    results = [r for r in results if r["top_edge"] is not None]
    results.sort(key=lambda r: r["top_edge"], reverse=True)
    return len(events), results


def print_results(n_events: int, results: list[dict], top: int):
    hits = [r for r in results if r["profit"] > 0]
    clean = [r for r in hits if not r["flags"]]
    print(f"\n[{datetime.now():%H:%M:%S}] scanned {n_events} open events, {len(results)} close to break-even, "
          f"{len(hits)} profitable after fees ({len(clean)} without red flags)")
    print(f"{'edge/set':>9} {'sets':>9} {'profit$':>9} {'cost$':>10}  {'side':<11} {'n':>2}  {'ends':<10}  event")
    for r in results[:top]:
        e = r["event"]
        print(f"{r['top_edge'] * 100:>8.2f}¢ {r['sets']:>9.1f} {r['profit']:>9.2f} {r['cost']:>10.2f}  "
              f"{r['side']:<11} {len(r['markets']):>2}  {(e.get('endDate') or '?')[:10]:<10}  {e['title'][:60]}")
        print(f"{'':>67}https://polymarket.com/event/{e['slug']}")
        if r["flags"]:
            print(f"{'':>67}!! {r['flags']}")
    if not hits:
        print("No risk-free sets right now. That's normal: bots close these fast. Use --watch to log how often they appear.")


def log_hits(results: list[dict]):
    hits = [r for r in results if r["profit"] > 0]
    if not hits:
        return
    LOG_PATH.parent.mkdir(exist_ok=True)
    new = not LOG_PATH.exists()
    with LOG_PATH.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time_utc", "event", "slug", "side", "outcomes", "edge_per_set", "sets", "cost", "profit",
                        "flags"])
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for r in hits:
            w.writerow([now, r["event"]["title"], r["event"]["slug"], r["side"], len(r["markets"]),
                        round(r["top_edge"], 5), round(r["sets"], 2), round(r["cost"], 2), round(r["profit"], 2), r["flags"]])


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--watch", type=int, metavar="SECONDS", help="rescan forever at this interval")
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--near", type=float, default=0.02, help="shortlist events within this many $ of break-even")
    p.add_argument("--include-augmented", action="store_true",
                   help="also scan events where new outcomes can be added later (NOT truly risk-free)")
    args = p.parse_args()

    while True:
        n_events, results = scan(args.include_augmented, args.near)
        print_results(n_events, results, args.top)
        log_hits(results)
        if not args.watch:
            break
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
