"""Run the lab's run_once() offline: the real freqtrade CLI, made-up candles, and only the test files in testrepo/.

  pip install freqtrade                      # in a virtualenv
  python gen_data.py /tmp/labtest/user_data/data/binance 62
  T=40 python run_lab.py /tmp/labtest        # run it several times: the never-finishing test strategy retires on the third

Nothing here reaches Binance or GitHub: ft_offline.py supplies the market list, download-data is skipped, and the two
source downloads are answered from this folder. It checks the lab's own machinery (admission, duplicates, time limit,
retirement, lookahead queue, live record), not any community strategy.
"""
import sys, os, io, stat, zipfile, importlib.util, urllib.request
here = os.path.dirname(os.path.abspath(__file__)); root = os.path.abspath(sys.argv[1])
os.makedirs(root + "/bin", exist_ok=True); shim = root + "/bin/freqtrade"
open(shim, "w").write(f'#!/bin/sh\nexec "{sys.executable}" "{here}/ft_offline.py" "$@"\n'); os.chmod(shim, os.stat(shim).st_mode | stat.S_IEXEC)
os.environ["PATH"] = root + "/bin:" + os.environ["PATH"]
spec = importlib.util.spec_from_file_location("lab", os.path.join(here, "..", "lab.py")); lab = importlib.util.module_from_spec(spec); spec.loader.exec_module(lab)
lab.UD = root + "/user_data"; lab.REG = lab.UD + "/registry.json"; lab.CFG = os.path.join(here, "..", "config.json")
lab.PUB = root + "/public/strategy-lab"; lab.BASELINES = os.path.join(here, "..", "baselines")
SHA = "0123456789abcdef0123456789abcdef01234567"
lab.PINNED = [("tas-test/pinned-a", SHA, ("strategies/", "helper/", "tests/"), ())]
lab.BT_TIMEOUT = int(os.environ.get("T", "40")); lab.LOOKAHEAD_PER_DAY = int(os.environ.get("LA", "2"))
class _Resp:
    def __init__(self, b): self.b = b
    def read(self): return self.b
def _fake(req, timeout=0):
    url = req.full_url if hasattr(req, "full_url") else req
    buf = io.BytesIO(); z = zipfile.ZipFile(buf, "w")
    if "tas-test/pinned-a" in url:  # the official collection's URL gets an empty archive
        base = os.path.join(here, "testrepo")
        for dp, dn, fn in os.walk(base):
            for f in fn: z.write(os.path.join(dp, f), os.environ.get("TOP", "pinned-a-" + SHA) + "/" + os.path.relpath(os.path.join(dp, f), base))
    z.close(); return _Resp(buf.getvalue())
urllib.request.urlopen = _fake
os.makedirs(lab.PUB, exist_ok=True)
lab.run_once()
