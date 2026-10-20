"""Polymarket vs Kalshi: find games where buying every outcome across the two venues costs < $1.

Each game has 2 outcomes (or 3 with a draw). For each outcome take the cheapest ask on either
venue, add that venue's taker fee, and sum. Below $1 means one share of every outcome is
guaranteed to pay $1 whatever happens: a locked-in profit, if both venues settle the same way.

Matching: Kalshi tickers like KXNBAGAME-26OCT20BOSDET map to Polymarket slugs like
nba-bos-det-2026-10-20. Outcomes are mapped by position and sanity-checked: if the two venues
price an outcome more than 15c apart the match is assumed wrong and skipped. Read-only.

    .venv/bin/python cross_venue.py              # one scan
    .venv/bin/python cross_venue.py --watch 120  # rescan every 2 min, log hits to data/cross_venue_hits.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore", message=".*OpenSSL.*")
import requests  # noqa: E402

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
LEAGUES = {"KXNFLGAME": "nfl", "KXNBAGAME": "nba", "KXNHLGAME": "nhl", "KXMLBGAME": "mlb",
           "KXEPLGAME": "epl", "KXUCLGAME": "ucl", "KXNCAAFGAME": "cfb", "KXWNBAGAME": "wnba"}
MONTHS = {m: i + 1 for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}
MAX_MISMATCH = 0.15
LOG_PATH = Path(__file__).resolve().parent / "data" / "cross_venue_hits.csv"

session = requests.Session()


def kalshi_fee(price: float, multiplier: float) -> float:
    """Kalshi taker fee per contract (quadratic): 0.07 * p * (1 - p), rounded up to the cent per order.
    We charge the unrounded amount plus half a cent so small orders aren't flattered."""
    return 0.07 * multiplier * price * (1 - price) + 0.005


def poly_fee(market: dict, price: float) -> float:
    sched = market.get("feeSchedule") or {}
    if not market.get("feesEnabled") or not sched:
        return 0.0
    return float(sched.get("rate", 0)) * (price * (1 - price)) ** float(sched.get("exponent", 1))


def kalshi_games() -> list[dict]:
    games = []
    for series, league in LEAGUES.items():
        try:
            mult = session.get(f"{KALSHI}/series/{series}", timeout=20).json()["series"].get("fee_multiplier", 1)
            events = session.get(f"{KALSHI}/events", params={"series_ticker": series, "status": "open",
                                                            "with_nested_markets": "true", "limit": 200},
                                 timeout=30).json().get("events", [])
        except (requests.RequestException, KeyError, ValueError):
            continue
        for e in events:
            m = re.match(r".*-(\d\d)([A-Z]{3})(\d\d)(?:\d{4})?([A-Z]+)$", e["event_ticker"])
            if not m:
                continue
            yy, mon, dd, teams = m.groups()
            markets = {mk["ticker"].split("-")[-1]: mk for mk in e["markets"] if mk.get("status") == "active"}
            codes = [c for c in markets if c != "TIE"]
            away = next((c for c in codes if teams.startswith(c)), None)
            home = next((c for c in codes if teams.endswith(c) and c != away), None)
            if not away or not home:
                continue
            games.append({"league": league, "date": f"20{yy}-{MONTHS[mon]:02d}-{dd}", "away": away, "home": home,
                          "title": e.get("title", e["event_ticker"]), "kalshi": markets, "fee_mult": float(mult)})
    return games


def poly_outcomes(game: dict) -> dict | None:
    """Map Polymarket's moneyline to {away, home[, TIE]} -> (yes token id, market)."""
    slug = f"{game['league']}-{game['away'].lower()}-{game['home'].lower()}-{game['date']}"
    try:
        events = session.get(f"{GAMMA}/events", params={"slug": slug}, timeout=20).json()
    except (requests.RequestException, ValueError):
        return None
    if not events:
        return None
    lines = [m for m in events[0].get("markets", []) if m.get("sportsMarketType") == "moneyline"
             and m.get("active") and not m.get("closed") and m.get("acceptingOrders")]
    game["slug"] = slug
    if len(lines) == 1:  # two-way: one market, outcomes [away, home]
        tokens = json.loads(lines[0]["clobTokenIds"])
        return {game["away"]: (tokens[0], lines[0]), game["home"]: (tokens[1], lines[0])}
    if len(lines) == 3 and "TIE" in game["kalshi"]:  # soccer: [away win, draw, home win], each Yes/No
        order = [game["away"], "TIE", game["home"]]
        return {code: (json.loads(m["clobTokenIds"])[0], m) for code, m in zip(order, lines)}
    return None


