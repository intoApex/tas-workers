"""What is inside a strategy, read from its source file without running it.

The Strategy Lab publishes what each bot did. This adds what each bot is: the candles it reads, the indicators its code
calls, how it leaves a trade and where its stop sits. Everything here comes from parsing the frozen file (Python's ast
module). The file is never imported or executed, so a strategy cannot influence what is written about it beyond what its
own code says. A fact that cannot be read as a plain value (a stop-loss computed at run time, say) is left out, never guessed.

describe(source, class_name) -> dict with the facts and one plain sentence built only from those facts.
"""
import ast
import re

# function name (lower case, leading underscores dropped) -> the name a reader knows
IND = {
    "rsi": "RSI", "stochrsi": "Stochastic RSI", "stoch": "Stochastic", "stochf": "Stochastic", "ema": "EMA", "sma": "SMA",
    "tema": "TEMA", "dema": "DEMA", "wma": "WMA", "kama": "KAMA", "hma": "Hull MA", "zema": "ZEMA", "vwma": "VWMA",
    "smma": "SMMA", "rma": "RMA", "t3": "T3", "macd": "MACD", "macdext": "MACD", "macdfix": "MACD", "ppo": "PPO",
    "bbands": "Bollinger Bands", "bollinger_bands": "Bollinger Bands", "weighted_bollinger_bands": "Bollinger Bands",
    "adx": "ADX", "adxr": "ADX", "plus_di": "Directional Index", "minus_di": "Directional Index", "dmi": "Directional Index",
    "mfi": "Money Flow Index", "cci": "CCI", "atr": "ATR", "natr": "ATR", "sar": "Parabolic SAR", "obv": "On-Balance Volume",
    "roc": "Rate of Change", "rocr": "Rate of Change", "mom": "Momentum", "willr": "Williams %R", "cmf": "Chaikin Money Flow",
    "chaikin_money_flow": "Chaikin Money Flow", "vwap": "VWAP", "rolling_vwap": "VWAP", "vwapb": "VWAP",
    "ichimoku": "Ichimoku", "supertrend": "Supertrend", "heikinashi": "Heikin Ashi", "ewo": "Elliott Wave Oscillator",
    "fisher": "Fisher Transform", "fishertransform": "Fisher Transform", "aroon": "Aroon", "aroonosc": "Aroon",
    "trix": "TRIX", "ultosc": "Ultimate Oscillator", "uo": "Ultimate Oscillator", "ao": "Awesome Oscillator",
    "awesome_oscillator": "Awesome Oscillator", "keltner_channel": "Keltner Channel", "kc": "Keltner Channel",
    "donchian": "Donchian Channel", "linearreg": "Linear Regression", "linearreg_slope": "Linear Regression",
    "tsf": "Time Series Forecast", "ht_trendline": "Hilbert Transform", "ht_sine": "Hilbert Transform", "cti": "CTI",
    "cmo": "Chande Momentum", "pivots_points": "Pivot Points", "pmax": "PMAX", "ssl_channels": "SSL Channels",
    "sslchannels": "SSL Channels", "ssl_atr": "SSL Channels", "wavetrend": "WaveTrend", "tke": "TKE", "vfi": "Volume Flow",
    "mama": "MAMA", "stc": "Schaff Trend Cycle", "chopiness": "Choppiness Index", "chop": "Choppiness Index",
    "tsi": "True Strength Index", "rmi": "Relative Momentum Index", "laguerre": "Laguerre RSI", "td_sequential": "TD Sequential",
    "bop": "Balance of Power", "ad": "Accumulation/Distribution", "adosc": "Accumulation/Distribution",
}
PARAMS = {"IntParameter", "DecimalParameter", "RealParameter", "CategoricalParameter", "BooleanParameter"}
TF = re.compile(r"^\d+[mhdwM]$")
TF_WORD = {"m": "minute", "h": "hour", "d": "day", "w": "week", "M": "month"}
SHOWN = 5  # indicators named in the sentence before "and N more"


def _lit(node):
    """The plain value a node spells out, or None. Never evaluates code."""
    try:
        return ast.literal_eval(node)
    except Exception:
        return None


def _param_default(node):
    """default= of IntParameter(...)-style calls, when it is a plain value."""
    if isinstance(node, ast.Call) and _fname(node.func) in PARAMS:
        for k in node.keywords:
            if k.arg == "default":
                return _lit(k.value)
    return None


def _fname(f):
    if isinstance(f, ast.Attribute): return f.attr
    if isinstance(f, ast.Name): return f.id
    return ""


