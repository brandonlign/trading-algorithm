"""Do machine-learning models help? Walk-forward test on SPY and BTC daily direction.

Models are retrained every year on all data up to that point, then predict the following year
blind, so every prediction scored here is out of sample. The question is not "is accuracy above
50%?" but "does trading on it beat buy & hold and a plain moving-average rule after costs?"

    .venv/bin/python -m research.ml_test
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from trading.backtest import run
from trading.data import load_crypto, load_stock_panel
from trading.strategies import rsi

warnings.filterwarnings("ignore")

MODELS = {
    "logistic": lambda: make_pipeline(StandardScaler(), LogisticRegression(C=0.1, max_iter=1000)),
    "random_forest": lambda: RandomForestClassifier(n_estimators=300, max_depth=4, min_samples_leaf=50,
                                                    n_jobs=-1, random_state=0),
    "gradient_boost": lambda: HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=200,
                                                             random_state=0),
}


def features(close: pd.Series, extra: pd.DataFrame | None = None) -> pd.DataFrame:
    r = close.pct_change()
    f = pd.DataFrame(index=close.index)
    for n in [1, 5, 20, 60, 120]:
        f[f"ret_{n}"] = close.pct_change(n)
    f["vol_20"] = r.rolling(20).std()
    f["vol_60"] = r.rolling(60).std()
    f["vol_ratio"] = f["vol_20"] / f["vol_60"]
    f["rsi_14"] = rsi(close, 14)
    f["dist_sma50"] = close / close.rolling(50).mean() - 1
    f["dist_sma200"] = close / close.rolling(200).mean() - 1
    f["dow"] = close.index.dayofweek
    if extra is not None:
        f = f.join(extra)
    return f


def walk_forward(close: pd.Series, feats: pd.DataFrame, horizon: int, first_test_year: int) -> pd.DataFrame:
    """Predict whether the next `horizon` days go up. Returns out-of-sample P(up) per model."""
    fwd = close.shift(-horizon) / close - 1
    data = feats.assign(y=(fwd > 0).astype(int), fwd=fwd).dropna()
    preds = pd.DataFrame(index=data.index[data.index.year >= first_test_year])
    for year in sorted(set(preds.index.year)):
        # last `horizon` training rows overlap the test year's outcome, so drop them (no leakage)
        train = data[data.index.year < year].iloc[:-horizon]
        test = data[data.index.year == year]
        X_cols = [c for c in data.columns if c not in ("y", "fwd")]
        for name, make in MODELS.items():
            model = make().fit(train[X_cols], train["y"])
            preds.loc[test.index, name] = model.predict_proba(test[X_cols])[:, 1]
    preds["y"] = data["y"]
    return preds


def evaluate(asset: str, close: pd.Series, feats: pd.DataFrame, first_test_year: int,
             fee_bps: float, ppy: int):
    print(f"\n=== {asset}: out-of-sample {first_test_year} -> {close.index[-1].date()} ===")
    test_close = close[close.index.year >= first_test_year]
    rows = []

    def add(name, target, extra=""):
        s = run(test_close, target.reindex(test_close.index).fillna(0), fee_bps, 2, ppy).stats
        rows.append({"strategy": name, "cagr": s["cagr"], "sharpe": s["sharpe"], "max_dd": s["max_dd"],
                     "trades": s["trades"], "exposure": s["exposure"], "note": extra})

    add("buy & hold", pd.Series(1.0, index=test_close.index))
    add("SMA 200 trend (no ML)", (close > close.rolling(200).mean()).astype(float))
    for horizon in [1, 5, 20]:
        preds = walk_forward(close, feats, horizon, first_test_year)
        for model in MODELS:
            p = preds[model]
            auc = roc_auc_score(preds["y"], p)
            acc = ((p > 0.5) == preds["y"]).mean()
            base = preds["y"].mean()
            note = f"acc {acc:.1%} vs always-up {base:.1%}, AUC {auc:.3f}"
            add(f"{model} h={horizon}d", (p > 0.5).astype(float), note)
    df = pd.DataFrame(rows)
    for c in ["cagr", "max_dd"]:
        df[c] = df[c].map(lambda x: f"{x:+.1%}")
    df["sharpe"] = df["sharpe"].map(lambda x: f"{x:.2f}")
    df["exposure"] = df["exposure"].map(lambda x: f"{x:.0%}")
    print(df.to_string(index=False))


def main():
    px = load_stock_panel(["SPY", "^VIX", "TLT"])
    spy = px["SPY"].dropna()
    extra = pd.DataFrame({"vix": px["^VIX"], "vix_chg5": px["^VIX"].pct_change(5),
                          "tlt_ret20": px["TLT"].pct_change(20)})
    evaluate("SPY", spy, features(spy, extra), first_test_year=2010, fee_bps=0, ppy=252)

    btc = load_crypto("BTC/USD", years=8)
    btc = btc[btc.index.date < pd.Timestamp.utcnow().date()]
    evaluate("BTC", btc, features(btc), first_test_year=2022, fee_bps=40, ppy=365)


if __name__ == "__main__":
    main()
