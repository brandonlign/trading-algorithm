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
    rule: str
    market: str  # "etf" or "crypto"
    weights: Callable[[pd.DataFrame], pd.DataFrame]
    freq: str  # rebalance: "M" monthly, "W" weekly
    cost_bps: float
    cash_asset: str | None = None  # where uninvested money sits (None = plain cash)
    benchmark: bool = False


SLEEVES = [
    Sleeve("etf_momentum", "ETF Momentum Rotation",
           "Top 3 of 9 asset-class ETFs by momentum; weak picks go to bonds. Monthly.",
           "etf", lambda px: A.xs_momentum(px, ROTATION_UNIVERSE, top=3, safe="IEF"), "M", 5, "BIL"),
    Sleeve("etf_rp_trend", "ETF Risk Parity + Trend",
           "Equal risk across 8 asset classes, skipping any below its 200-day average. Monthly.",
           "etf", lambda px: A.risk_parity(px, RISK_PARITY_UNIVERSE, trend_sma=200), "M", 5, "BIL"),
    Sleeve("btc_trend", "BTC Trend Ensemble",
           "1/3 in BTC per 50/100/200-day average the price is above. Weekly.",
           "crypto", lambda px: A.trend_ensemble(px, "BTC"), "W", 40),
    Sleeve("bench_spy", "SPY buy & hold", "Benchmark: 100% S&P 500.",
           "etf", lambda px: A.fixed(px, {"SPY": 1.0}), "M", 5, benchmark=True),
    Sleeve("bench_btc", "BTC buy & hold", "Benchmark: 100% bitcoin.",
           "crypto", lambda px: A.fixed(px, {"BTC": 1.0}), "W", 40, benchmark=True),
]
