"""Shared dependency error used to stop treatment safely and escalate."""


class TreatmentDependencyError(RuntimeError):
    """Raised when a required provider, artifact, or retrieval dependency fails."""
