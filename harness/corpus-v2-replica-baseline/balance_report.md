# Synthetic corpus balance report

Seed: `20261022`
Records: **300**

> Generated mutation fixtures only. These are not observed production incidents, and the taxonomy has not yet received supervisor approval. Do not present this count as an empirical corpus.

## Counts

| Dimension | Counts |
|---|---|
| Class | {'benign': 75, 'critical': 75, 'operational': 150} |
| Severity | {'0': 75, '1': 75, '2': 75, '3': 75} |
| Resource kind | {'Deployment': 60, 'NetworkPolicy': 60, 'Role': 60, 'RoleBinding': 60, 'Service': 60} |
| Intent | {'intentional-benign': 40, 'malicious': 75, 'unintended': 185} |
| Split | {'dev': 60, 'test': 240} |

## Severity × kind cross-balance

| Severity | Deployment | Service | Role | RoleBinding | NetworkPolicy |
|---:|---:|---:|---:|---:|---:|
| 0 | 15 | 15 | 15 | 15 | 15 |
| 1 | 15 | 15 | 15 | 15 | 15 |
| 2 | 15 | 15 | 15 | 15 | 15 |
| 3 | 15 | 15 | 15 | 15 | 15 |

The default generator creates exactly 15 examples in each severity × kind cell (75 per severity; 60 per kind), with a stratified 20% development and 80% test split within every cell.
