# Manifest validation gate

`src/validate_manifest.py` runs the static checks for a rendered Kubernetes
manifest in this order:

1. `kubeconform` with strict schema validation.
2. `conftest` with the policies in `policy/`.
3. `checkov` using its Kubernetes checks.
4. Optionally, Kubernetes API server validation with a server-side dry run.

Install the three static tools and run:

```bash
python3 infra/validator/src/validate_manifest.py rendered.yaml
```

To include the live API server's admission and schema checks, add
`--server-side`. That flag uses the active kubeconfig and context and performs
`kubectl apply --dry-run=server --validate=strict`; it does not write resources.
The caller must provide a short-lived, narrowly scoped identity approved for the
target namespace. ADR-0002 selects local static validation inside the worker;
server-side validation is a separate operator action. The worker has no cluster
credentials or Kubernetes write permissions.

Every command runs without a shell, has a timeout, prints its own output, and
contributes to a single pass/fail result. Any missing tool or failed check makes
the gate fail closed.

`src/validate_patch.py` can either export the event's exact Git revision from a
local checkout or use the checksum-verified `source.tar.gz` artifact, applies
the candidate patch in a disposable temporary directory, renders the Kustomize
application, then runs this manifest gate. It never changes the real GitOps
checkout. Its optional server-side gate is intended only for a runtime with the
scoped identity selected by ADR-0002.

Add `--json-report results.json` to the manifest command to record each check
as PASS, FAIL, ERROR, or SKIP with bounded diagnostic output and elapsed time.
Tool failures and timeouts are ERROR; a skipped server-side check is not proof
of live admission. The snapshot Python API accepts an optional `report` dict
and adds source verification, actual changed-file scope, Git application, and
rendering outcomes. Archives containing links, Git configuration, duplicate
members, or excessive expanded content are rejected before use.
