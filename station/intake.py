import sqlite3
from fractions import Fraction

from plan.rationals import parse_rational
from plan.registration import PlanSpec, load_plan
from plan.stations import require_applicable_station, require_name
from plan.units import require_in_domain
from station.corrections import stage_correction
from station.ledger import Reading, stage_reading
from station.positions import validate_position


def validate_reading(
    plan: PlanSpec, station_id: str, reading_id: str, position: int,
    value: object, weight: object, *, replaces: str | None = None,
    sensitivity: Fraction | None = None,
) -> Reading:
    require_applicable_station(station_id, plan.stations)
    require_name(reading_id, 'reading_id')
    position = validate_position(position)
    value = require_in_domain(value, plan.reference.domain, field='reading')
    weight = parse_rational(weight)
    if weight <= 0:
        raise ValueError('reading weight must be positive')
    # Zero is an explicit, valid sensitivity; None means the evidence is missing.
    sensitivity = None if sensitivity is None else parse_rational(sensitivity)
    if replaces is not None:
        require_name(replaces, 'replaces')
        if replaces == reading_id:
            raise ValueError('a correction requires a new reading ID')
    return Reading(
        plan.plan_id, station_id, reading_id, position, value, weight, replaces, sensitivity,
    )


def record(
    db: sqlite3.Connection, plan_id: str, station_id: str, reading_id: str,
    position: int, value: object, weight: object, *,
    base_revision_id: int | None = None, replaces: str | None = None,
    sensitivity: object = None,
) -> Reading:
    if (base_revision_id is None) != (replaces is None):
        raise ValueError('a correction requires both a base revision and a target reading')
    plan = load_plan(db, plan_id)
    reading = validate_reading(
        plan, station_id, reading_id, position, value, weight,
        replaces=replaces, sensitivity=None if sensitivity is None else parse_rational(sensitivity),
    )
    db.execute('BEGIN IMMEDIATE')
    with db:
        if base_revision_id is None:
            stage_reading(db, reading)
        else:
            stage_correction(db, reading, base_revision_id)
    return reading
