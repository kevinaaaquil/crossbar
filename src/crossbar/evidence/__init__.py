"""Evidence: what an Attempt left behind, captured for the judge.

Captured while the Environment is still alive, because judging can be deferred
and re-judged long after the container is gone. Anything the judge needs but
cannot get becomes a recorded reason, never an exception.
"""

from crossbar.evidence.model import (
    MAX_CONTENT_CHARS,
    Evidence,
    EvidenceItem,
    EvidenceRequest,
    load_evidence,
)
from crossbar.evidence.capture import capture

__all__ = [
    "MAX_CONTENT_CHARS",
    "Evidence",
    "EvidenceItem",
    "EvidenceRequest",
    "capture",
    "load_evidence",
]
