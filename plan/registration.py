import sqlite3
from dataclasses import dataclass
from fractions import Fraction

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
    offset_interval: tuple[Fraction, Fraction] | None = None


def validate_offset_interval(value: object) -> tuple[Fraction, Fraction] | None:
    """Validate the optional fixed rational closed offset interval (single points allowed)."""
    if value is None:
        return None
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise ValueError('offset interval requires a lower and an upper rational endpoint')
    lower = parse_rational(value[0])
    upper = parse_rational(value[1])
    if lower > upper:
        raise ValueError('offset interval lower endpoint must not exceed its upper endpoint')
    return lower, upper


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
    # Reinitialization must also widen a V1-era table without touching fixed results.
    columns = {row[1] for row in db.execute('PRAGMA table_info(calibration_plans)')}
    if 'offset_lower' not in columns:
        db.execute('ALTER TABLE calibration_plans ADD COLUMN offset_lower TEXT')
    if 'offset_upper' not in columns:
        db.execute('ALTER TABLE calibration_plans ADD COLUMN offset_upper TEXT')


def register(db: sqlite3.Connection, plan: PlanSpec) -> str:
    if not isinstance(plan, PlanSpec):
        raise ValueError('expected a calibration plan')
    offset_interval = validate_offset_interval(plan.offset_interval)
    fixed = PlanSpec(
        require_name(plan.plan_id, 'plan_id'),
        require_name(plan.instrument, 'instrument'),
        validate_unit(plan.unit),
        validate_reference(plan.reference),
        validate_station_pair(plan.stations),
        offset_interval,
    )
    db.execute('BEGIN IMMEDIATE')
    with db:
        db.execute('''
            INSERT INTO calibration_plans (
                plan_id, instrument, unit, reference_position, lower_bound,
                upper_bound, first_station, second_station, offset_lower, offset_upper
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(plan_id) DO NOTHING
        ''', (
            fixed.plan_id, fixed.instrument, fixed.unit, str(fixed.reference.position),
            encode_rational(fixed.reference.lower), encode_rational(fixed.reference.upper),
            *fixed.stations,
            None if fixed.offset_interval is None else encode_rational(fixed.offset_interval[0]),
            None if fixed.offset_interval is None else encode_rational(fixed.offset_interval[1]),
        ))
        if load_plan(db, fixed.plan_id) != fixed:
            raise ValueError('plan ID already has a different fixed specification')
    return fixed.plan_id


def load_plan(db: sqlite3.Connection, plan_id: str) -> PlanSpec:
    require_name(plan_id, 'plan_id')
    row = db.execute('''
        SELECT instrument, unit, reference_position, lower_bound, upper_bound,
               first_station, second_station, offset_lower, offset_upper
        FROM calibration_plans WHERE plan_id = ?
    ''', (plan_id,)).fetchone()
    if row is None:
        raise LookupError(f'unknown plan {plan_id!r}')
    instrument, unit, position, lower, upper, first, second, offset_lower, offset_upper = row
    reference = ReferenceSpec(int(position), parse_rational(lower), parse_rational(upper))
    if offset_lower is None or offset_upper is None:
        offset_interval = None
    else:
        offset_interval = (parse_rational(offset_lower), parse_rational(offset_upper))
    return PlanSpec(plan_id, instrument, unit, reference, (first, second), offset_interval)
