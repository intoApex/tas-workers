"""Run the real freqtrade CLI with the exchange's market list supplied locally, for tests on a machine that cannot reach Binance."""
import sys, ccxt, ccxt.async_support as ccxta
PAIRS = "BTC ETH SOL BNB XRP DOGE ADA AVAX LINK LTC DOT TRX BCH NEAR UNI ATOM ETC FIL APT ARB".split()
def _markets():
    out = []
    for b in PAIRS:
        out.append({"id": b + "USDT", "symbol": b + "/USDT", "base": b, "quote": "USDT", "baseId": b, "quoteId": "USDT", "settle": None, "settleId": None,
                    "type": "spot", "spot": True, "margin": False, "swap": False, "future": False, "option": False, "contract": False, "active": True,
                    "linear": None, "inverse": None, "contractSize": None, "expiry": None, "expiryDatetime": None, "strike": None, "optionType": None,
                    "taker": 0.001, "maker": 0.001, "percentage": True, "tierBased": False,
                    "precision": {"amount": 1e-05, "price": 1e-08, "base": 1e-08, "quote": 1e-08},
                    "limits": {"amount": {"min": 1e-05, "max": 9e9}, "price": {"min": 1e-08, "max": 1e9}, "cost": {"min": 5.0, "max": 9e9}, "leverage": {"min": None, "max": None}, "market": {"min": 0, "max": 1e9}},
                    "created": None, "info": {"status": "TRADING", "permissions": ["SPOT"]}})
    return out
async def _aload(self, reload=False, params={}):
    return self.set_markets(_markets())
def _load(self, reload=False, params={}):
    return self.set_markets(_markets())
ccxta.binance.load_markets = _aload
ccxt.binance.load_markets = _load
try:
    import ccxt.pro as ccxtp
    ccxtp.binance.load_markets = _aload
except Exception:
    pass
from freqtrade.main import main
if len(sys.argv) > 1 and sys.argv[1] == "download-data":
    print("offline harness: download-data skipped"); sys.exit(0)
main(sys.argv[1:])
