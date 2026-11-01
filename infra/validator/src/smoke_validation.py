"""Exercise installed static tools against known-bad inputs without cloud access."""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from pathlib import Path

POLICY = Path(__file__).resolve().parents[1] / "policy"
UNSAFE_POD = """apiVersion: v1
kind: Pod
metadata:
  name: deliberately-unsafe
spec:
  hostNetwork: true
  containers:
    - name: app
      image: nginx:latest
      securityContext:
        privileged: true
"""


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="drift-validator-smoke-") as directory:
        root = Path(directory)
        invalid = root / "malformed.yaml"
        invalid.write_text("apiVersion: v1\nkind: Pod\nspec: [\n", encoding="utf-8")
        unsafe = root / "unsafe.yaml"
        unsafe.write_text(UNSAFE_POD, encoding="utf-8")
        commands = {
            "kubeconform": ["kubeconform", "-strict", "-summary", str(invalid)],
            "conftest": ["conftest", "test", "--policy", str(POLICY), str(unsafe)],
            "checkov": ["checkov", "--framework", "kubernetes", "--skip-download",
                        "--output", "json", "--file", str(unsafe)],
        }
        results = []
        for name, command in commands.items():
            start = time.monotonic()
            result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=120)
            output = result.stdout + result.stderr
            if name == "checkov":
                parsed = json.loads(result.stdout)
                rejected = result.returncode == 1 and parsed["summary"]["failed"] > 0
            elif name == "conftest":
                rejected = result.returncode == 1 and "privileged container" in output
            else:
                rejected = result.returncode == 1 and "error unmarshalling resource" in output.lower()
            record = {"tool": name, "known_bad_rejected": rejected,
                      "duration_ms": round((time.monotonic() - start) * 1000)}
            results.append(record)
            print(json.dumps(record), flush=True)
            if not rejected:
                print(output[:3000], flush=True)
        return 0 if all(row["known_bad_rejected"] for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