def poly_ask(token: str) -> tuple[float, float] | None:
    book = session.get(f"{CLOB}/book", params={"token_id": token}, timeout=15).json()
    asks = sorted((float(o["price"]), float(o["size"])) for o in book.get("asks", []))
    return asks[0] if asks else None


def kalshi_ask(ticker: str) -> tuple[float, float] | None:
    """YES ask = 1 - best NO bid (Kalshi books only list bids)."""
    ob = session.get(f"{KALSHI}/markets/{ticker}/orderbook", timeout=15).json().get("orderbook_fp", {})
    no_bids = [(float(p), float(q)) for p, q in ob.get("no_dollars") or []]
    if not no_bids:
        return None
    p, q = max(no_bids)
    return round(1 - p, 4), q


def price_game(game: dict) -> dict | None:
    poly = poly_outcomes(game)
    if not poly:
        return None
    legs, mids = {}, []
    for code, kmarket in game["kalshi"].items():
        if code not in poly:
            return None
        k = kalshi_ask(kmarket["ticker"])
        token, pmarket = poly[code]
        p = poly_ask(token)
        if not k or not p:
            return None
        if abs(k[0] - p[0]) > MAX_MISMATCH:
            return None  # venues disagree too much: probably mapped the wrong side
        k_cost, p_cost = k[0] + kalshi_fee(k[0], game["fee_mult"]), p[0] + poly_fee(pmarket, p[0])
        legs[code] = (("Kalshi", k[0], k_cost, k[1]) if k_cost <= p_cost else ("Polymarket", p[0], p_cost, p[1]))
    cost = sum(leg[2] for leg in legs.values())
    size = min(leg[3] for leg in legs.values())
    return {**game, "legs": legs, "cost": cost, "edge": 1 - cost, "size": size,
            "profit": max(0.0, 1 - cost) * size}


def scan() -> list[dict]:
    games = kalshi_games()
    with ThreadPoolExecutor(4) as pool:  # more threads trips the venues' rate limits
        priced = [g for g in pool.map(price_game, games) if g]
    priced.sort(key=lambda g: g["edge"], reverse=True)
    return priced


def report(priced: list[dict], top: int):
    hits = [g for g in priced if g["edge"] > 0]
    print(f"\n[{datetime.now():%H:%M:%S}] {len(priced)} games matched on both venues, {len(hits)} with a locked-in edge")
    print(f"{'edge':>7} {'size':>8} {'profit$':>8}  game / cheapest venue per outcome")
    for g in priced[:top]:
        legs = ", ".join(f"{c} {v[0][:4]} {v[1]:.2f}" for c, v in g["legs"].items())
        print(f"{g['edge'] * 100:>6.2f}¢ {g['size']:>8.0f} {g['profit']:>8.2f}  {g['league'].upper()} "
              f"{g['away']}@{g['home']} {g['date']}  [{legs}]")


def log_hits(priced: list[dict]):
    hits = [g for g in priced if g["edge"] > 0]
    if not hits:
        return
    LOG_PATH.parent.mkdir(exist_ok=True)
    new = not LOG_PATH.exists()
    with LOG_PATH.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time_utc", "league", "game", "date", "edge", "size", "profit", "legs"])
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for g in hits:
            w.writerow([now, g["league"], f"{g['away']}@{g['home']}", g["date"], round(g["edge"], 4),
                        round(g["size"]), round(g["profit"], 2),
                        "; ".join(f"{c}:{v[0]}@{v[1]}" for c, v in g["legs"].items())])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--watch", type=int, metavar="SECONDS")
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()
    while True:
        priced = scan()
        report(priced, args.top)
        log_hits(priced)
        if not args.watch:
            break
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
