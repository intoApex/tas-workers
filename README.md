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
- `strategy-lab` (compose.yaml, strategy-lab/): forward tests of public open-source strategies (first source: freqtrade/freqtrade-strategies) on Binance spot candles, scored only on data after the day each strategy was added, with a one-time lookahead-bias check. Results: https://workers.tradingagentstack.com/strategy-lab/results.json
- `vaults/scan.py` (deploy.sh, daily after 00:45 UTC): vaults and automated strategies on more venues, from each venue's own public API with no keys: Paradex vaults (Starknet perps), Orderly strategy vaults, and Kamino automated liquidity strategies (Solana). Raw lists stay on the server; https://workers.tradingagentstack.com/vaults/<venue>.json has the ones with real money in them, each with the venue's own figures labelled as the venue's and our own record from the daily share prices we save.
