"""The strategies the live paper portfolio runs. Each sleeve starts with its own fake $10,000.

Picked from research/ because they held up out of sample and across nearby parameters,
not because they had the best backtest. Benchmarks run alongside so you can see whether
the strategies are actually earning their keep.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd

from trading import allocations as A

ROTATION_UNIVERSE = ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "GLD", "DBC", "VNQ"]
RISK_PARITY_UNIVERSE = ["SPY", "EFA", "EEM", "TLT", "IEF", "GLD", "DBC", "VNQ"]
ETF_TICKERS = sorted(set(ROTATION_UNIVERSE + RISK_PARITY_UNIVERSE + ["IEF", "BIL"]))
CRYPTO_SYMBOLS = ["BTC/USD"]


@dataclass
class Sleeve:
    key: str
    name: str
    description: str
    market: str  # "etf" or "crypto"
    weights: Callable[[pd.DataFrame], pd.DataFrame]
    freq: str  # rebalance: "M" monthly, "W" weekly
    cost_bps: float
    cash_asset: str | None = None  # where uninvested money sits (None = plain cash)
    benchmark: bool = False
    backtest_start: str = "2007-07-01"


SLEEVES = [
    Sleeve("etf_momentum", "ETF Momentum Rotation",
           "Each month, hold the 3 ETFs (of 9: stocks, bonds, gold, commodities, REITs) with the best "
           "1/3/6/12-month momentum. Picks below their 200-day average go to bonds (IEF).",
           "etf", lambda px: A.xs_momentum(px, ROTATION_UNIVERSE, top=3, safe="IEF"), "M", 5, "BIL"),
    Sleeve("etf_rp_trend", "ETF Risk Parity + Trend",
           "Each month, spread risk evenly across 8 asset classes (inverse volatility), skipping any "
           "below its 200-day average. Low return, very small drawdowns.",
           "etf", lambda px: A.risk_parity(px, RISK_PARITY_UNIVERSE, trend_sma=200), "M", 5, "BIL"),
    Sleeve("btc_trend", "BTC Trend Ensemble",
           "Each week, own 1/3 BTC for each of its 50/100/200-day averages the price is above. "
           "Aims to sit out the big crypto crashes.",
           "crypto", lambda px: A.trend_ensemble(px, "BTC"), "W", 40, None, backtest_start="2019-01-01"),
    Sleeve("bench_spy", "Benchmark: SPY buy & hold", "100% S&P 500, never trades.",
           "etf", lambda px: A.fixed(px, {"SPY": 1.0}), "M", 5, None, benchmark=True),
    Sleeve("bench_btc", "Benchmark: BTC buy & hold", "100% bitcoin, never trades.",
           "crypto", lambda px: A.fixed(px, {"BTC": 1.0}), "W", 40, None, benchmark=True,
           backtest_start="2019-01-01"),
]
