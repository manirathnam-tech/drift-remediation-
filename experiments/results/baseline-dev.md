# Deterministic baseline development evaluation

Dataset: `/mnt/c/Users/Manirathnam/OneDrive/Desktop/Thesis/Thesis Implementation/repos/drift-remediation-drift52-plan/harness/dev.jsonl`. This is a synthetic development split, not observed incident performance.

- Records classified: 60 / 60 (coverage 1.000)
- Class accuracy (classified records): 0.500
- Class macro-F1: 0.389
- Severity accuracy (classified records): 0.400
- Severity macro-F1: 0.323
- Intent and remediation decisions are not scored because the baseline does not infer experiment authorization or intent.

## Class confusion matrix (rows = gold, columns = predicted)

```json
{
  "benign": {
    "benign": 0,
    "operational": 15,
    "critical": 0
  },
  "operational": {
    "benign": 0,
    "operational": 15,
    "critical": 15
  },
  "critical": {
    "benign": 0,
    "operational": 0,
    "critical": 15
  }
}
```

## Severity confusion matrix (rows = gold, columns = predicted)

```json
{
  "0": {
    "0": 0,
    "1": 3,
    "2": 12,
    "3": 0
  },
  "1": {
    "0": 0,
    "1": 6,
    "2": 3,
    "3": 6
  },
  "2": {
    "0": 0,
    "1": 3,
    "2": 3,
    "3": 9
  },
  "3": {
    "0": 0,
    "1": 0,
    "2": 0,
    "3": 15
  }
}
```

These metrics describe this ruleset against synthetic dev fixtures only. They are not empirical deployment accuracy and must not be reported as performance on observed incidents.
