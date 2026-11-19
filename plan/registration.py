import sqlite3
from dataclasses import dataclass

from plan.rationals import encode_rational, parse_rational
from plan.reference import ReferenceSpec, validate_reference
from plan.stations import require_name, validate_station_pair
from plan.units import validate_unit


@dataclass(frozen=True)
class PlanSpec:
    plan_id: str
    instrument: str
    unit: str
    reference: ReferenceSpec
    stations: tuple[str, str]


def initialize_plans(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS calibration_plans (
            plan_id TEXT PRIMARY KEY NOT NULL,
            instrument TEXT NOT NULL,
            unit TEXT NOT NULL,
            reference_position TEXT NOT NULL,
            lower_bound TEXT NOT NULL,
            upper_bound TEXT NOT NULL,
            first_station TEXT NOT NULL,
            second_station TEXT NOT NULL,
            CHECK (first_station <> second_station)
        )
    ''')


def register(db: sqlite3.Connection, plan: PlanSpec) -> str:
    if not isinstance(plan, PlanSpec):
        raise ValueError('expected a calibration plan')
    fixed = PlanSpec(
        require_name(plan.plan_id, 'plan_id'),
        require_name(plan.instrument, 'instrument'),
        validate_unit(plan.unit),
        validate_reference(plan.reference),
        validate_station_pair(plan.stations),
    )
    db.execute('BEGIN IMMEDIATE')
    with db:
        db.execute('''
            INSERT INTO calibration_plans (
                plan_id, instrument, unit, reference_position, lower_bound,
                upper_bound, first_station, second_station
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(plan_id) DO NOTHING
        ''', (
            fixed.plan_id, fixed.instrument, fixed.unit, str(fixed.reference.position),
            encode_rational(fixed.reference.lower), encode_rational(fixed.reference.upper),
            *fixed.stations,
        ))
        if load_plan(db, fixed.plan_id) != fixed:
            raise ValueError('plan ID already has a different fixed specification')
    return fixed.plan_id


def load_plan(db: sqlite3.Connection, plan_id: str) -> PlanSpec:
    require_name(plan_id, 'plan_id')
    row = db.execute('''
        SELECT instrument, unit, reference_position, lower_bound, upper_bound,
               first_station, second_station
        FROM calibration_plans WHERE plan_id = ?
    ''', (plan_id,)).fetchone()
    if row is None:
        raise LookupError(f'unknown plan {plan_id!r}')
    instrument, unit, position, lower, upper, first, second = row
    reference = ReferenceSpec(int(position), parse_rational(lower), parse_rational(upper))
    return PlanSpec(plan_id, instrument, unit, reference, (first, second))
