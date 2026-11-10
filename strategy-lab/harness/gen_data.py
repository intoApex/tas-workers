"""Synthetic Binance-shaped candles for offline tests of the lab. Not market data: a seeded random walk with volatility clustering."""
import sys, os, numpy as np, pandas as pd
out, days = sys.argv[1], int(sys.argv[2])
PAIRS = "BTC ETH SOL BNB XRP DOGE ADA AVAX LINK LTC DOT TRX BCH NEAR UNI ATOM ETC FIL APT ARB".split()
START = {"BTC": 100000, "ETH": 3500, "SOL": 180, "BNB": 900, "XRP": 2.4, "DOGE": 0.2, "ADA": 0.7, "AVAX": 25, "LINK": 18, "LTC": 95, "DOT": 4.5, "TRX": 0.3, "BCH": 500, "NEAR": 3, "UNI": 8, "ATOM": 5, "ETC": 20, "FIL": 3.5, "APT": 6, "ARB": 0.5}
end = pd.Timestamp.now(tz="UTC").floor("min"); n = days * 1440
idx = pd.date_range(end=end, periods=n, freq="1min")
rng = np.random.default_rng(7)
mkt = rng.standard_normal(n)
os.makedirs(out, exist_ok=True)
for p in PAIRS:
    vol = np.empty(n); v = 0.0006
    shocks = rng.standard_normal(n)
    for i in range(n):  # volatility clustering
        v = 0.97 * v + 0.03 * 0.0006 * (1 + 2.5 * abs(shocks[i - 1] if i else 0)); vol[i] = v
    drift = 0.0000015 * np.sin(np.arange(n) / (1440 * 9) + rng.uniform(0, 6))
    r = drift + vol * (0.6 * mkt + 0.8 * shocks)
    close = START[p] * np.exp(np.cumsum(r)); open_ = np.concatenate([[close[0]], close[:-1]])
    wig = np.abs(rng.standard_normal(n)) * vol * close
    df = pd.DataFrame({"date": idx, "open": open_, "high": np.maximum(open_, close) + wig, "low": np.minimum(open_, close) - wig * 0.9, "close": close,
                       "volume": np.abs(rng.standard_normal(n)) * 50 + 10})
    for tf, rule in (("1m", None), ("5m", "5min"), ("15m", "15min"), ("1h", "1h"), ("4h", "4h"), ("1d", "1D")):
        d = df if rule is None else df.set_index("date").resample(rule).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna().reset_index()
        d = d.astype({"open": "float64", "high": "float64", "low": "float64", "close": "float64", "volume": "float64"})
        d["date"] = d["date"].astype("datetime64[ms, UTC]")
        d = d.reset_index(drop=True)
        d.to_feather(os.path.join(out, f"{p}_USDT-{tf}.feather"), compression_level=9, compression="lz4")
        # the same candles again as a USDT perpetual, plus hourly mark prices and funding rates, for the leverage board
        os.makedirs(os.path.join(out, "futures"), exist_ok=True)
        d.to_feather(os.path.join(out, "futures", f"{p}_USDT_USDT-{tf}-futures.feather"), compression_level=9, compression="lz4")
        if tf == "1h":
            d.to_feather(os.path.join(out, "futures", f"{p}_USDT_USDT-1h-mark.feather"), compression_level=9, compression="lz4")
            fr = pd.DataFrame({"date": d["date"], "funding_rate": np.where(d["date"].dt.hour % 8 == 0, 0.0001, 0.0)})
            fr.to_feather(os.path.join(out, "futures", f"{p}_USDT_USDT-1h-funding_rate.feather"), compression_level=9, compression="lz4")
print("wrote", len(PAIRS), "pairs x 6 timeframes, spot and futures,", days, "days")
