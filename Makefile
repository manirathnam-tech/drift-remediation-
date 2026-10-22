.PHONY: up down status check-leftovers cost-note

up:
	bash scripts/up.sh

down:
	bash scripts/down.sh

status:
	bash scripts/status.sh

check-leftovers:
	bash scripts/check-leftovers.sh

# Record the delayed Cost Explorer amount for a session date range.
# Usage: make cost-note START=2026-10-22 END=2026-10-23
cost-note:
	@test -n "$(START)" -a -n "$(END)" || (echo 'Usage: make cost-note START=YYYY-MM-DD END=YYYY-MM-DD'; exit 2)
	bash scripts/cost-note.sh "$(START)" "$(END)"

# Read-only AWS inventory after teardown; does not delete anything.

