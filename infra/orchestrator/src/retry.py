"""Bounded validator-feedback retries for patch proposals."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

Proposal = dict[str, Any]
Feedback = Sequence[str]
Generator = Callable[[Feedback], Proposal]
Validator = Callable[[Proposal], list[str]]


def generate_with_bounded_retries(
    generate: Generator,
    validate: Validator,
    *,
    max_retries: int = 2,
    on_attempt: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Generate once, then retry with validator reasons at most twice.

    The returned attempts include each proposal, verdict, and feedback reasons so a
    caller can persist the audit record. This function never applies or merges a patch.
    """
    if (
        isinstance(max_retries, bool)
        or not isinstance(max_retries, int)
        or not 0 <= max_retries <= 2
    ):
        raise ValueError("max_retries must be between 0 and 2")

    attempts: list[dict[str, Any]] = []
    feedback: list[str] = []
    for attempt_number in range(1, max_retries + 2):
        proposal = generate(feedback)
        reasons = validate(proposal)
        attempts.append(
            {
                "attempt": attempt_number,
                "verdict": "PASS" if not reasons else "REJECT",
                "reasons": reasons,
                "proposal": proposal,
            }
        )
        if on_attempt is not None:
            on_attempt(attempts[-1])
        if not reasons:
            return {
                "status": "VALIDATED",
                "proposal": proposal,
                "attempts": attempts,
            }
        feedback = reasons

    return {
        "status": "ESCALATE",
        "proposal": None,
        "attempts": attempts,
        "escalation_reason": "validator rejected the proposal after the retry limit",
    }
