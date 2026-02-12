#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

BURN_IN_SECONDS="${1:-120}"
TIMESTAMP_UTC="$(date -u +"%Y%m%dT%H%M%SZ")"
START_UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
OUTPUT_DIR="${ORION_BURNIN_OUTPUT_DIR:-$ROOT_DIR/.artifacts/burnin/$TIMESTAMP_UTC}"
SERVICES=(
  "feature_enrichment"
  "execution"
  "position-monitor"
  "eod-agent"
  "indexer"
  "mcp-server"
)

mkdir -p "$OUTPUT_DIR"

echo "Starting Orion burn-in run"
echo "Output directory: $OUTPUT_DIR"
echo "Burn-in seconds: $BURN_IN_SECONDS"
echo "Start time (UTC): $START_UTC"

docker compose up -d --build --force-recreate "${SERVICES[@]}"

echo "Services started. Monitoring logs for ${BURN_IN_SECONDS}s..."
sleep "$BURN_IN_SECONDS"

docker compose logs --no-color --since "$START_UTC" > "$OUTPUT_DIR/compose.log" || true
for service in "${SERVICES[@]}"; do
  docker compose logs --no-color --since "$START_UTC" "$service" > "$OUTPUT_DIR/${service}.log" || true
done

hard_error_patterns=(
  "Traceback"
  "heber_read_failed"
  "Parquet magic bytes not found"
  "sec_health_check_unexpected"
  "503 Service Unavailable"
  "Failed to fetch positions"
)

redundant_poll_pattern="HTTP Request: GET https://api\\.unusualwhales\\.com"
hard_error_count=0

count_matches() {
  local pattern="$1"
  local target_file="$2"
  # rg returns exit code 1 when no lines match; treat that as a zero count.
  (rg -n "$pattern" "$target_file" || true) | wc -l | tr -d ' '
}

for pattern in "${hard_error_patterns[@]}"; do
  count=$(count_matches "$pattern" "$OUTPUT_DIR/compose.log")
  hard_error_count=$((hard_error_count + count))
done

redundant_poll_count=$(count_matches "$redundant_poll_pattern" "$OUTPUT_DIR/feature_enrichment.log")

echo ""
echo "Burn-in summary"
echo "Hard error hits: $hard_error_count"
echo "Feature enrichment redundant UW poll hits: $redundant_poll_count"

if [[ "$redundant_poll_count" -gt 0 ]]; then
  echo "FAIL: Feature enrichment is still directly polling Unusual Whales."
  echo "Check: $OUTPUT_DIR/feature_enrichment.log"
  exit 2
fi

if [[ "$hard_error_count" -gt 0 ]]; then
  echo "FAIL: Hard errors found during burn-in."
  echo "Check: $OUTPUT_DIR/compose.log"
  exit 1
fi

echo "PASS: No hard errors and no redundant UW polling detected."
echo "Logs saved in: $OUTPUT_DIR"
