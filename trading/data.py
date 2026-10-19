"""Daily close-price loaders for stocks (Yahoo Finance) and crypto (ccxt), with a small CSV cache."""
from __future__ import annotations

import time
import warnings
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore", message=".*OpenSSL.*")

CACHE_DIR = Path(__file__).resolve().parent.parent / "data"
CACHE_MAX_AGE_SECONDS = 6 * 3600


def _cached(name: str, fetch, fresh: bool) -> pd.Series:
    CACHE_DIR.mkdir(exist_ok=True)
    path = CACHE_DIR / f"{name}.csv"
    if not fresh and path.exists() and time.time() - path.stat().st_mtime < CACHE_MAX_AGE_SECONDS:
        return pd.read_csv(path, index_col=0, parse_dates=True)["close"]
    close = fetch()
    close.to_frame("close").to_csv(path)
    return close


def load_stock(ticker: str, years: int = 10, fresh: bool = False) -> pd.Series:
    """Split/dividend-adjusted daily closes. The last row may be today's still-moving price."""
    import yfinance as yf

    def fetch() -> pd.Series:
        df = yf.download(ticker, period=f"{years}y", interval="1d", auto_adjust=True, progress=False)
        if df.empty:
            raise ValueError(f"No Yahoo data for {ticker!r}")
        close = df["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        close.index = pd.to_datetime(close.index).tz_localize(None)
        return close.dropna().rename("close")

    return _cached(f"stock_{ticker}_{years}y", fetch, fresh)


def load_stock_panel(tickers: list[str], start: str = "2000-01-01", fresh: bool = False) -> pd.DataFrame:
    """Adjusted daily closes for many tickers, one column each (NaN before a ticker existed)."""
    import yfinance as yf

    CACHE_DIR.mkdir(exist_ok=True)
    path = CACHE_DIR / f"panel_{'-'.join(sorted(tickers))}_{start}.csv"
    if not fresh and path.exists() and time.time() - path.stat().st_mtime < CACHE_MAX_AGE_SECONDS:
        return pd.read_csv(path, index_col=0, parse_dates=True)
    df = yf.download(tickers, start=start, interval="1d", auto_adjust=True, progress=False)["Close"]
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df = df[tickers].dropna(how="all")
    df.to_csv(path)
    return df


def load_crypto_panel(symbols: list[str], exchange: str = "coinbase", years: int = 8,
                      fresh: bool = False) -> pd.DataFrame:
    """Daily closes for many coins, one column each, keyed by base currency (BTC, ETH, ...)."""
    cols = {}
    for s in symbols:
        try:
            cols[s.split("/")[0]] = load_crypto(s, exchange, years=years, fresh=fresh)
        except Exception as err:  # delisted or unknown pair: skip it rather than fail the panel
            print(f"skipping {s}: {err}")
    return pd.DataFrame(cols).sort_index()


def load_crypto(symbol: str, exchange: str = "coinbase", timeframe: str = "1d",
                years: int = 5, fresh: bool = False) -> pd.Series:
    """Daily closes from a public exchange API (no keys needed). The last row is the still-open candle."""
    import ccxt

    def fetch() -> pd.Series:
        ex = getattr(ccxt, exchange)({"enableRateLimit": True})
        step = ex.parse_timeframe(timeframe) * 1000
        since = ex.milliseconds() - years * 365 * 86_400_000
        rows: list = []
        while since <= ex.milliseconds():
            batch = ex.fetch_ohlcv(symbol, timeframe, since=since, limit=300)
            batch = [r for r in batch if not rows or r[0] > rows[-1][0]]
            if batch:
                rows += batch
                since = rows[-1][0] + step
            else:  # before listing, or a delisting gap: jump ahead instead of stopping
                since += 300 * step
        if not rows:
            raise ValueError(f"No {exchange} data for {symbol!r}")
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
        idx = pd.to_datetime(df["ts"], unit="ms")
        return pd.Series(df["close"].values, index=idx, name="close")

    return _cached(f"crypto_{exchange}_{symbol.replace('/', '-')}_{timeframe}_{years}y", fetch, fresh)
