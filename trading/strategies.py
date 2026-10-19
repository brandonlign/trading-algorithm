"""Classic signal strategies. Each returns a target position series: 1 = invested, 0 = cash.

A strategy only looks at prices up to and including the current bar; the backtester
handles the one-bar delay before acting on it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def buy_and_hold(close: pd.Series) -> pd.Series:
    return pd.Series(1.0, index=close.index)


def sma_cross(close: pd.Series, fast: int = 20, slow: int = 100) -> pd.Series:
    """Trend following: invested while the fast moving average is above the slow one."""
    signal = close.rolling(fast).mean() > close.rolling(slow).mean()
    return signal.astype(float)


def momentum(close: pd.Series, lookback: int = 90) -> pd.Series:
    """Time-series momentum: invested while the price is above where it was `lookback` bars ago."""
    return (close / close.shift(lookback) > 1).astype(float)


def rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    return 100 - 100 / (1 + gain / loss.replace(0, np.nan))


def rsi_reversion(close: pd.Series, period: int = 14, buy_below: float = 30,
                  sell_above: float = 55) -> pd.Series:
    """Mean reversion: buy when oversold (RSI low), sell once it recovers."""
    r = rsi(close, period).to_numpy()
    pos = np.zeros(len(r))
    holding = False
    for i, v in enumerate(r):
        if not holding and v < buy_below:
            holding = True
        elif holding and v > sell_above:
            holding = False
        pos[i] = 1.0 if holding else 0.0
    return pd.Series(pos, index=close.index)


def trend_with_vol_filter(close: pd.Series, slow: int = 100, vol_window: int = 20,
                          vol_cap: float = 0.6) -> pd.Series:
    """Invested in an uptrend (price above slow MA), stepping aside when volatility spikes."""
    returns = close.pct_change()
    ann_vol = returns.rolling(vol_window).std() * np.sqrt(252)
    trend = close > close.rolling(slow).mean()
    calm = ann_vol < ann_vol.rolling(252, min_periods=60).quantile(vol_cap)
    return (trend & calm).astype(float)


STRATEGIES = {
    "buy_and_hold": {"fn": buy_and_hold, "grid": {}},
    "sma_cross": {
        "fn": sma_cross,
        "grid": {"fast": [10, 20, 50], "slow": [50, 100, 200]},
        "valid": lambda p: p["fast"] < p["slow"],
    },
    "momentum": {"fn": momentum, "grid": {"lookback": [20, 60, 90, 180, 252]}},
    "rsi_reversion": {
        "fn": rsi_reversion,
        "grid": {"period": [7, 14], "buy_below": [25, 30, 35], "sell_above": [50, 60, 70]},
    },
    "trend_vol_filter": {
        "fn": trend_with_vol_filter,
        "grid": {"slow": [50, 100, 200], "vol_cap": [0.6, 0.8]},
    },
}
