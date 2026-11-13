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

## The one thing that cannot be fetched again

Everything here can be rebuilt from public sources except the daily share prices behind our own vault records (Paradex,
Orderly, Kamino, Lagoon, dHEDGE): a past day's price is gone once the day is over. They live in
`/srv/tas/data/vaults/<venue>/prices/<day>.json` and `/srv/tas/data/dhedge/prices/<day>.json`.

- **Backup.** Each scan also publishes the whole history as one file, `vaults/<venue>-prices.json` and `dhedge/prices.json`
  under https://workers.tradingagentstack.com/ (format in `common/pricebook.py`). The site's nightly "Data tracks" run copies
  them to the `vault-prices/` folder of its `perf-data` branch, and refuses a copy that has fewer days than the one it holds.
  The files cover vaults with at least a tenth of the venue's publishing minimum in them, and any vault once it is in.
- **Restore after a lost disk.** Copy the files from `vault-prices/` on the site repository's `perf-data` branch into this
  repository with a pull request: `restore/vaults/<venue>-prices.json` and `restore/dhedge/prices.json`. The server pulls
  the repo, and the next daily scan writes back every day its prices folder lacks, then computes the records as before.
  Days already on disk are never changed, so the files can stay in `restore/` afterwards; a run that happened before the
  restore does no harm.
- **Restore after a lost data folder** (the public folder survived): nothing to do, the next scan restores from the
  published file.
- Test, no network: `python3 tests/test_pricebook.py`.

## License

MIT

## Services

- `okx-copytrading` (compose.yaml, okx/snapshot.py): once a day, a snapshot of OKX's official public copy-trading endpoints (lead trader ranks, daily and weekly PnL, closed positions). Raw data stays on the server; https://workers.tradingagentstack.com/okx-status.json shows counts only.
- `strategy-lab` (compose.yaml, strategy-lab/): forward tests of public open-source strategies (first source: freqtrade/freqtrade-strategies) on Binance spot candles, scored only on data after the day each strategy was added, with a one-time lookahead-bias check. Results: https://workers.tradingagentstack.com/strategy-lab/results.json Our own baselines are in `strategy-lab/baselines/`: hold, moving-average cross, dip buying, breakout and a coin-flip control (`tas_baselines.py`), and two grid bots, two DCA bots and plain weekly buying (`tas_baselines_2.py`). `tas_tradingview_builtins.py` restates fifteen of the example strategies that ship with TradingView's Strategy Tester (MACD, RSI, Supertrend and so on) with their default settings, long only, on 1-hour candles; it is our own code written from the rules, not TradingView's, and each class says in words what it does. A strategy's file is frozen on the day it is added: it is never replaced afterwards, and its SHA-256 is published with its result. New or changed baselines go in a new file under new class names. Community collections are listed in `PINNED` in `strategy-lab/lab.py`, each pinned to one commit and fetched once; a file is admitted only if a read of its code (it is not run) shows it imports nothing beyond what a strategy needs and does not open files or run commands, and the files that were not admitted are published with the reason at https://workers.tradingagentstack.com/strategy-lab/not-admitted.json A strategy that times out or is killed three runs in a row is retired. The container can write only to its own results folder. `strategy-lab/harness/` runs the lab offline against the real Freqtrade for tests.
- `strategy-lab-futures` (compose.yaml, same `strategy-lab/lab.py` with `LAB_MODE=futures`): the leverage board. Binance USDT perpetual futures, isolated margin, $1,000 paper wallet, funding fees and liquidations included, shorting allowed. It runs the official collection's futures strategies and our own leverage ladder in `strategy-lab/baselines-futures/` (holding Bitcoin, a long-and-short trend rule and RSI dip buying, each at several leverage levels). Results: https://workers.tradingagentstack.com/strategy-lab-futures/results.json Never ranked together with the spot lab. Both labs publish `max_drawdown_marked`: the worst drop of the wallet with open positions valued every hour, next to Freqtrade's own drawdown, which only moves when a trade closes.
- `polymarket/traders.py` (deploy.sh, daily after 01:30 UTC): Polymarket's top traders, from the documented routes of Polymarket's public Data API (`/v2/leaderboard`, `/v2/user-pnl`, `/v2/user-stats`, `/v2/value`). It takes the 50 wallets with the biggest 30-day profit and works out, in dollars, each wallet's lifetime profit, the highest it has been, the most it has given back from a high, and the share of lifetime profit that came from its single biggest win. There is no percentage return: Polymarket does not publish deposits and withdrawals, so the money a wallet worked with is unknown. A wallet that drops off the list is followed for 90 more days. Results: https://workers.tradingagentstack.com/polymarket/traders.json Health: https://workers.tradingagentstack.com/polymarket/status.json Test, no network: `python3 tests/test_pm_traders.py`.
- `probes/bybit_copytrading.sh` (deploy.sh, hourly): asks Bybit's two public copy-trading lists whether they answer from this server (from GitHub's US servers they answer HTTP 403). Writes status, list lengths and field names only to https://workers.tradingagentstack.com/probe-bybit.json
- `hyperliquid/traders.py` (deploy.sh, daily after 01:15 UTC): Hyperliquid's top traders. It reads Hyperliquid's public leaderboard, takes the 50 wallets with the biggest 30-day profit among accounts holding $100,000 or more (our choice, written into the output), and works out each wallet's record from Hyperliquid's own account history: return, worst drop and Bitcoin over the same dates, with deposits and withdrawals not counted as profit. A wallet that later drops off the list is still followed for 90 days, so the list can be shown again later with the ones that fell away. Results: https://workers.tradingagentstack.com/hl/traders.json Health: https://workers.tradingagentstack.com/hl/status.json No keys; addresses and display names are Hyperliquid's public leaderboard data. Test, no network: `python3 tests/test_hl_traders.py`.
- `vaults/scan.py` (deploy.sh, daily after 00:45 UTC): vaults and automated strategies on more venues, from each venue's own public API with no keys: Paradex vaults (Starknet perps), Orderly strategy vaults, Kamino automated liquidity strategies (Solana), GRVT strategy vaults and Hibachi vaults (perp DEXs that publish each vault's share price since launch, so the record covers the whole life of the vault), Lagoon on-chain vaults ($100k+, all chains, with the curator and strategy type), and Metric pools ($50k+; a DEX on Ethereum, BNB Chain and Robinhood Chain where a curator or market maker runs each pool's pricing. A pool has no share price, so those rows carry Metric's own figures and no record of ours). Raw lists stay on the server; https://workers.tradingagentstack.com/vaults/<venue>.json has the ones with real money in them, each with the venue's own figures labelled as the venue's and our own record (return, worst drop, annualised from 30 days, last 90 days) computed from the venue's share-price history where it publishes one, otherwise from the daily share prices we save. `computed_from` says which. On-chain strategy vaults (`vaults/onchain_vaults.json`: ERC-4626 vaults with trading-type strategies on Ethereum, Base, Arbitrum and BNB Chain, from the strategy tags in Trading Strategy's open-source [web3-ethereum-defi](https://github.com/tradingstrategy-ai/web3-ethereum-defi), MIT licence) are read straight from the chain through public RPC endpoints: share price from `convertToAssets`, size from `totalAssets` priced with DefiLlama's public price API. Their record starts on the day we start saving prices, since the free public endpoints don't serve old blocks. Kamino Earn (`kamino-earn`, added 2026-11-13) is the second Solana venue: vaults where a curator spreads one token across Kamino's lending markets; the record is computed from Kamino's own daily value per share, in the vault's own token, over the last 365 days. Test, no network: `python3 tests/test_kamino_earn.py`.
