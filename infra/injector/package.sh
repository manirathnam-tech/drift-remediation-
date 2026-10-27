#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
injector_dir="$repo_root/infra/injector"
build_dir="$injector_dir/build"

test -s "$repo_root/harness/corpus.jsonl" || {
  echo "Missing harness/corpus.jsonl; generate the frozen corpus first." >&2
  exit 1
}
python3 - "$build_dir" <<'PY'
from pathlib import Path
import shutil
import sys

build = Path(sys.argv[1])
if build.exists():
    shutil.rmtree(build)
(build / "layer/python").mkdir(parents=True)
(build / "function").mkdir(parents=True)
PY

python3 -m pip install \
  --platform manylinux2014_x86_64 \
  --implementation cp \
  --python-version 3.12 \
  --abi cp312 \
  --only-binary=:all: \
  --target "$build_dir/layer/python" \
  -r "$injector_dir/requirements.txt"

python3 - "$repo_root" "$build_dir" <<'PY'
from pathlib import Path
import sys
import zipfile

root, build = Path(sys.argv[1]), Path(sys.argv[2])
shutil = __import__("shutil")
shutil.copy2(root / "infra/injector/src/injector.py", build / "function/injector.py")
shutil.copy2(root / "harness/corpus.jsonl", build / "function/corpus.jsonl")
with zipfile.ZipFile(build / "kubernetes-client-layer.zip", "w", zipfile.ZIP_DEFLATED) as archive:
    for path in sorted((build / "layer").rglob("*")):
        if path.is_file():
            archive.write(path, path.relative_to(build / "layer"))
print(f"Prepared {build / 'function'} for Terraform and built {build / 'kubernetes-client-layer.zip'}")
PY
