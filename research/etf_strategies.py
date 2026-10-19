"""ETF portfolio research: well-known published strategies vs buy & hold, 2007-2026.

Using strategies published *before* most of this data (Faber 2006, Antonacci 2012, risk parity
~2005) with their textbook parameters means most of the test period is genuinely out of sample.
Idle cash earns the T-bill ETF (BIL). Nothing here is tuned to this data; the sensitivity section shows how fragile each choice is.

    .venv/bin/python -m research.etf_strategies
"""
import pandas as pd

from trading import allocations as A
from trading.data import load_stock_panel
from trading.portfolio import fmt_table, run_portfolio, stats_table

UNIVERSE = ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "SHY", "GLD", "DBC", "VNQ", "LQD", "HYG", "BIL"]
START = "2007-07-01"
COST_BPS = 5
SPLITS = [("2007-07-01", "2016-12-31"), ("2017-01-01", "2026-12-31")]


def strategies(px: pd.DataFrame) -> dict:
    gtaa = A.trend_equal_weight(px, ["SPY", "EFA", "IEF", "VNQ", "DBC"])
    rp_trend = A.risk_parity(px, ["SPY", "EFA", "EEM", "TLT", "IEF", "GLD", "DBC", "VNQ"], trend_sma=200)
    gem = A.dual_momentum(px, ["SPY", "EFA"], safe="IEF", cash="BIL")
    return {
        "SPY buy&hold": A.fixed(px, {"SPY": 1.0}),
        "60/40 SPY/IEF": A.fixed(px, {"SPY": 0.6, "IEF": 0.4}),
        "SPY vol-target 15%": A.vol_target(px, "SPY", 0.15),
        "GTAA trend (Faber)": gtaa,
        "Dual momentum (GEM)": gem,
        "XS momentum top3": A.xs_momentum(px, ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "GLD", "DBC", "VNQ"],
                                          top=3, safe="IEF"),
        "Risk parity": A.risk_parity(px, ["SPY", "TLT", "GLD", "DBC"]),
        "Risk parity + trend": rp_trend,
        "Combo: GEM+RP trend": A.blend((0.5, gem), (0.5, rp_trend)),
    }


def sensitivity(px: pd.DataFrame) -> dict:
    out = {}
    for sma in [100, 150, 200, 250]:
        out[f"GTAA sma={sma}"] = A.trend_equal_weight(px, ["SPY", "EFA", "IEF", "VNQ", "DBC"], sma=sma)
    for lb in [126, 189, 252]:
        out[f"GEM lookback={lb}"] = A.dual_momentum(px, ["SPY", "EFA"], "IEF", "BIL", lookback=lb)
    xs = ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "GLD", "DBC", "VNQ"]
    for top in [2, 3, 4]:
        out[f"XS mom top{top}"] = A.xs_momentum(px, xs, top=top, safe="IEF")
    out["XS mom top3, 12m only"] = A.xs_momentum(px, xs, top=3, lookbacks=(252,), safe="IEF")
    out["XS mom top3, 3-6m only"] = A.xs_momentum(px, xs, top=3, lookbacks=(63, 126), safe="IEF")
    out["XS mom top3, no QQQ"] = A.xs_momentum(px, [a for a in xs if a != "QQQ"], top=3, safe="IEF")
    out["XS mom top3, no trend filter"] = A.xs_momentum(px, xs, top=3, safe="IEF", trend_sma=None)
    for tv in [0.10, 0.15, 0.20]:
        out[f"SPY vol-target {tv:.0%}"] = A.vol_target(px, "SPY", tv)
    return out


def run(weights_by_name: dict, px: pd.DataFrame) -> pd.DataFrame:
    test_px = px.loc[START:]
    results = {name: run_portfolio(test_px, A.park_cash(w, "BIL").loc[START:], COST_BPS)
               for name, w in weights_by_name.items()}
    return stats_table(results, 252, SPLITS)


def main():
    px = load_stock_panel(UNIVERSE)
    print(f"ETF strategies {START} -> {px.index[-1].date()}  (cost {COST_BPS} bps per unit turnover)\n")
    print(fmt_table(run(strategies(px), px)))
    print("\nParameter sensitivity (a robust idea works across nearby settings):\n")
    sens = run(sensitivity(px), px)
    print(fmt_table(sens[["cagr", "sharpe", "max_dd"] + [c for c in sens.columns if c.startswith("sharpe ")]]))


if __name__ == "__main__":
    main()
