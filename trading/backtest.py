"""Vectorized long/flat backtester with fees, slippage, and honest train/test evaluation."""
from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .strategies import STRATEGIES


@dataclass
class Result:
    equity: pd.Series
    position: pd.Series
    stats: dict


def run(close: pd.Series, target: pd.Series, fee_bps: float, slippage_bps: float,
        periods_per_year: int) -> Result:
    """Backtest a target-position series (0 = cash, 1 = fully invested).

    The signal computed at bar t's close is held over bar t+1, so a strategy can
    never trade on a price it hasn't seen yet. Every change in position pays
    fee + slippage on the traded fraction of equity.
    """
    target = target.reindex(close.index).fillna(0.0).clip(0.0, 1.0)
    position = target.shift(1).fillna(0.0)
    returns = close.pct_change().fillna(0.0)
    turnover = position.diff().abs().fillna(position.abs())
    costs = turnover * (fee_bps + slippage_bps) / 10_000
    strat_returns = position * returns - costs
    equity = (1 + strat_returns).cumprod()
    return Result(equity, position, _stats(strat_returns, equity, position, turnover, periods_per_year))


def _stats(rets: pd.Series, equity: pd.Series, position: pd.Series, turnover: pd.Series,
           periods_per_year: int) -> dict:
    years = max(len(rets) / periods_per_year, 1e-9)
    total = equity.iloc[-1] - 1
    std = rets.std()
    return {
        "return": total,
        "cagr": (1 + total) ** (1 / years) - 1 if total > -1 else -1.0,
        "sharpe": rets.mean() / std * np.sqrt(periods_per_year) if std > 0 else 0.0,
        "max_dd": (equity / equity.cummax() - 1).min(),
        "trades": int((turnover > 0).sum()),
        "exposure": position.mean(),
    }


def param_grid(name: str):
    grid = STRATEGIES[name]["grid"]
    keys = list(grid)
    for values in itertools.product(*grid.values()):
        params = dict(zip(keys, values))
        if STRATEGIES[name].get("valid", lambda p: True)(params):
            yield params


def evaluate(close: pd.Series, fee_bps: float, slippage_bps: float, periods_per_year: int,
             train_frac: float = 0.7) -> pd.DataFrame:
    """Pick each strategy's best parameters on the first `train_frac` of history (by Sharpe),
    then report how those exact parameters did on the unseen remainder.

    The "test" columns are the numbers that matter: the train numbers are always
    flattering because the parameters were chosen to fit them.
    """
    split = close.index[int(len(close) * train_frac)]
    train, test = close[close.index < split], close[close.index >= split]
    rows = []
    for name, spec in STRATEGIES.items():
        best_params, best_train = None, None
        for params in param_grid(name):
            target = spec["fn"](close, **params)
            res = run(train, target[train.index], fee_bps, slippage_bps, periods_per_year)
            if best_train is None or res.stats["sharpe"] > best_train["sharpe"]:
                best_params, best_train = params, res.stats
        target = spec["fn"](close, **best_params)
        test_res = run(test, target[test.index], fee_bps, slippage_bps, periods_per_year)
        rows.append({
            "strategy": name,
            "params": ",".join(f"{k}={v}" for k, v in best_params.items()) or "-",
            "train_sharpe": best_train["sharpe"],
            "test_return": test_res.stats["return"],
            "test_cagr": test_res.stats["cagr"],
            "test_sharpe": test_res.stats["sharpe"],
            "test_max_dd": test_res.stats["max_dd"],
            "test_trades": test_res.stats["trades"],
            "test_exposure": test_res.stats["exposure"],
        })
    df = pd.DataFrame(rows)
    df.attrs["train_period"] = (train.index[0].date(), train.index[-1].date())
    df.attrs["test_period"] = (test.index[0].date(), test.index[-1].date())
    return df


def format_report(df: pd.DataFrame, title: str) -> str:
    tr, te = df.attrs["train_period"], df.attrs["test_period"]
    out = df.copy()
    for col in ["test_return", "test_cagr", "test_max_dd", "test_exposure"]:
        out[col] = out[col].map(lambda x: f"{x:+.1%}" if col != "test_exposure" else f"{x:.0%}")
    for col in ["train_sharpe", "test_sharpe"]:
        out[col] = out[col].map(lambda x: f"{x:.2f}")
    bh = df.loc[df.strategy == "buy_and_hold", "test_sharpe"].iloc[0]
    winners = df[(df.strategy != "buy_and_hold") & (df.test_sharpe > bh)].strategy.tolist()
    verdict = (f"Beat buy & hold (risk-adjusted) out of sample: {', '.join(winners)}"
               if winners else "Nothing beat buy & hold out of sample.")
    return (f"\n=== {title} ===\n"
            f"train {tr[0]} -> {tr[1]}  |  test {te[0]} -> {te[1]}\n"
            f"{out.to_string(index=False)}\n{verdict}\n")
