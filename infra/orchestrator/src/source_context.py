"""Read bounded manifest source from a verified archive without extracting it."""

from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import PurePosixPath

from errors import TreatmentDependencyError

MAX_SOURCE_BYTES = 100_000


def manifest_sources(golden: dict) -> dict[str, str]:
    archive = golden.get("source_archive")
    if not isinstance(archive, bytes):
        raise TreatmentDependencyError("source archive is required for patch generation")
    if hashlib.sha256(archive).hexdigest() != golden.get("source_sha256"):
        raise TreatmentDependencyError("source archive checksum mismatch")
    sources = {}
    size = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as bundle:
            for member in bundle:
                path = PurePosixPath(member.name)
                if path.is_absolute() or ".." in path.parts or "\\" in member.name:
                    raise TreatmentDependencyError("unsafe source archive path")
                if not member.name.startswith("apps/online-boutique/"):
                    continue
                if path.suffix not in {".yaml", ".yml"}:
                    continue
                if not member.isfile() or member.name in sources:
                    raise TreatmentDependencyError("source must contain unique regular files")
                size += member.size
                if size > MAX_SOURCE_BYTES:
                    raise TreatmentDependencyError("manifest source exceeds prompt budget")
                stream = bundle.extractfile(member)
                if stream is None:
                    raise TreatmentDependencyError("manifest source cannot be read")
                with stream:
                    sources[member.name] = stream.read(MAX_SOURCE_BYTES + 1).decode("utf-8")
    except (tarfile.TarError, UnicodeError, OSError) as exc:
        raise TreatmentDependencyError("invalid source archive") from exc
    if not sources:
        raise TreatmentDependencyError("no GitOps manifest sources found")
    return sources
