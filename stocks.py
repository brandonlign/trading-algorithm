"""Backtest the strategy set on stocks/ETFs with daily Yahoo Finance data.

    .venv/bin/python stocks.py                 # SPY QQQ AAPL NVDA
    .venv/bin/python stocks.py TSLA MSFT --years 15
"""
import argparse

from trading.backtest import evaluate, format_report
from trading.data import load_stock


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("tickers", nargs="*", default=["SPY", "QQQ", "AAPL", "NVDA"])
    p.add_argument("--years", type=int, default=10)
    p.add_argument("--fee-bps", type=float, default=0.0, help="commission per trade (most US brokers: 0)")
    p.add_argument("--slippage-bps", type=float, default=3.0, help="spread + price impact per trade")
    args = p.parse_args()

    for ticker in args.tickers:
        close = load_stock(ticker, args.years)
        report = evaluate(close, args.fee_bps, args.slippage_bps, periods_per_year=252)
        print(format_report(report, f"{ticker}  ({len(close)} days)"))


if __name__ == "__main__":
    main()
