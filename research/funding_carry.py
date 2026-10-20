"""Crypto funding-rate carry: long spot + short perpetual of the same coin.

The two legs cancel price risk; what's left is the funding payment perp longs pay perp shorts
(positive most of the time because traders like leveraged longs). Data: Hyperliquid hourly funding.

Return is measured on *capital*: every $1 of hedged position needs $1 for the spot coin plus
perp margin (1 / PERP_LEVERAGE). Fees are paid on both legs whenever positions change.
Ignored: exchange failure, liquidation of the short in a squeeze, spot/perp price gaps on exit.

    .venv/bin/python -m research.funding_carry
"""
from __future__ import annotations

import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "AVAX", "LINK", "LTC", "BNB", "ADA", "SUI", "ARB", "OP", "APT", "WIF", "PEPE"]
PERP_LEVERAGE = 2.0          # short perp margined at 2x -> survives a ~40% rally before trouble
CAPITAL_PER_NOTIONAL = 1 + 1 / PERP_LEVERAGE
FEE_PER_LEG = {"spot": 0.0010, "perp": 0.00045}  # taker fees: typical spot venue, Hyperliquid perp
ROUND_TRIP = 2 * (FEE_PER_LEG["spot"] + FEE_PER_LEG["perp"])  # open + close both legs, per $ notional
HOURS_PER_YEAR = 24 * 365


def funding_history(coin: str, years: float = 3.0) -> pd.Series:
    """Hourly funding rate (fraction per hour) for COIN on Hyperliquid, cached on disk."""
    path = DATA_DIR / f"funding_hyperliquid_{coin}.csv"
    if path.exists() and time.time() - path.stat().st_mtime < 24 * 3600:
        return pd.read_csv(path, index_col=0, parse_dates=True)["rate"]
    import ccxt

    ex = ccxt.hyperliquid({"enableRateLimit": True})
    since = ex.milliseconds() - int(years * 365 * 86_400_000)
    rows = []
    while since < ex.milliseconds():
        batch = ex.fetch_funding_rate_history(f"{coin}/USDC:USDC", since=since, limit=500)
        if not batch:
            break
        rows += [(b["timestamp"], b["fundingRate"]) for b in batch]
        if batch[-1]["timestamp"] <= since:
            break
        since = batch[-1]["timestamp"] + 1
    s = pd.Series({pd.to_datetime(t, unit="ms").floor("h"): r for t, r in rows}, name="rate").sort_index()
    DATA_DIR.mkdir(exist_ok=True)
    s.to_frame().to_csv(path)
    return s


def load_panel() -> pd.DataFrame:
    cols = {}
    for c in COINS:
        try:
            cols[c] = funding_history(c)
        except Exception as err:
            print(f"skip {c}: {type(err).__name__} {err}"[:120])
    return pd.DataFrame(cols).sort_index()


def simulate(funding: pd.DataFrame, weights: pd.DataFrame) -> pd.Series:
    """Hourly return on capital. `weights` = $ notional per $ capital-capacity, rebalanced on its index."""
    w = weights.reindex(funding.index).ffill().fillna(0.0)
    held = w.shift(1).fillna(0.0)  # decided at t, earns from t+1
    gross = (held * funding.fillna(0.0)).sum(axis=1)
    turnover = held.diff().abs().sum(axis=1).fillna(held.abs().sum(axis=1))
    fees = turnover * ROUND_TRIP / 2  # each change = half a round trip (open or close)
    return (gross - fees) / CAPITAL_PER_NOTIONAL


def daily_stats(hourly: pd.Series) -> dict:
    daily = (1 + hourly).resample("D").prod() - 1
    eq = (1 + daily).cumprod()
    years = len(daily) / 365
    return {"apr": eq.iloc[-1] ** (1 / years) - 1, "vol": daily.std() * np.sqrt(365),
            "sharpe": daily.mean() / daily.std() * np.sqrt(365) if daily.std() > 0 else np.nan,
            "max_dd": (eq / eq.cummax() - 1).min(), "neg_months": int(((1 + daily).resample("ME").prod() < 1).sum()),
            "months": int(len((1 + daily).resample("ME").prod()))}


def strategies(f: pd.DataFrame) -> dict:
    daily_idx = f.resample("D").last().index
    trailing = f.rolling(24 * 7, min_periods=24 * 3).mean() * HOURS_PER_YEAR  # 7-day avg funding APR
    t_daily = trailing.reindex(daily_idx, method="ffill")
    out = {}
    for coin in ["BTC", "ETH", "SOL"]:
        if coin in f:
            out[f"{coin} always on"] = pd.DataFrame({coin: 1.0}, index=daily_idx[:1])
            out[f"{coin} on if 7d APR>10%"] = (t_daily[[coin]] > 0.10).astype(float)
    majors = [c for c in ["BTC", "ETH", "SOL"] if c in f]
    out["BTC/ETH/SOL equal, always on"] = pd.DataFrame({c: 1 / len(majors) for c in majors}, index=daily_idx[:1])
    for top, thr in [(3, 0.10), (5, 0.10), (3, 0.20), (5, 0.05)]:
        w = pd.DataFrame(0.0, index=daily_idx, columns=f.columns)
        weekly = daily_idx[daily_idx.dayofweek == 0]
        for d in weekly:
            row = t_daily.loc[d].dropna()
            picks = row[row > thr].nlargest(top)
            w.loc[d, picks.index] = 1 / top
        out[f"Top{top} coins, 7d APR>{thr:.0%}, weekly"] = w.loc[weekly]
    return out


def main():
    f = load_panel()
    f = f.loc[f.index >= f["BTC"].first_valid_index()]
    print(f"Hyperliquid funding {f.index[0].date()} -> {f.index[-1].date()}, coins: {', '.join(f.columns)}")
    print(f"capital per $1 hedged = ${CAPITAL_PER_NOTIONAL:.2f}, round-trip fees {ROUND_TRIP:.2%} of notional\n")

    avg = (f.mean() * HOURS_PER_YEAR).sort_values(ascending=False)
    pos = (f > 0).mean()
    print("Average funding APR (gross, per $ notional) and % of hours positive:")
    print("  " + "  ".join(f"{c} {avg[c]:+.1%} ({pos[c]:.0%})" for c in avg.index[:16]) + "\n")

    yearly = (f[["BTC", "ETH", "SOL"]].resample("YE").mean() * HOURS_PER_YEAR)
    yearly.index = yearly.index.year
    print("BTC/ETH/SOL gross funding APR by year:\n" + yearly.map(lambda x: f"{x:+.1%}").to_string() + "\n")

    split = f.index[0] + (f.index[-1] - f.index[0]) / 2
    rows = []
    for name, w in strategies(f).items():
        r = simulate(f, w)
        full, a, b = daily_stats(r), daily_stats(r[r.index < split]), daily_stats(r[r.index >= split])
        rows.append({"strategy": name, "APR": full["apr"], "vol": full["vol"], "sharpe": full["sharpe"],
                     "max_dd": full["max_dd"], "losing months": f"{full['neg_months']}/{full['months']}",
                     "APR 1st half": a["apr"], "APR 2nd half": b["apr"]})
    df = pd.DataFrame(rows).set_index("strategy")
    for c in ["APR", "vol", "max_dd", "APR 1st half", "APR 2nd half"]:
        df[c] = df[c].map(lambda x: f"{x:+.1%}")
    df["sharpe"] = df["sharpe"].map(lambda x: f"{x:.1f}")
    print(f"Net return on capital after fees (halves split at {split.date()}):")
    print(df.to_string())


if __name__ == "__main__":
    main()