def _classes(tree, name):
    """The class and the base classes it inherits from that live in the same file, child first."""
    by = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    out, seen, todo = [], set(), [name]
    while todo:
        c = todo.pop(0)
        if c in seen or c not in by: continue
        seen.add(c); out.append(by[c])
        todo += [_fname(b) for b in by[c].bases]
    return out


def _attrs(classes):
    """Class-level assignments, the child's value winning over a base class's."""
    a = {}
    for c in reversed(classes):
        for n in c.body:
            tgt, val = None, None
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name): tgt, val = n.targets[0].id, n.value
            elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.value is not None: tgt, val = n.target.id, n.value
            if tgt: a[tgt] = val
    return a


def _methods(classes):
    m = {}
    for c in reversed(classes):
        for n in c.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)): m[n.name] = n
    return m


def _tf_words(tf):
    n, u = tf[:-1], TF_WORD.get(tf[-1])
    if not u or not n.isdigit(): return tf
    if tf == "1d": return "daily"
    if tf == "1w": return "weekly"
    return f"{n}-{u}"


def _pct(x):
    v = abs(x) * 100
    return (f"{v:.1f}".rstrip("0").rstrip(".") if v < 10 else f"{v:.0f}") + "%"


def _join(xs):
    xs = list(xs)
    if len(xs) <= 1: return "".join(xs)
    return ", ".join(xs[:-1]) + " and " + xs[-1]


def _sets_exit(fn):
    """True when the exit method writes an exit column (exit_long, exit_short or the older sell) with something other
    than a plain 0. Many strategies keep the method only to switch the signal off: dataframe['exit_long'] = 0."""
    cols = ("exit_long", "exit_short", "sell")
    def names(node): return any(isinstance(n, ast.Constant) and n.value in cols for n in ast.walk(node))
    hits = [n for n in ast.walk(fn) if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign))
            and any(names(t) for t in (n.targets if isinstance(n, ast.Assign) else [n.target]))]
    if hits: return any(_lit(n.value) not in (0, False) or _lit(n.value) is None for n in hits)
    return names(fn)  # written some other way (a helper call, say): the column is at least named


MAX_CHARS = 1_500_000  # the lab's admission check already parses every admitted file; this is a second fence
_last = [None, None]   # one file often holds many strategies: parse it once


def _tree(source):
    if _last[0] != source:
        _last[0], _last[1] = source, ast.parse(source)
    return _last[1]


def describe(source, class_name):
    if len(source) > MAX_CHARS: return {"error": "file too large to read"}
    try:
        tree = _tree(source)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as e:
        return {"error": "source could not be parsed: " + type(e).__name__}
    classes = _classes(tree, class_name)
    if not classes: return {"error": "class not found in its file"}
    attrs, methods = _attrs(classes), _methods(classes)

    def val(*names):
        for k in names:
            if k in attrs:
                v = _lit(attrs[k])
                if v is None: v = _param_default(attrs[k])
                if v is not None: return v
        return None

    d = {}
    tf = val("timeframe", "ticker_interval")
    if isinstance(tf, str) and TF.match(tf): d["timeframe"] = tf
    # other candle sizes: @informative('1h') decorators and class attributes spelling a timeframe
    other = []
    for fn in methods.values():
        for dec in fn.decorator_list:
            if isinstance(dec, ast.Call) and _fname(dec.func) == "informative" and dec.args:
                v = _lit(dec.args[0])
                if isinstance(v, str) and TF.match(v) and v not in other: other.append(v)
    for k, node in attrs.items():
        v = _lit(node)
        if k not in ("timeframe", "ticker_interval") and isinstance(v, str) and TF.match(v) and v not in other: other.append(v)
    other = [t for t in other if t != d.get("timeframe")]
    if other: d["other_timeframes"] = other

    # indicators: every call whose function name is one we know, in the class, its bases and the file's helper functions
    inds, used_helpers = [], set()
    helpers = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    def scan(node):
        for n in ast.walk(node):
            if not isinstance(n, ast.Call): continue
            raw = _fname(n.func)
            key = raw.lstrip("_").lower()
            lab = IND.get(key) or ("Candlestick patterns" if key.startswith("cdl") else None)
            if lab and lab not in inds: inds.append(lab)
            if isinstance(n.func, ast.Name) and raw in helpers and raw not in used_helpers:
                used_helpers.add(raw); scan(helpers[raw])
    for c in classes: scan(c)
    d["indicators"] = inds

    sl = val("stoploss")
    if isinstance(sl, (int, float)) and not isinstance(sl, bool): d["stoploss"] = float(sl)
    d["custom_stoploss"] = bool(val("use_custom_stoploss")) and "custom_stoploss" in methods
    d["trailing_stop"] = bool(val("trailing_stop"))
    roi = val("minimal_roi")
    if isinstance(roi, dict) and roi:
        try:
            pts = sorted((float(k), float(v)) for k, v in roi.items())
            first, last = pts[0][1], pts[-1][1]
            # a first target of 1000% or more is the usual way to switch the profit target off
            d["roi"] = {"off": True} if first >= 10 else {"first": first, "last": last, "steps": len(pts)}
        except (TypeError, ValueError):
            pass
    ues = val("use_exit_signal", "use_sell_signal")
    exit_fn = methods.get("populate_exit_trend") or methods.get("populate_sell_trend")
    d["exit_signal"] = bool(exit_fn is not None and _sets_exit(exit_fn) and ues is not False)
    d["custom_exit"] = any(k in methods for k in ("custom_exit", "custom_sell"))
    d["can_short"] = bool(val("can_short"))
    d["adjusts_position"] = bool(val("position_adjustment_enable")) and "adjust_trade_position" in methods
    mx = val("max_entry_position_adjustment")
    if d["adjusts_position"] and isinstance(mx, int) and not isinstance(mx, bool) and mx > 0: d["max_extra_buys"] = mx
    d["tunable_parameters"] = sum(1 for node in attrs.values() if isinstance(node, ast.Call) and _fname(node.func) in PARAMS)
    d["protections"] = "protections" in attrs or "protections" in methods
    d["machine_learning"] = any(isinstance(n, ast.Attribute) and n.attr == "freqai" for c in classes for n in ast.walk(c))
    d["lines"] = sum((c.end_lineno or c.lineno) - c.lineno + 1 for c in classes)
    d["sentence"] = sentence(d)
    return d


