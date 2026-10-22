# DRIFT-20 synthetic corpus harness

Run from the repository root:

```bash
python3 -m pip install -r harness/requirements.txt
python3 harness/generate_corpus.py
```

The default run writes `harness/corpus.jsonl`, `harness/dev.jsonl`, `harness/test.jsonl`, `harness/balance_report.md`, `harness/manifest.json`, and `harness/SHA256SUMS`. It generates 300 records with fixed seed `20261022`: 15 records in each severity-by-kind cell (4 severity levels × 5 Kubernetes kinds), divided within every cell into 3 development records and 12 held-out test records. This gives 60 development records (20%) and 240 test records (80%).

Use only `dev.jsonl` for prompt/model tuning. Keep `test.jsonl` frozen until final evaluation. The manifest records the seed and SHA-256 hashes; do not edit corpus or split files after freezing. Regenerate with the same seed and count to reproduce the bytes. A different seed or count is a new dataset version and requires a new manifest/hash set.

Every JSONL line is checked against `schema.json`. The JSON Patch operation is also applied to the included `base_object` using the supported `add`, `replace`, and `remove` subset, and its target kind/name are checked. The records are **synthetic mutation fixtures**, not observed drift events, labelled incident data, or evidence of model performance. Their `source_trace` points to taxonomy rationale, not a claim that cited papers supply the exact label. The DRIFT-19 taxonomy and this synthetic corpus remain subject to supervisor review. Keep the proposed 300-instance target unless pilot throughput indicates it is infeasible; obtain supervisor approval before reducing the target and report the achieved size.

The local corpus and hashes are ready for review. Do not upload this to the Terraform state bucket. To store it in S3, choose a dedicated research-artifact bucket and prefix first, then upload the frozen files and record the S3 object version IDs alongside the manifest.
