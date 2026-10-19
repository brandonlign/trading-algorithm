"""Backtest the strategy set on crypto with daily candles from a public exchange API.

    .venv/bin/python crypto.py                          # BTC/USD ETH/USD SOL/USD on Coinbase
    .venv/bin/python crypto.py DOGE/USD --exchange kraken
"""
import argparse

from trading.backtest import evaluate, format_report
from trading.data import load_crypto


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("symbols", nargs="*", default=["BTC/USD", "ETH/USD", "SOL/USD"])
    p.add_argument("--exchange", default="coinbase", help="any ccxt exchange id (coinbase, kraken, ...)")
    p.add_argument("--years", type=int, default=5)
    p.add_argument("--fee-bps", type=float, default=40.0, help="taker fee; Coinbase/Kraken retail is ~25-60 bps")
    p.add_argument("--slippage-bps", type=float, default=5.0)
    args = p.parse_args()

    for symbol in args.symbols:
        close = load_crypto(symbol, args.exchange, years=args.years)
        report = evaluate(close, args.fee_bps, args.slippage_bps, periods_per_year=365)
        print(format_report(report, f"{symbol} on {args.exchange}  ({len(close)} days)"))


if __name__ == "__main__":
    main()
