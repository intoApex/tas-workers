# AI league

Bots written by AI models from one prompt, for the Strategy Lab's leverage board. Rules, prompt and raw answers:
https://tradingagentstack.com/ai-league/

One folder per season (`preseason/`, later `season-1/`), each with:

- `league.json`: for every bot, the lab, the model's display name, the exact model ID it was called with, the model name
  and time in the answer, the class name the prompt asked for, and the file (or `null` with a `note`, when the answer had
  no Python code block).
- one `.py` file per bot: the first Python code block of the model's answer, byte for byte. These files are never edited.
  A later season gets a new folder.

How the lab runs them (`strategy-lab/lab.py`, LAB_MODE=futures):

- Each file is copied in once, checked with the same reader as every other strategy (`admit()`), and loaded on its own
  first; a file that is refused, does not load or crashes is shown as "did not run" with the reason.
- It runs through a wrapper the lab writes (`tasleague_<season>_<class>.py`): the model's class, unchanged, with leverage
  cut to the season's cap (3x). If the model named its class differently, that class runs and the page says so.
- Its record starts on the first daily run with the season's bots in it and lasts `days` (30): Binance USDT perpetual
  futures, isolated margin, $1,000 paper wallet, 5 open trades, fees and funding included, liquidation by Binance's tiers.
- It is retired at the first liquidation of any position, or the first hour its wallet (open positions at that hour's
  price) is more than 30% below its peak; its value is then the wallet at that hour.
- Results: `results.json` on the leverage board, key `league`. League bots are never ranked on the board itself.

Test season: `strategy-lab/harness/league-test/`, `python3 tests/test_league.py`.
