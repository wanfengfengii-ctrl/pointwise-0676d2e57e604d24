import sqlite3
from dataclasses import dataclass

from issue.artifacts import StationBundle


@dataclass(frozen=True)
class AcceptedBundle:
    acceptance_id: int
    bundle: StationBundle


def initialize_catalog(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS accepted_bundles (
            acceptance_id INTEGER PRIMARY KEY,
            plan_id TEXT NOT NULL REFERENCES calibration_plans(plan_id),
            station_id TEXT NOT NULL,
            bundle_id INTEGER NOT NULL,
            version INTEGER NOT NULL,
            wire TEXT NOT NULL,
            UNIQUE (plan_id, station_id, bundle_id, version)
        )
    ''')
    columns = {row[1] for row in db.execute('PRAGMA table_info(accepted_bundles)')}
    if 'version' not in columns:
        # Preserve every V1 acceptance when widening the identity of a V1-era catalog.
        # Disable FK enforcement while renaming a table referenced by report sources.
        foreign_keys = db.execute('PRAGMA foreign_keys').fetchone()[0]
        db.execute('PRAGMA foreign_keys = OFF')
        db.execute('ALTER TABLE accepted_bundles RENAME TO accepted_bundles_v1_legacy')
        db.execute('''
            CREATE TABLE accepted_bundles (
                acceptance_id INTEGER PRIMARY KEY,
                plan_id TEXT NOT NULL REFERENCES calibration_plans(plan_id),
                station_id TEXT NOT NULL,
                bundle_id INTEGER NOT NULL,
                version INTEGER NOT NULL,
                wire TEXT NOT NULL,
                UNIQUE (plan_id, station_id, bundle_id, version)
            )
        ''')
        db.execute('''
            INSERT INTO accepted_bundles (
                acceptance_id, plan_id, station_id, bundle_id, version, wire
            )
            SELECT acceptance_id, plan_id, station_id, bundle_id, 1, wire
            FROM accepted_bundles_v1_legacy
        ''')
        db.execute('DROP TABLE accepted_bundles_v1_legacy')
        if foreign_keys:
            db.execute('PRAGMA foreign_keys = ON')


def store_accepted(db: sqlite3.Connection, bundle: StationBundle) -> int:
    wire = bundle.to_json()
    identity = bundle.plan_id, bundle.station_id, bundle.bundle_id, bundle.version
    db.execute('BEGIN IMMEDIATE')
    with db:
        db.execute('''
            INSERT INTO accepted_bundles (plan_id, station_id, bundle_id, version, wire)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(plan_id, station_id, bundle_id, version) DO NOTHING
        ''', (*identity, wire))
        acceptance_id, stored = db.execute('''
            SELECT acceptance_id, wire FROM accepted_bundles
            WHERE plan_id = ? AND station_id = ? AND bundle_id = ? AND version = ?
        ''', identity).fetchone()
        if stored != wire:
            raise ValueError('station bundle identity already has different fixed content')
    return acceptance_id


def load_accepted(db: sqlite3.Connection, acceptance_id: int) -> AcceptedBundle:
    if type(acceptance_id) is not int or not 1 <= acceptance_id < 2**63:
        raise ValueError('acceptance ID must be a positive SQLite integer')
    row = db.execute('SELECT wire FROM accepted_bundles WHERE acceptance_id = ?', (acceptance_id,)).fetchone()
    if row is None:
        raise LookupError(f'unknown acceptance {acceptance_id}')
    return AcceptedBundle(acceptance_id, StationBundle.from_json(row[0]))
