import sqlite3
from dataclasses import dataclass
from fractions import Fraction

from plan.rationals import encode_rational, parse_rational


@dataclass(frozen=True)
class Reading:
    plan_id: str
    station_id: str
    reading_id: str
    position: int
    value: Fraction
    weight: Fraction
    replaces: str | None = None


def initialize_readings(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS reading_facts (
            plan_id TEXT NOT NULL REFERENCES calibration_plans(plan_id),
            station_id TEXT NOT NULL,
            reading_id TEXT NOT NULL,
            position TEXT NOT NULL,
            value TEXT NOT NULL,
            weight TEXT NOT NULL,
            replaces TEXT,
            PRIMARY KEY (plan_id, station_id, reading_id),
            FOREIGN KEY (plan_id, station_id, replaces)
                REFERENCES reading_facts(plan_id, station_id, reading_id)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS station_drafts (
            plan_id TEXT NOT NULL REFERENCES calibration_plans(plan_id),
            station_id TEXT NOT NULL,
            base_revision_id INTEGER,
            PRIMARY KEY (plan_id, station_id),
            FOREIGN KEY (base_revision_id, plan_id, station_id)
                REFERENCES reading_revisions(revision_id, plan_id, station_id)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS draft_members (
            plan_id TEXT NOT NULL,
            station_id TEXT NOT NULL,
            reading_id TEXT NOT NULL,
            PRIMARY KEY (plan_id, station_id, reading_id),
            FOREIGN KEY (plan_id, station_id) REFERENCES station_drafts(plan_id, station_id),
            FOREIGN KEY (plan_id, station_id, reading_id)
                REFERENCES reading_facts(plan_id, station_id, reading_id)
        )
    ''')


def _reading_from_row(plan_id: str, station_id: str, row: tuple) -> Reading:
    reading_id, position, value, weight, replaces = row
    return Reading(
        plan_id, station_id, reading_id, int(position),
        parse_rational(value), parse_rational(weight), replaces,
    )


def load_reading(
    db: sqlite3.Connection, plan_id: str, station_id: str, reading_id: str
) -> Reading:
    row = db.execute('''
        SELECT reading_id, position, value, weight, replaces
        FROM reading_facts WHERE plan_id = ? AND station_id = ? AND reading_id = ?
    ''', (plan_id, station_id, reading_id)).fetchone()
    if row is None:
        raise LookupError(f'unknown reading {reading_id!r} for {plan_id}/{station_id}')
    return _reading_from_row(plan_id, station_id, row)


def load_draft(db: sqlite3.Connection, plan_id: str, station_id: str) -> tuple[Reading, ...]:
    rows = db.execute('''
        SELECT r.reading_id, r.position, r.value, r.weight, r.replaces
        FROM draft_members AS d
        JOIN reading_facts AS r USING (plan_id, station_id, reading_id)
        WHERE d.plan_id = ? AND d.station_id = ?
        ORDER BY r.reading_id
    ''', (plan_id, station_id))
    return tuple(_reading_from_row(plan_id, station_id, row) for row in rows)


def load_draft_base(db: sqlite3.Connection, plan_id: str, station_id: str) -> int | None:
    row = db.execute('''
        SELECT base_revision_id FROM station_drafts WHERE plan_id = ? AND station_id = ?
    ''', (plan_id, station_id)).fetchone()
    return None if row is None else row[0]


def _insert_reading(db: sqlite3.Connection, reading: Reading) -> None:
    inserted = db.execute('''
        INSERT INTO reading_facts (plan_id, station_id, reading_id, position, value, weight, replaces)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(plan_id, station_id, reading_id) DO NOTHING
    ''', (
        reading.plan_id, reading.station_id, reading.reading_id, str(reading.position),
        encode_rational(reading.value), encode_rational(reading.weight), reading.replaces,
    ))
    if inserted.rowcount != 1:
        raise ValueError(f'reading ID already exists: {reading.reading_id!r}')


def stage_reading(db: sqlite3.Connection, reading: Reading) -> None:
    """Append a checked reading within the caller's station transaction."""
    _insert_reading(db, reading)
    db.execute('''
        INSERT INTO station_drafts (plan_id, station_id) VALUES (?, ?)
        ON CONFLICT(plan_id, station_id) DO NOTHING
    ''', (reading.plan_id, reading.station_id))
    db.execute('''
        INSERT INTO draft_members (plan_id, station_id, reading_id) VALUES (?, ?, ?)
    ''', (reading.plan_id, reading.station_id, reading.reading_id))


def replace_draft_member(
    db: sqlite3.Connection, replacement: Reading, base_revision_id: int,
    base_readings: tuple[Reading, ...],
) -> None:
    """Stage the chosen revision with one replacement in the caller's transaction."""
    _insert_reading(db, replacement)
    scope = replacement.plan_id, replacement.station_id
    db.execute('''
        INSERT INTO station_drafts (plan_id, station_id, base_revision_id) VALUES (?, ?, ?)
        ON CONFLICT(plan_id, station_id) DO UPDATE SET base_revision_id = excluded.base_revision_id
    ''', (*scope, base_revision_id))
    db.execute('DELETE FROM draft_members WHERE plan_id = ? AND station_id = ?', scope)
    member_ids = (
        replacement.reading_id if reading.reading_id == replacement.replaces else reading.reading_id
        for reading in base_readings
    )
    db.executemany('''
        INSERT INTO draft_members (plan_id, station_id, reading_id) VALUES (?, ?, ?)
    ''', ((*scope, reading_id) for reading_id in member_ids))
