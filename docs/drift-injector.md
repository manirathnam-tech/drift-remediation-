# DRIFT-21: drift injector Lambda

## What it does

The Lambda accepts only a corpus record ID. It reads the selected record from the bundled `harness/corpus.jsonl`, verifies that the target is one of the explicitly allowlisted Online Boutique objects in `online-boutique`, reads the live object, and sends that record's JSON Patch to the Kubernetes API. The request uses Kubernetes field manager `drift-injector`; successful patches write `t_inject` (UTC and epoch milliseconds), trial ID, record labels, mutation, and trigger source to DynamoDB. One Lambda invocation runs at a time. The handler does not accept arbitrary patches from its event.

The IAM role is mapped to the Kubernetes group `drift-injector` by the EKS access entry. `infra/platform/injector-rbac.yaml` binds a namespaced `RoleBinding` in `online-boutique` to a `ClusterRole` limited to the corpus target names. It grants `get` and `patch` for those named objects; `escalate` and `bind` are limited to the single `drift-observer` Role so the critical RBAC fixtures can be exercised. These two verbs are powerful and must remain constrained to that resource name and namespace. The function runs in private VPC subnets and reaches the private EKS API; a DynamoDB gateway endpoint avoids sending ledger traffic through NAT.

## Build and deploy

Run from the repository root in WSL after committing/reviewing the frozen corpus. First add the protected ledger table to the persistent bootstrap stack. It survives `make down`:

```bash
terraform -chdir=infra/bootstrap init
terraform -chdir=infra/bootstrap plan
terraform -chdir=infra/bootstrap apply
```

Then build and plan the EKS/Lambda stack:

```bash
bash infra/injector/package.sh
cd infra
terraform init
terraform fmt -check -recursive
terraform plan
terraform apply
```

Review the Terraform plan before applying. It creates the Lambda, IAM roles/policies, a Lambda security group, an EKS API ingress rule, and a DynamoDB gateway endpoint. It also reads the protected on-demand ledger table from the bootstrap stack and adds the Lambda IAM role as a group-only EKS access entry; this is not a cluster-admin access policy. The Scheduler role is created, while the schedule itself is disabled unless `injector_schedule_enabled=true`.

After the `online-boutique` namespace exists, apply the Kubernetes RBAC manifest with an administrator identity and inspect the access-entry mapping:

```bash
kubectl apply -f infra/platform/injector-rbac.yaml
aws eks describe-access-entry --cluster-name drift-thesis-eks \
  --principal-arn "$(terraform output -raw drift_injector_role_arn)" \
  --region ap-southeast-2 --profile thesis-project
```

Then test authorization through an actual Lambda invocation. Do not grant `AmazonEKSClusterAdminPolicy` to the Lambda role.

## Manual invocation

Use a record from the frozen corpus. The supplied sample list is in `infra/injector/smoke-samples.json`.

```bash
aws lambda invoke \
  --function-name drift-thesis-injector \
  --region ap-southeast-2 \
  --profile thesis-project \
  --cli-binary-format raw-in-base64-out \
  --payload '{"instance_id":"SYN-20261022-0006","trial_id":"pilot-001","trigger":"manual"}' \
  /tmp/injector-response.json
cat /tmp/injector-response.json
```

Use a unique `trial_id` for each run. After each invocation, confirm a matching ledger row and check Argo CD:

```bash
kubectl get application online-boutique -n argocd
aws dynamodb query --table-name drift-thesis-experiment-ledger \
  --key-condition-expression 'pk = :pk' \
  --expression-attribute-values '{":pk":{"S":"DRIFT#SYN-20261022-0006"}}' \
  --region ap-southeast-2 --profile thesis-project
```

For the 10-instance acceptance check, use the records in `smoke-samples.json` one at a time. Confirm `OutOfSync` after each mutation, then restore/sync the Git baseline and confirm `Synced` before proceeding. The critical examples broaden access or network reachability; severity-2 examples can interrupt service. Run them only in the experiment window and restore each immediately after recording the result. The Lambda itself does not automatically undo the drift.

## EventBridge Scheduler

Scheduler is optional and disabled by default because the cluster must be on a clean baseline before a trial, and a recurring fixed record cannot reset the environment. For a controlled one-shot demonstration, set `injector_schedule_enabled=true`, provide a test-set ID in `injector_schedule_instance_id`, choose the desired expression, apply Terraform, wait for one invocation, then disable/remove the schedule with Terraform. Do not leave a recurring schedule enabled across trials.

## Unit tests and boundaries

Run the standard-library test suite from the repository root:

```bash
python3 -m unittest discover -s infra/injector/src -p 'test_*.py' -v
```

The tests validate record allowlisting, field manager/media type, metadata annotation handling and ledger timestamp ordering. Live acceptance still requires deploying into an active EKS cluster and independently confirming the 10 Argo CD OutOfSync transitions. The synthetic corpus is not observed drift data and its labels remain subject to supervisor review.

The DynamoDB table uses on-demand billing and deletion protection because audit history must survive nightly teardown. `make down` retains it with the rest of `infra/bootstrap`.
