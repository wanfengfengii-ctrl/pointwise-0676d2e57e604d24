import sqlite3

from issue.artifacts import StationBundle, archive_station_bundle
from issue.checks import LocalCheck, build_local_check
from issue.evidence import encode_evidence
from issue.local_fit import build_loss_curve
from plan.registration import load_plan
from plan.stations import require_applicable_station
from station.revisions import load_revision


def issue(
    db: sqlite3.Connection, plan_id: str, station_id: str, revision_id: int,
) -> tuple[LocalCheck, StationBundle]:
    plan = load_plan(db, plan_id)
    require_applicable_station(station_id, plan.stations)
    revision = load_revision(db, revision_id)
    if (revision.plan_id, revision.station_id) != (plan.plan_id, station_id) or not revision.readings:
        raise ValueError('revision is not an effective revision for this plan and station')
    curve = build_loss_curve(revision, plan.reference)
    check = build_local_check(revision, curve)
    bundle = archive_station_bundle(db, plan, station_id, revision_id, encode_evidence(curve))
    return check, bundle
