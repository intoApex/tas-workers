# tas-workers

The jobs [TradingAgentStack](https://tradingagentstack.com) runs on its own server: paper-trading bots
and snapshots of public data, such as copy-trading leaderboards. The code is public so anyone can check
how the numbers on the site are produced.

## How it runs

- The server (Vultr, Frankfurt) pulls this repo every 10 minutes and runs `deploy.sh`.
  Nothing is copied to it by hand, and nobody logs in to change it: a change here is a change there.
- Services are defined in `compose.yaml` and run under Docker.
- Results are published read-only at `https://workers.tradingagentstack.com/`:
  - `status.json`: the server's health and which exchange endpoints answer from it
  - `deploy.json`: the commit that is running and its containers

## Rules

- **No keys, ever.** No exchange API keys, tokens, passwords or wallet keys in this repo or on the server.
  Bots run in dry-run (paper) mode on public market data, so they can't place a real order.
- **Public data only**, through public or official endpoints, at a polite rate.
- Every published number keeps the date it was measured and the source it came from.

## License

MIT

## Services

- `okx-copytrading` (compose.yaml, okx/snapshot.py): once a day, a snapshot of OKX's official public copy-trading endpoints (lead trader ranks, daily and weekly PnL, closed positions). Raw data stays on the server; https://workers.tradingagentstack.com/okx-status.json shows counts only.
- `strategy-lab` (compose.yaml, strategy-lab/): forward tests of public open-source strategies (first source: freqtrade/freqtrade-strategies) on Binance spot candles, scored only on data after the day each strategy was added, with a one-time lookahead-bias check. Results: https://workers.tradingagentstack.com/strategy-lab/results.json Our own baselines are in `strategy-lab/baselines/`: hold, moving-average cross, dip buying, breakout and a coin-flip control (`tas_baselines.py`), and two grid bots, two DCA bots and plain weekly buying (`tas_baselines_2.py`). A strategy's file is frozen on the day it is added: it is never replaced afterwards, and its SHA-256 is published with its result. New or changed baselines go in a new file under new class names. Community collections are listed in `PINNED` in `strategy-lab/lab.py`, each pinned to one commit and fetched once; a file is admitted only if a read of its code (it is not run) shows it imports nothing beyond what a strategy needs and does not open files or run commands, and the files that were not admitted are published with the reason at https://workers.tradingagentstack.com/strategy-lab/not-admitted.json A strategy that times out or is killed three runs in a row is retired. The container can write only to its own results folder. `strategy-lab/harness/` runs the lab offline against the real Freqtrade for tests.
- `strategy-lab-futures` (compose.yaml, same `strategy-lab/lab.py` with `LAB_MODE=futures`): the leverage board. Binance USDT perpetual futures, isolated margin, $1,000 paper wallet, funding fees and liquidations included, shorting allowed. It runs the official collection's futures strategies and our own leverage ladder in `strategy-lab/baselines-futures/` (holding Bitcoin, a long-and-short trend rule and RSI dip buying, each at several leverage levels). Results: https://workers.tradingagentstack.com/strategy-lab-futures/results.json Never ranked together with the spot lab. Both labs publish `max_drawdown_marked`: the worst drop of the wallet with open positions valued every hour, next to Freqtrade's own drawdown, which only moves when a trade closes.
- `vaults/scan.py` (deploy.sh, daily after 00:45 UTC): vaults and automated strategies on more venues, from each venue's own public API with no keys: Paradex vaults (Starknet perps), Orderly strategy vaults, Kamino automated liquidity strategies (Solana), GRVT strategy vaults and Hibachi vaults (perp DEXs that publish each vault's share price since launch, so the record covers the whole life of the vault), Lagoon on-chain vaults ($100k+, all chains, with the curator and strategy type), and Metric pools ($50k+; a DEX on Ethereum, BNB Chain and Robinhood Chain where a curator or market maker runs each pool's pricing. A pool has no share price, so those rows carry Metric's own figures and no record of ours). Raw lists stay on the server; https://workers.tradingagentstack.com/vaults/<venue>.json has the ones with real money in them, each with the venue's own figures labelled as the venue's and our own record (return, worst drop, annualised from 30 days, last 90 days) computed from the venue's share-price history where it publishes one, otherwise from the daily share prices we save. `computed_from` says which.
