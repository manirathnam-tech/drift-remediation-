#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="${AWS_PROFILE:-thesis-project}"
REGION="${AWS_REGION:-ap-southeast-2}"
START="${1:?Usage: cost-note.sh START_DATE END_DATE (END exclusive)}"
END="${2:?Usage: cost-note.sh START_DATE END_DATE (END exclusive)}"
export AWS_PROFILE="$PROFILE" AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION"
amount="$(aws ce get-cost-and-usage --time-period "Start=$START,End=$END" --granularity DAILY --metrics UnblendedCost --query 'ResultsByTime[].Total.UnblendedCost.Amount' --output text)"
currency="$(aws ce get-cost-and-usage --time-period "Start=$START,End=$END" --granularity DAILY --metrics UnblendedCost --query 'ResultsByTime[].Total.UnblendedCost.Unit' --output text)"
amount="$(printf '%s\n' "$amount" | awk '{s+=$1} END {printf "%.6f", s}')"
printf '\nCost Explorer observation for %s through %s (end exclusive): %s %s account spend. Costs are delayed and may include non-project charges; use this as a date-range observation, not exact per-session attribution.\n' "$START" "$END" "$amount" "${currency:-USD}" >> "$ROOT/docs/cost-log.md"
echo "Recorded Cost Explorer date-range total: $amount ${currency:-USD}"
