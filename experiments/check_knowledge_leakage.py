"""Check indexed Markdown for held-out IDs without using test labels or examples."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

INSTANCE_ID = re.compile(r"SYN-\d{8}-\d+")


def check(corpus: Path, dev_path: Path, test_path: Path) -> dict:
    def ids(path, split):
        result = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("split") != split or not isinstance(row.get("id"), str):
                raise ValueError(f"invalid {split} split identity")
            if row["id"] in result:
                raise ValueError(f"duplicate {split} split identity")
            result.add(row["id"])
        if not result:
            raise ValueError(f"empty {split} split")
        return result

    dev_ids = ids(dev_path, "dev")
    test_ids = ids(test_path, "test")
    if dev_ids & test_ids:
        raise ValueError("dev and test IDs overlap")
    files = sorted(corpus.glob("*.md"))
    if not files:
        raise ValueError("indexed corpus contains no Markdown files")
    referenced = set()
    for path in files:
        found = set(INSTANCE_ID.findall(path.read_text(encoding="utf-8")))
        if found & test_ids:
            raise ValueError(f"held-out ID found in {path.name}")
        if found - dev_ids:
            raise ValueError(f"unknown instance ID found in {path.name}")
        referenced.update(found)
    return {"status": "PASS", "documents": len(files), "dev_examples_referenced": len(referenced),
            "scope": "ID-level leakage check only; content and adjudication require review"}


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=root / "infra/orchestrator/knowledge_base")
    parser.add_argument("--dev", type=Path, default=root / "harness/dev.jsonl")
    parser.add_argument("--test", type=Path, default=root / "harness/test.jsonl")
    args = parser.parse_args()
    print(json.dumps(check(args.corpus, args.dev, args.test)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
