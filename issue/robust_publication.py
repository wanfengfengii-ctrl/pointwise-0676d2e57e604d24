import sqlite3
from dataclasses import replace
from fractions import Fraction

from issue.artifacts import StationBundle, archive_station_bundle
from issue.local_fit import build_loss_curve
from issue.robust_evidence import RobustLossEvidence, encode_robust_evidence
from plan.registration import load_plan
from plan.stations import require_applicable_station
from station.revisions import ReadingRevision, load_revision


def _shifted_revision(
    revision: ReadingRevision, offset: Fraction,
) -> tuple[ReadingRevision, tuple[Fraction, ...]]:
    shifted = tuple(
        replace(reading, value=reading.value + reading.sensitivity * offset)
        for reading in revision.readings
    )
    conditioned = ReadingRevision(
        revision.revision_id, revision.plan_id, revision.station_id,
        revision.base_revision_id, shifted,
    )
    return conditioned, tuple(reading.value for reading in shifted)


def issue_robust(
    db: sqlite3.Connection, plan_id: str, station_id: str, revision_id: int,
) -> StationBundle:
    """Issue the fixed V2 bundle for one exact frozen revision under the shared offset."""
    plan = load_plan(db, plan_id)
    require_applicable_station(station_id, plan.stations)
    if plan.offset_interval is None:
        raise ValueError('the fixed plan declares no shared offset interval')
    offset_interval = plan.offset_interval
    revision = load_revision(db, revision_id)
    if (revision.plan_id, revision.station_id) != (plan.plan_id, station_id) or not revision.readings:
        raise ValueError('revision is not an effective revision for this plan and station')
    if any(reading.sensitivity is None for reading in revision.readings):
        raise ValueError('every effective reading requires a registered sensitivity')
    lower, upper = offset_interval
    lower_revision, lower_values = _shifted_revision(revision, lower)
    upper_revision, upper_values = _shifted_revision(revision, upper)
    domain = plan.reference.domain
    for label, values in (('lower', lower_values), ('upper', upper_values)):
        if any(not domain[0] <= value <= domain[1] for value in values):
            raise ValueError(
                f'a corrected reading leaves [0, 10] at the {label} offset endpoint'
            )
    lower_curve = build_loss_curve(lower_revision, plan.reference)
    upper_curve = build_loss_curve(upper_revision, plan.reference)
    evidence = RobustLossEvidence(offset_interval, lower_curve, upper_curve)
    return archive_station_bundle(
        db, plan, station_id, revision_id, encode_robust_evidence(evidence), version=2,
    )
