#!/bin/bash
# Probe: can this server read Bybit's public copy-trading lists (no key)?
# Bybit's official AI connector (github.com/bybit-exchange/trading-mcp) calls these two routes without signing them.
# From GitHub's servers in the US they answer HTTP 403 "block access from your country" (checked 2026-11-13), so the only
# place we can read them from is here. This writes what came back to /srv/tas/public/probe-bybit.json: HTTP status,
# Bybit's own return code and message, and for every list in the answer its length and the field names of its first row.
# It stores no trader names, marks or other identifiers.
set -uo pipefail
OUT=/srv/tas/public/probe-bybit.json
B=https://api.bybit.com
UA="TradingAgentStack-workers/1.0 (+https://tradingagentstack.com/about/)"
row() { # name path -> json summary
  local r body code; r=$(curl -sS --max-time 20 -A "$UA" -w '\n%{http_code}' "$B$2" 2>/dev/null); code=${r##*$'\n'}; body=${r%$'\n'*}
  jq -n --arg name "$1" --arg path "$2" --arg http "$code" --arg start "$(printf '%s' "$body" | head -c 160 | tr -d '\n')" \
     --slurpfile b <(printf '%s' "$body" | jq -c . 2>/dev/null || echo 'null') '
    ($b[0]) as $j |
    def lists: [paths(type == "array")] | map({key: (map(tostring) | join(".")), n: (. as $p | $j | getpath($p) | length),
                                               fields: (. as $p | $j | getpath($p) | (.[0] // null) | if type == "object" then keys else type end)}) | .[0:12];
    {name: $name, path: $path, http: $http,
     ret_code: (if ($j | type) == "object" then ($j.retCode // $j.ret_code // null) else null end),
     ret_msg: (if ($j | type) == "object" then ($j.retMsg // $j.ret_msg // null) else null end),
     result_keys: (if ($j | type) == "object" and (($j.result // null) | type) == "object" then ($j.result | keys) else null end),
     lists: (if ($j | type) == "object" then ($j | lists) else null end),
     not_json_start: (if $j == null then $start else null end)}'
}
r1=$(row classic "/v5/copy-trade/recommend-leader-list")
sleep 1
r2=$(row tradfi "/v5/copy-mt5/recommend-provider-list")
jq -n --arg at "$(date -u +%FT%TZ)" --argjson a "${r1:-null}" --argjson b "${r2:-null}" \
  '{at: $at, note: "status, list lengths and field names only; no trader identifiers stored", endpoints: [$a, $b]}' > "$OUT.tmp" && mv "$OUT.tmp" "$OUT"
