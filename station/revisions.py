import sqlite3
from dataclasses import dataclass

from plan.registration import load_plan
from plan.stations import require_applicable_station
from station.ledger import Reading, load_draft, load_draft_base, load_reading
from station.positions import group_readings_by_position, ordered_positions


@dataclass(frozen=True)
class ReadingRevision:
    revision_id: int
    plan_id: str
    station_id: str
    base_revision_id: int | None
    readings: tuple[Reading, ...]


def initialize_revisions(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS reading_revisions (
            revision_id INTEGER PRIMARY KEY,
            plan_id TEXT NOT NULL REFERENCES calibration_plans(plan_id),
            station_id TEXT NOT NULL,
            base_revision_id INTEGER,
            UNIQUE (revision_id, plan_id, station_id),
            FOREIGN KEY (base_revision_id, plan_id, station_id)
                REFERENCES reading_revisions(revision_id, plan_id, station_id)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS revision_members (
            revision_id INTEGER NOT NULL,
            plan_id TEXT NOT NULL,
            station_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            reading_id TEXT NOT NULL,
            PRIMARY KEY (revision_id, ordinal),
            UNIQUE (revision_id, reading_id),
            FOREIGN KEY (revision_id, plan_id, station_id)
                REFERENCES reading_revisions(revision_id, plan_id, station_id),
            FOREIGN KEY (plan_id, station_id, reading_id)
                REFERENCES reading_facts(plan_id, station_id, reading_id)
        )
    ''')


def freeze_revision(db: sqlite3.Connection, plan_id: str, station_id: str) -> ReadingRevision:
    plan = load_plan(db, plan_id)
    require_applicable_station(station_id, plan.stations)
    db.execute('BEGIN IMMEDIATE')
    with db:
        draft = load_draft(db, plan_id, station_id)
        if not draft:
            raise ValueError('no effective readings to freeze')
        groups = group_readings_by_position(draft)
        readings = tuple(
            reading
            for position in ordered_positions(groups, plan.reference.position)
            for reading in groups.get(position, ())
        )
        base = load_draft_base(db, plan_id, station_id)
        revision_id = db.execute('''
            INSERT INTO reading_revisions (plan_id, station_id, base_revision_id) VALUES (?, ?, ?)
        ''', (plan_id, station_id, base)).lastrowid
        db.executemany('''
            INSERT INTO revision_members (revision_id, plan_id, station_id, ordinal, reading_id)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            (revision_id, plan_id, station_id, index, reading.reading_id)
            for index, reading in enumerate(readings)
        ))
    return ReadingRevision(revision_id, plan_id, station_id, base, readings)


def load_revision(db: sqlite3.Connection, revision_id: int) -> ReadingRevision:
    if type(revision_id) is not int or not 1 <= revision_id < 2**63:
        raise ValueError('revision ID must be a positive SQLite integer')
    row = db.execute('''
        SELECT plan_id, station_id, base_revision_id FROM reading_revisions WHERE revision_id = ?
    ''', (revision_id,)).fetchone()
    if row is None:
        raise LookupError(f'unknown revision {revision_id}')
    plan_id, station_id, base = row
    members = db.execute('''
        SELECT reading_id FROM revision_members WHERE revision_id = ? ORDER BY ordinal
    ''', (revision_id,))
    readings = tuple(load_reading(db, plan_id, station_id, member[0]) for member in members)
    return ReadingRevision(revision_id, plan_id, station_id, base, readings)
