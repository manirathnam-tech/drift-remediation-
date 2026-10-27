# DRIFT-23: Argo CD detection latency

Argo CD reconciliation is configured with `timeout.reconciliation: 30s` and
`timeout.reconciliation.jitter: 0s` in `infra/platform/helm.tf`. The setting is
managed by Terraform; the live `argocd-cm` was verified to report `30s` and `0s`.

## Measurement

The harness injected a Deployment replica mutation and polled the Argo CD
Application sync status. `T_inject` came from the injector's DynamoDB ledger
record; `T_detect` was the first poll that observed the Application as
`OutOfSync`. Latency is `T_detect - T_inject`, in milliseconds. Each trial was
reset before the next one; the injector guard requires a prior `CLEAN` reset.

The final dataset contains 20 detected trials. The interrupted retry
and the trial whose reset initially failed were excluded; the latter was reset
cleanly but did not retain a detection measurement. The three replacement
trials completed with clean resets.

- Minimum: 6850 ms
- Median: 8262 ms
- Mean: 8366 ms
- Maximum: 10066 ms

See [`../harness/results/drift23-detection-final.csv`](../harness/results/drift23-detection-final.csv)
for trial records and [`../harness/results/drift23-detection-final.svg`](../harness/results/drift23-detection-final.svg)
for the plot.

These are observed results from this 20-trial run. They include polling and
client/network overhead and should not be interpreted as a universal Argo CD
reconciliation guarantee.
