#!/bin/bash
# Runs on the TAS workers server every 10 minutes, as the `tas` user, after the server pulls this repo
# (see the server's /usr/local/bin/tas-deploy). Must be safe to run again and again.
#
# Rules for everything in this repo:
#   - No API keys, tokens, passwords or wallet keys. Bots run in dry-run (paper) mode on public market data.
#   - Public data only, through public or official endpoints, at a polite rate.
#   - Output goes to /srv/tas/public (served read-only at https://workers.tradingagentstack.com/)
#     or /srv/tas/data (kept on the server, not served).
set -uo pipefail
cd "$(dirname "$0")"

PUBLIC=/srv/tas/public
DATA=/srv/tas/data
mkdir -p "$PUBLIC" "$DATA"

# Services: anything defined in compose.yaml runs under Docker with restart policies.
if [ -f compose.yaml ] && docker compose config --services 2>/dev/null | grep -q .; then
  docker compose up -d --build --remove-orphans --quiet-pull >/dev/null 2>&1 \
    && compose_state="up" || compose_state="failed"
else
  compose_state="no services yet"
fi

# Probes: one-off checks of public endpoints, run at most once an hour each (results in $PUBLIC/probe-*.json).
for probe in probes/*.sh; do
  [ -x "$probe" ] || continue
  stamp="$DATA/.$(basename "$probe").last"
  if [ ! -f "$stamp" ] || [ -n "$(find "$stamp" -mmin +59 2>/dev/null)" ]; then
    timeout 120 "$probe" >/dev/null 2>&1; touch "$stamp"
  fi
done

# Derived public files (hourly): OKX lead-trader records from the latest snapshot.
stamp="$DATA/.okx_metrics.last"
if [ ! -f "$stamp" ] || [ -n "$(find "$stamp" -mmin +59 2>/dev/null)" ] || [ okx/metrics.py -nt "$stamp" ] || [ -n "$(find "$DATA/okx" -name detail.json.gz -newer "$stamp" 2>/dev/null | head -1)" ]; then
  timeout 300 python3 okx/metrics.py >/dev/null 2>"$DATA/okx_metrics.err"; touch "$stamp"
fi

# dHEDGE vaults (daily, after 00:30 UTC): raw snapshot kept on the server, public summary in $PUBLIC/dhedge/vaults.json.
day="$(date -u +%F)"
if [ "$(date -u +%H%M)" -ge 0030 ] && { [ "$(cat "$DATA/.dhedge.day" 2>/dev/null)" != "$day" ] || [ dhedge/snapshot.py -nt "$DATA/.dhedge.day" ]; }; then
  timeout 300 python3 dhedge/snapshot.py >"$DATA/dhedge.log" 2>&1 && echo "$day" > "$DATA/.dhedge.day"
fi

# Vaults on more venues (daily, after 00:45 UTC): Paradex, Orderly and Kamino from their own public APIs.
# Raw lists stay on the server; public summaries in $PUBLIC/vaults/<venue>.json, health in $PUBLIC/vaults/status.json.
if [ "$(date -u +%H%M)" -ge 0045 ] && { [ "$(cat "$DATA/.vaults.day" 2>/dev/null)" != "$day" ] || [ vaults/scan.py -nt "$DATA/.vaults.day" ]; }; then
  timeout 900 python3 vaults/scan.py >"$DATA/vaults.log" 2>&1; echo "$day" > "$DATA/.vaults.day"
fi

# Hyperliquid top traders (daily, after 01:15 UTC): the leaderboard's biggest 30-day winners, each wallet's record worked out
# from Hyperliquid's own account history, and the wallets we have watched since they first made the list.
# Public summary in $PUBLIC/hl/traders.json, health in $PUBLIC/hl/status.json. The day is stamped whether or not the run
# worked, so a bad day is tried again tomorrow (or as soon as the script changes), not every ten minutes.
if [ "$(date -u +%H%M)" -ge 0115 ] && { [ "$(cat "$DATA/.hltraders.day" 2>/dev/null)" != "$day" ] || [ hyperliquid/traders.py -nt "$DATA/.hltraders.day" ]; }; then
  timeout 900 python3 hyperliquid/traders.py >"$DATA/hltraders.log" 2>&1; echo "$day" > "$DATA/.hltraders.day"
fi

# Polymarket top traders (daily, after 01:30 UTC): the leaderboard's biggest 30-day winners, each wallet's lifetime profit day
# by day from Polymarket's own Data API, and the wallets we have watched since they first made the list.
# Public summary in $PUBLIC/polymarket/traders.json, health in $PUBLIC/polymarket/status.json. Stamped whether or not it worked.
if [ "$(date -u +%H%M)" -ge 0130 ] && { [ "$(cat "$DATA/.pmtraders.day" 2>/dev/null)" != "$day" ] || [ polymarket/traders.py -nt "$DATA/.pmtraders.day" ]; }; then
  timeout 900 python3 polymarket/traders.py >"$DATA/pmtraders.log" 2>&1; echo "$day" > "$DATA/.pmtraders.day"
fi

# What is deployed (read by the TAS site and the weekly scorecard).
jq -n \
  --arg at "$(date -u +%FT%TZ)" \
  --arg commit "$(git rev-parse --short HEAD)" \
  --arg compose "$compose_state" \
  --argjson running "$(docker ps --format '{{.Names}}' 2>/dev/null | jq -R . | jq -s . || echo '[]')" \
  '{at:$at, commit:$commit, compose:$compose, containers:$running}' > "$PUBLIC/deploy.json.tmp" \
  && mv "$PUBLIC/deploy.json.tmp" "$PUBLIC/deploy.json"
