"""Portfolio strategies: each takes a price DataFrame and returns target weights on rebalance dates.

Every signal on date t uses only closes up to and including t. Assets without enough history
on a date are skipped, so the same code works for coins that listed recently.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .portfolio import rebalance_dates


def _frame(prices: pd.DataFrame, freq: str) -> tuple[pd.DatetimeIndex, pd.DataFrame]:
    dates = rebalance_dates(prices.index, freq)
    return dates, pd.DataFrame(0.0, index=dates, columns=prices.columns)


def _ann_vol(prices: pd.DataFrame, window: int, periods_per_year: int) -> pd.DataFrame:
    vol = prices.pct_change(fill_method=None).rolling(window).std() * np.sqrt(periods_per_year)
    return vol.where(vol > 0)  # a flat (stale) price isn't "zero risk"


def fixed(prices: pd.DataFrame, weights: dict, freq: str = "M") -> pd.DataFrame:
    """Static mix rebalanced on schedule (e.g. 100% SPY, or 60/40)."""
    dates, w = _frame(prices, freq)
    for asset, weight in weights.items():
        w[asset] = weight
    return w.where(prices.loc[dates].notna(), 0.0)


def vol_target(prices: pd.DataFrame, asset: str, target_vol: float = 0.15, window: int = 20,
               freq: str = "W", periods_per_year: int = 252) -> pd.DataFrame:
    """Hold more when markets are calm, less when they're wild. Never leverages above 100%."""
    dates, w = _frame(prices, freq)
    vol = _ann_vol(prices[[asset]], window, periods_per_year)[asset]
    w[asset] = (target_vol / vol.loc[dates]).clip(upper=1.0).fillna(0.0)
    return w


def trend_equal_weight(prices: pd.DataFrame, assets: list[str], sma: int = 210,
                       freq: str = "M") -> pd.DataFrame:
    """Faber's GTAA (2006): equal slices; each asset held only while above its moving average."""
    dates, w = _frame(prices, freq)
    above = (prices[assets] > prices[assets].rolling(sma).mean()).loc[dates]
    w[assets] = above.astype(float) / len(assets)
    return w


def trend_ensemble(prices: pd.DataFrame, asset: str, smas: tuple = (50, 100, 200),
                   freq: str = "W") -> pd.DataFrame:
    """Scale in by trend strength: 1/3 invested per moving average the price is above.
    Averaging several lookbacks avoids betting everything on one cherry-picked setting."""
    dates, w = _frame(prices, freq)
    px = prices[asset]
    w[asset] = sum((px > px.rolling(n).mean()).astype(float) for n in smas).loc[dates] / len(smas)
    return w


def dual_momentum(prices: pd.DataFrame, risk: list[str], safe: str, cash: str,
                  lookback: int = 252, freq: str = "M") -> pd.DataFrame:
    """Antonacci's GEM (2012): own the strongest risk asset if it beats cash, otherwise bonds."""
    dates, w = _frame(prices, freq)
    mom = (prices / prices.shift(lookback) - 1).loc[dates]
    for d in dates:
        r = mom.loc[d, risk].dropna()
        if r.empty or pd.isna(mom.loc[d, cash]):
            continue
        best = r.idxmax()
        w.loc[d, best if r[best] > mom.loc[d, cash] else safe] = 1.0
    return w


def xs_momentum(prices: pd.DataFrame, universe: list[str], top: int = 3,
                lookbacks: tuple = (21, 63, 126, 252), safe: str | None = None,
                trend_sma: int | None = 200, freq: str = "M") -> pd.DataFrame:
    """Rotation: hold the `top` assets with the best blended momentum, equal weight.
    A pick below its moving average (if trend_sma) has its slice moved to `safe` (or cash)."""
    dates, w = _frame(prices, freq)
    px = prices[universe]
    score = sum(px / px.shift(lb) - 1 for lb in lookbacks) / len(lookbacks)
    trend_ok = px > px.rolling(trend_sma).mean() if trend_sma else px.notna()
    for d in dates:
        s = score.loc[d].dropna()
        for asset in s.nlargest(top).index:
            if trend_ok.loc[d, asset] and s[asset] > 0:
                w.loc[d, asset] += 1 / top
            elif safe:
                w.loc[d, safe] += 1 / top
    return w


def risk_parity(prices: pd.DataFrame, assets: list[str], window: int = 63, freq: str = "M",
                trend_sma: int | None = None, target_vol: float | None = None,
                periods_per_year: int = 252) -> pd.DataFrame:
    """Inverse-volatility weights so each asset contributes similar risk.
    Optional trend filter (drop assets below their moving average -> cash) and portfolio vol target."""
    dates, w = _frame(prices, freq)
    px = prices[assets]
    vol = _ann_vol(px, window, periods_per_year).loc[dates]
    inv = 1 / vol
    raw = inv.div(inv.sum(axis=1), axis=0).fillna(0.0)
    if trend_sma:
        raw = raw * (px > px.rolling(trend_sma).mean()).loc[dates]
    if target_vol:
        rets = px.pct_change(fill_method=None)
        for d in dates:
            hist = rets.loc[:d].tail(window)
            port_vol = (hist.fillna(0) @ raw.loc[d]).std() * np.sqrt(periods_per_year)
            if port_vol > 0:
                raw.loc[d] *= min(1.0, target_vol / port_vol)
    w[assets] = raw
    return w


def park_cash(weights: pd.DataFrame, cash: str) -> pd.DataFrame:
    """Put whatever isn't invested into a T-bill ETF instead of leaving it idle at 0%."""
    w = weights.copy()
    if cash not in w:
        w[cash] = 0.0
    w[cash] += (1 - w.sum(axis=1)).clip(lower=0)
    return w


def blend(*parts: tuple[float, pd.DataFrame]) -> pd.DataFrame:
    """Combine strategies, e.g. blend((0.5, a), (0.5, b)). Rebalance dates are the union."""
    idx = sorted(set().union(*(p.index for _, p in parts)))
    cols = sorted(set().union(*(p.columns for _, p in parts)))
    total = pd.DataFrame(0.0, index=idx, columns=cols)
    for share, p in parts:
        total = total.add(share * p.reindex(index=idx, columns=cols).ffill().fillna(0.0), fill_value=0.0)
    return total
