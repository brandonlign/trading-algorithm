"""Crypto portfolio research: trend and momentum across the major coins vs just holding BTC.

Caveat: the coin list is today's survivors, which flatters anything that holds a basket.
Coins only enter once they have enough history on the exchange.

    .venv/bin/python -m research.crypto_strategies
"""
import pandas as pd

from trading import allocations as A
from trading.data import load_crypto_panel
from trading.portfolio import fmt_table, run_portfolio, stats_table

COINS = ["BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", "ADA/USD", "DOGE/USD", "LINK/USD",
         "AVAX/USD", "LTC/USD", "BCH/USD", "DOT/USD", "XLM/USD"]
START = "2019-01-01"
COST_BPS = 40
SPLITS = [("2019-01-01", "2022-12-31"), ("2023-01-01", "2026-12-31")]


def strategies(px: pd.DataFrame) -> dict:
    alts = list(px.columns)
    return {
        "BTC buy&hold": A.fixed(px, {"BTC": 1.0}),
        "BTC/ETH 50/50": A.fixed(px, {"BTC": 0.5, "ETH": 0.5}),
        "BTC trend sma50": A.trend_equal_weight(px, ["BTC"], sma=50, freq="W"),
        "BTC trend sma100": A.trend_equal_weight(px, ["BTC"], sma=100, freq="W"),
        "BTC trend sma200": A.trend_equal_weight(px, ["BTC"], sma=200, freq="W"),
        "BTC trend ensemble 50/100/200": A.trend_ensemble(px, "BTC"),
        "BTC vol-target 50%": A.vol_target(px, "BTC", 0.50, window=30, freq="W", periods_per_year=365),
        "BTC/ETH trend sma100": A.trend_equal_weight(px, ["BTC", "ETH"], sma=100, freq="W"),
        "Basket trend inv-vol": A.risk_parity(px, alts, window=30, freq="W", trend_sma=100, periods_per_year=365),
        "XS mom top3 (4/12wk)": A.xs_momentum(px, alts, top=3, lookbacks=(28, 84), trend_sma=100, freq="W"),
        "XS mom top3 (12wk)": A.xs_momentum(px, alts, top=3, lookbacks=(84,), trend_sma=100, freq="W"),
        "XS mom top5 (4/12wk)": A.xs_momentum(px, alts, top=5, lookbacks=(28, 84), trend_sma=100, freq="W"),
    }


def main():
    px = load_crypto_panel(COINS)
    test_px = px.loc[START:]
    results = {name: run_portfolio(test_px, w.loc[START:], COST_BPS) for name, w in strategies(px).items()}
    print(f"Crypto strategies {START} -> {px.index[-1].date()}  (cost {COST_BPS} bps per unit turnover)")
    print("history starts: " + ", ".join(f"{c} {px[c].first_valid_index().date()}" for c in px.columns) + "\n")
    print(fmt_table(stats_table(results, 365, SPLITS)))


if __name__ == "__main__":
    main()
