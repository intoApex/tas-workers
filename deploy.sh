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

# What is deployed (read by the TAS site and the weekly scorecard).
jq -n \
  --arg at "$(date -u +%FT%TZ)" \
  --arg commit "$(git rev-parse --short HEAD)" \
  --arg compose "$compose_state" \
  --argjson running "$(docker ps --format '{{.Names}}' 2>/dev/null | jq -R . | jq -s . || echo '[]')" \
  '{at:$at, commit:$commit, compose:$compose, containers:$running}' > "$PUBLIC/deploy.json.tmp" \
  && mv "$PUBLIC/deploy.json.tmp" "$PUBLIC/deploy.json"
