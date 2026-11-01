.PHONY: up down status check-leftovers cost-note
.DEFAULT_GOAL := up

.PHONY: treatment-start treatment-stop treatment-status policy-test
AWS_PROFILE ?= thesis-project
AWS_REGION ?= ap-southeast-2
TREATMENT_CLUSTER := drift-thesis-treatment
TREATMENT_SERVICE := drift-thesis-treatment-worker

# Explicit operator actions; never called by local checks or ordinary make up.
# Terraform retains a default desired count of zero for the next apply.
treatment-start:
	aws ecs update-service --profile "$(AWS_PROFILE)" --region "$(AWS_REGION)" --cluster "$(TREATMENT_CLUSTER)" --service "$(TREATMENT_SERVICE)" --desired-count 1 --query 'service.{desired:desiredCount,running:runningCount}'

treatment-stop:
	aws ecs update-service --profile "$(AWS_PROFILE)" --region "$(AWS_REGION)" --cluster "$(TREATMENT_CLUSTER)" --service "$(TREATMENT_SERVICE)" --desired-count 0 --query 'service.{desired:desiredCount,running:runningCount}'

treatment-status:
	aws ecs describe-services --profile "$(AWS_PROFILE)" --region "$(AWS_REGION)" --cluster "$(TREATMENT_CLUSTER)" --services "$(TREATMENT_SERVICE)" --query '{services:services[].{desired:desiredCount,running:runningCount,pending:pendingCount},failures:failures}'

policy-test:
	docker run --rm --network none --entrypoint conftest drift-treatment:local verify --policy /app/validator/policy

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

