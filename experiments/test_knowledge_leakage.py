"""The index preparation check must refuse held-out and unknown identities."""

import json

import pytest
from check_knowledge_leakage import check


@pytest.mark.parametrize("reference,passes", [
    ("SYN-20261022-0001", True), ("SYN-20261022-0002", False),
    ("SYN-20261022-9999", False),
])
def test_only_dev_references_are_allowed(tmp_path, reference, passes):
    dev = tmp_path / "dev.jsonl"
    test = tmp_path / "test.jsonl"
    dev.write_text(json.dumps({"id": "SYN-20261022-0001", "split": "dev"}))
    test.write_text(json.dumps({"id": "SYN-20261022-0002", "split": "test"}))
    (tmp_path / "examples.md").write_text(reference)
    if passes:
        assert check(tmp_path, dev, test)["dev_examples_referenced"] == 1
    else:
        with pytest.raises(ValueError, match="ID"):
            check(tmp_path, dev, test)
