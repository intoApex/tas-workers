#!/bin/bash
# Probe: can this server read OKX's official public copy-trading endpoints (no keys)?
# Writes only HTTP status, item counts and field names to /srv/tas/public/probe-okx.json.
# It never stores trader names, codes or other identifiers.
set -uo pipefail
OUT=/srv/tas/public/probe-okx.json
B=https://www.okx.com/api/v5/copytrading
UA="TradingAgentStack-workers/1.0 (+https://tradingagentstack.com/about/)"
get() { curl -sS --max-time 20 -A "$UA" -w '\n%{http_code}' "$1" 2>/dev/null; }
row() { # name url -> json summary; also prints body to fd 3
  local r body code; r=$(get "$2"); code=${r##*$'\n'}; body=${r%$'\n'*}
  printf '%s' "$body" > "/tmp/okx_$1.json"
  jq -n --arg name "$1" --arg path "${2#https://www.okx.com}" --arg http "$code" --slurpfile b <(printf '%s' "$body" | jq -c . 2>/dev/null || echo '{}') '
    ($b[0] // {}) as $j |
    ($j.data // []) as $d |
    {name:$name, path:$path, http:$http, code:($j.code // null), msg:($j.msg // null),
     items:(if ($d|type)=="array" then ($d|length) else null end),
     fields:(if ($d|type)=="array" and ($d|length)>0 then ($d[0]|keys) else [] end),
     nested:(if ($d|type)=="array" and ($d|length)>0 and ($d[0].ranks? != null) then {ranks:($d[0].ranks|length), rank_fields:(($d[0].ranks[0] // {})|keys)} else null end)}'
}
r1=$(row config "$B/public-config?instType=SWAP")
r2=$(row lead_traders "$B/public-lead-traders?instType=SWAP&sortType=overview&limit=20")
code=$(jq -r '.data[0].ranks[0].uniqueCode // empty' /tmp/okx_lead_traders.json 2>/dev/null)
if [ -n "$code" ]; then
  sleep 1; r3=$(row stats "$B/public-stats?instType=SWAP&uniqueCode=$code&lastDays=4")
  sleep 1; r4=$(row daily_pnl "$B/public-pnl?instType=SWAP&uniqueCode=$code&lastDays=4")
  sleep 1; r5=$(row weekly_pnl "$B/public-weekly-pnl?instType=SWAP&uniqueCode=$code")
  sleep 1; r6=$(row position_history "$B/public-subpositions-history?instType=SWAP&uniqueCode=$code&limit=10")
else r3='null'; r4='null'; r5='null'; r6='null'; fi
rm -f /tmp/okx_*.json
jq -n --arg at "$(date -u +%FT%TZ)" --argjson a "$r1" --argjson b "$r2" --argjson c "$r3" --argjson d "$r4" --argjson e "$r5" --argjson f "$r6" \
  '{at:$at, note:"counts and field names only; no trader identifiers stored", endpoints:[$a,$b,$c,$d,$e,$f]}' > "$OUT.tmp" && mv "$OUT.tmp" "$OUT"
