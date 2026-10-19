"""Multi-asset portfolio backtester.

A strategy returns target weights (rows = rebalance dates, columns = assets, rows sum to <= 1;
the remainder sits in cash earning nothing). Weights decided at a day's close are traded at that
close and held from the next day on, drifting with prices until the next rebalance. Every
rebalance pays `cost_bps` on the fraction of the portfolio that changes hands.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def rebalance_dates(index: pd.DatetimeIndex, freq: str) -> pd.DatetimeIndex:
    """Last available bar of each period: freq 'W' (weekly), 'M' (monthly) or 'D' (daily)."""
    if freq == "D":
        return index
    key = index.to_period("W" if freq == "W" else "M")
    return index[pd.Series(key, index=index).ne(pd.Series(key, index=index).shift(-1)).values]


def run_portfolio(prices: pd.DataFrame, weights: pd.DataFrame, cost_bps: float) -> pd.DataFrame:
    """Returns a frame with daily `equity`, `ret`, `turnover`, plus the held weights per asset."""
    prices = prices.ffill()
    rets = prices.pct_change().fillna(0.0).to_numpy()
    target = weights.reindex(prices.index).reindex(columns=prices.columns)
    has_target = target.notna().any(axis=1).to_numpy()
    target = target.fillna(0.0).to_numpy()
    cost = cost_bps / 10_000

    n_days, n_assets = rets.shape
    w = np.zeros(n_assets)
    equity = 1.0
    out_eq, out_ret, out_turn = np.empty(n_days), np.empty(n_days), np.zeros(n_days)
    held = np.empty((n_days, n_assets))
    for t in range(n_days):
        port_ret = float(w @ rets[t])
        equity *= 1 + port_ret
        if 1 + port_ret > 0:
            w = w * (1 + rets[t]) / (1 + port_ret)
        day_ret = port_ret
        if has_target[t]:
            turnover = float(np.abs(target[t] - w).sum())
            equity *= 1 - turnover * cost
            day_ret = (1 + port_ret) * (1 - turnover * cost) - 1
            out_turn[t] = turnover
            w = target[t].copy()
        out_eq[t], out_ret[t], held[t] = equity, day_ret, w
    df = pd.DataFrame(held, index=prices.index, columns=prices.columns)
    df["equity"], df["ret"], df["turnover"] = out_eq, out_ret, out_turn
    return df


def stats(result: pd.DataFrame, periods_per_year: int) -> dict:
    rets, eq = result["ret"], result["equity"]
    years = len(rets) / periods_per_year
    total = eq.iloc[-1] / eq.iloc[0] * (1 + rets.iloc[0]) - 1
    std = rets.std()
    yearly = (1 + rets).groupby(rets.index.year).prod() - 1
    dd = eq / eq.cummax() - 1
    return {
        "cagr": (1 + total) ** (1 / years) - 1 if total > -1 else -1.0,
        "vol": std * np.sqrt(periods_per_year),
        "sharpe": rets.mean() / std * np.sqrt(periods_per_year) if std > 0 else 0.0,
        "max_dd": dd.min(),
        "worst_year": yearly.min(),
        "turnover_yr": result["turnover"].sum() / years,
    }


def stats_table(results: dict[str, pd.DataFrame], periods_per_year: int,
                splits: list[tuple[str, str]] | None = None) -> pd.DataFrame:
    """One row per strategy: full-period stats plus Sharpe/CAGR in each sub-period."""
    rows = []
    for name, res in results.items():
        row = {"strategy": name, **stats(res, periods_per_year)}
        for start, end in splits or []:
            sub = res.loc[start:end]
            s = stats(sub, periods_per_year)
            row[f"sharpe {start[:4]}-{end[:4]}"] = s["sharpe"]
            row[f"cagr {start[:4]}-{end[:4]}"] = s["cagr"]
        rows.append(row)
    return pd.DataFrame(rows).set_index("strategy")


def fmt_table(df: pd.DataFrame) -> str:
    out = df.copy()
    for col in out.columns:
        if "sharpe" in col:
            out[col] = out[col].map(lambda x: f"{x:.2f}")
        elif col == "turnover_yr":
            out[col] = out[col].map(lambda x: f"{x:.1f}x")
        else:
            out[col] = out[col].map(lambda x: f"{x:+.1%}")
    return out.to_string()
