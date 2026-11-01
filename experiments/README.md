# Offline treatment experiments

`eval_classify.py` scores saved classifier outputs against the development split.
It does not invoke Bedrock. Predictions use JSONL rows with an instance `id` and
the raw model JSON string in `raw_output`:

```json
{"id":"SYN-20261022-0005","raw_output":"{\"severity\":0,\"drift_class\":\"benign\",\"intent\":\"intentional-benign\",\"justification\":\"approved metadata-only change\",\"confidence\":0.95,\"review_required\":false,\"evidence_refs\":[\"taxonomy-v1\"],\"provenance\":{\"model_id\":\"model-id-from-ADR-0005\",\"prompt_version\":\"classify_v1\",\"prompt_sha256\":\"0000000000000000000000000000000000000000000000000000000000000000\",\"temperature\":0}}"}
```

The raw output must match the versioned classification schema, including model
ID, prompt version/hash, and temperature provenance. Schema-invalid first-pass
outputs count against first-pass validity and are excluded from label scoring.
Predictions for IDs outside the supplied dev file are rejected. Every data row
must say `split=dev`; there is no test-split option.

After obtaining predictions from both model families, run:

```bash
python3 experiments/eval_classify.py \
  --prediction amazon=/path/to/amazon-dev-predictions.jsonl \
  --prediction anthropic=/path/to/anthropic-dev-predictions.jsonl \
  --out experiments/results/classification-dev.md
```

The command writes a Markdown report and a JSON metrics file beside it. The
report includes first-pass schema validity, dev coverage, class and severity
accuracy, macro-F1, and confusion matrices. These are synthetic development
fixtures; they do not represent observed incident performance. Model family
selection remains open until ADR-0005 is available.
