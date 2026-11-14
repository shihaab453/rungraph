"""Private synthetic trace-claim classifier, not an authority or native validator.

Classifies *supplied assertions* only. No filesystem, SQLite, or RunGraph I/O.
An eligible result never authorizes staged reads, writes, repair or cutover.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ClaimedPoint:
    ordinal: int | None
    json_digest: str | None
    json_identity: str | None
    shadow_source_digest: str | None
    stage_identity: str | None
    stage_receipt_digest: str | None
    stage_revision: int | None
    stage_verified: bool | None
    artifact_digest: str | None


@dataclass(frozen=True, slots=True)
class TraceDecision:
    status: str
    reason: str


REFUSE = "REFUSE"
REVIEW = "ELIGIBLE_FOR_SEPARATE_VALIDATION"


def classify_claimed_trace(points: tuple[ClaimedPoint, ...]) -> TraceDecision:
    """Detect divergences visible in a bounded sequence of untrusted claims.

    The tuple is NOT a filesystem snapshot, a native guard, or a history proof.
    Undisclosed A-B-A changes cannot be detected.
    """
    if type(points) is not tuple or not 3 <= len(points) <= 32:
        return TraceDecision(REFUSE, "INVALID_TRACE_SHAPE")
    if any(type(p) is not ClaimedPoint for p in points):
        return TraceDecision(REFUSE, "INVALID_TRACE_SHAPE")
    if any(type(p.ordinal) is not int or p.ordinal != i for i, p in enumerate(points)):
        return TraceDecision(REFUSE, "INVALID_ORDINAL_SEQUENCE")

    strings = (
        "json_digest", "json_identity", "shadow_source_digest",
        "stage_identity", "stage_receipt_digest", "artifact_digest",
    )
    if any(not isinstance(getattr(p, k), str) or not getattr(p, k).strip()
           for p in points for k in strings):
        return TraceDecision(REFUSE, "MISSING_OR_INVALID_OBSERVATION")
    if any(type(p.stage_revision) is not int or p.stage_revision != 1 for p in points):
        return TraceDecision(REFUSE, "UNSUPPORTED_STAGE_REVISION")
    if any(p.stage_verified is not True for p in points):
        return TraceDecision(REFUSE, "STAGE_NOT_VERIFIED")

    start = points[0]
    fields = (
        ("json_digest", "OBSERVED_JSON_BYTES_TRANSITION"),
        ("json_identity", "OBSERVED_JSON_IDENTITY_TRANSITION"),
        ("stage_identity", "OBSERVED_STAGE_IDENTITY_TRANSITION"),
        ("stage_receipt_digest", "OBSERVED_STAGE_RECEIPT_TRANSITION"),
        ("artifact_digest", "OBSERVED_ARTIFACT_TRANSITION"),
    )
    for key, reason in fields:
        if any(getattr(p, key) != getattr(start, key) for p in points[1:]):
            return TraceDecision(REFUSE, reason)
    if any(p.shadow_source_digest != p.json_digest for p in points):
        return TraceDecision(REFUSE, "SHADOW_SOURCE_DIVERGED")

    return TraceDecision(REVIEW, "PROVISIONAL_CLAIMED_TRACE_STABILITY_ONLY")
