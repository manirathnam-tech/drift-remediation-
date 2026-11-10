#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="${AWS_PROFILE:-thesis-project}"
REGION="${AWS_REGION:-ap-southeast-2}"
START="${1:?Usage: cost-note.sh START_DATE END_DATE (END exclusive)}"
END="${2:?Usage: cost-note.sh START_DATE END_DATE (END exclusive)}"
export AWS_PROFILE="$PROFILE" AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION"
response="$(aws ce get-cost-and-usage \
  --time-period "Start=$START,End=$END" \
  --granularity DAILY \
  --metrics UnblendedCost \
  --output json)"
summary="$(python3 -c '
import json
import sys
from decimal import Decimal, InvalidOperation

data = json.load(sys.stdin)
rows = data.get("ResultsByTime", [])
if not rows:
    raise SystemExit("Cost Explorer returned no daily results")

try:
    costs = [row["Total"]["UnblendedCost"] for row in rows]
    units = {cost["Unit"] for cost in costs}
    if len(units) != 1:
        raise SystemExit("Cost Explorer returned missing or inconsistent currencies")
    amount = sum((Decimal(cost["Amount"]) for cost in costs), Decimal("0"))
except (KeyError, InvalidOperation) as exc:
    raise SystemExit("Cost Explorer returned an invalid cost value") from exc

print("{}\t{}".format(format(amount, "f"), units.pop()))
' <<< "$response")"
IFS=$'\t' read -r amount currency <<< "$summary"
[[ -n "$amount" && -n "$currency" ]] || { echo "Cost Explorer returned an incomplete total." >&2; exit 1; }

printf '\nCost Explorer observation for %s through %s (end exclusive): %s %s account-level UnblendedCost total. Costs are delayed and may include non-project charges; use this as a date-range observation, not exact per-session attribution.\n' \
  "$START" "$END" "$amount" "$currency" >> "$ROOT/docs/cost-log.md"
echo "Recorded Cost Explorer date-range total: $amount $currency"
