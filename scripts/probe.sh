#!/usr/bin/env bash
# High-precision prober running inside a node namespace.
set -euo pipefail

OUT_FILE="${1:-/tmp/probe.log}"
DST_IP="${2:-10.255.0.2}"
SRC_IP="${3:-10.255.0.1}"

: > "$OUT_FILE"

while true; do
  ts=$(date +%s%3N)
  dev=$(ip route get "$DST_IP" 2>/dev/null | grep -o 'dev [^ ]*' | head -n1 | cut -d' ' -f2 || true)
  if [ -z "$dev" ]; then dev="none"; fi
  if ping -c 1 -W 0.1 -I "$SRC_IP" "$DST_IP" >/dev/null 2>&1; then
    ok=1
  else
    ok=0
  fi
  echo "$ts $dev $ok" >> "$OUT_FILE"
  sleep 0.02
done