def sentence(d):
    """One plain description, built only from the facts in d."""
    s = []
    tf = d.get("timeframe")
    head = f"Reads {_tf_words(tf)} candles" if tf else "Reads candles"
    if d.get("other_timeframes"): head += f" (and {_join(_tf_words(t) for t in d['other_timeframes'])} ones)"
    inds = d.get("indicators") or []
    if inds:
        shown = inds[:SHOWN]
        more = len(inds) - len(shown)
        head += " with " + (_join(shown) if not more else ", ".join(shown) + f" and {more} more")
    else:
        head += " with its own maths, no standard indicator"
    s.append(head + ".")
    ways = []
    if d.get("exit_signal"): ways.append("its own sell signal")
    if d.get("custom_exit"): ways.append("a custom exit rule")
    roi = d.get("roi") or {}
    if roi and not roi.get("off"):
        t = f"a profit target of {_pct(roi['first'])}"
        if roi["steps"] > 1 and roi["last"] < roi["first"]: t += f" that falls to {_pct(roi['last'])} the longer it holds"
        ways.append(t)
    sl = d.get("stoploss")
    no_stop = (sl is None or abs(sl) >= 0.9) and not d.get("custom_stoploss")
    if ways: s.append("Sells on " + _join(ways) + ".")
    elif d.get("adjusts_position"): s.append("Has no sell signal or profit target of its own.")
    elif no_stop:
        s.append("Has no sell signal, no profit target and no working stop-loss, so it keeps what it buys.")
        return " ".join(s + (["Can bet on prices falling."] if d.get("can_short") else []))
    else: s.append("Has no sell signal and no profit target, so a trade ends at the stop.")
    if sl is not None:
        if abs(sl) >= 0.9: stop = f"The stop-loss is set at {_pct(sl)}, so in practice there is none"
        else: stop = f"Stop-loss at a {_pct(sl)} loss"
        extra = [x for x, on in (("moved by its own rule", d.get("custom_stoploss")), ("trailing behind the price", d.get("trailing_stop"))) if on]
        if extra: stop += ", " + _join(extra)
        s.append(stop + ".")
    elif d.get("custom_stoploss"):
        s.append("The stop-loss follows its own rule.")
    if d.get("adjusts_position"):
        n = d.get("max_extra_buys")
        s.append("Changes the size of an open trade as it goes" + (f" (up to {n} extra buys)" if n else "") + ".")
    if d.get("can_short"): s.append("Can bet on prices falling.")
    if d.get("machine_learning"): s.append("Uses a machine-learning model (FreqAI).")
    return " ".join(s)
