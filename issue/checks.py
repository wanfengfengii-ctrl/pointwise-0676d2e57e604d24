from dataclasses import dataclass
from fractions import Fraction

from issue.evidence import StationLossEvidence
from station.ledger import Reading
from station.revisions import ReadingRevision


@dataclass(frozen=True)
class LocalCheck:
    plan_id: str
    station_id: str
    revision_id: int
    reference_interval: tuple[Fraction, Fraction]
    minimum_error: Fraction
    readings: tuple[Reading, ...]


def build_local_check(revision: ReadingRevision, evidence: StationLossEvidence) -> LocalCheck:
    minimum_error = min(error for _, error in evidence.points)
    optimal_points = [reference for reference, error in evidence.points if error == minimum_error]
    return LocalCheck(
        revision.plan_id, revision.station_id, revision.revision_id,
        (optimal_points[0], optimal_points[-1]), minimum_error, revision.readings,
    )
