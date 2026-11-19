import json
import sqlite3
from dataclasses import dataclass
from fractions import Fraction

from plan.rationals import encode_rational, parse_rational
from plan.registration import PlanSpec


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    fields = {}
    for key, value in pairs:
        if key in fields:
            raise ValueError(f'duplicate station bundle field: {key}')
        fields[key] = value
    return fields


def _reject_constant(token: str) -> None:
    raise ValueError(f'invalid JSON number: {token}')


def _json_object(text: str) -> dict[str, object]:
    if not isinstance(text, str):
        raise ValueError('station bundle JSON must be text')
    value = json.loads(text, object_pairs_hook=_unique_fields, parse_constant=_reject_constant)
    if not isinstance(value, dict):
        raise ValueError('station bundle requires a JSON object')
    return value


def _json_text(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


@dataclass(frozen=True)
class StationBundle:
    bundle_id: int
    plan_id: str
    instrument: str
    unit: str
    reference_position: int
    domain: tuple[Fraction, Fraction]
    stations: tuple[str, str]
    station_id: str
    revision_id: int
    evidence_json: str
    version: int = 1

    @property
    def evidence(self) -> dict[str, object]:
        return _json_object(self.evidence_json)

    def to_json(self) -> str:
        return _json_text({
            'version': self.version,
            'bundle_id': self.bundle_id,
            'plan_id': self.plan_id,
            'instrument': self.instrument,
            'unit': self.unit,
            'reference_position': self.reference_position,
            'domain': [encode_rational(bound) for bound in self.domain],
            'stations': list(self.stations),
            'station_id': self.station_id,
            'revision_id': self.revision_id,
            'evidence': self.evidence,
        })

    @classmethod
    def from_json(cls, text: str) -> 'StationBundle':
        fields = _json_object(text)
        if set(fields) != {
            'version', 'bundle_id', 'plan_id', 'instrument', 'unit', 'reference_position',
            'domain', 'stations', 'station_id', 'revision_id', 'evidence',
        }:
            raise ValueError('invalid station bundle envelope fields')
        domain = fields['domain']
        stations = fields['stations']
        if not isinstance(domain, list) or len(domain) != 2 or any(type(bound) is not str for bound in domain):
            raise ValueError('bundle domain requires two rational strings')
        if not isinstance(stations, list) or len(stations) != 2:
            raise ValueError('bundle must declare two applicable stations')
        if not isinstance(fields['evidence'], dict):
            raise ValueError('bundle evidence must be an object')
        if type(fields['version']) is not int or fields['version'] not in (1, 2):
            raise ValueError('unsupported station bundle version')
        return cls(
            fields['bundle_id'], fields['plan_id'], fields['instrument'], fields['unit'],
            fields['reference_position'], (parse_rational(domain[0]), parse_rational(domain[1])),
            (stations[0], stations[1]), fields['station_id'], fields['revision_id'],
            _json_text(fields['evidence']), fields['version'],
        )


def initialize_station_artifacts(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS station_bundles (
            bundle_id INTEGER NOT NULL REFERENCES reading_revisions(revision_id),
            version INTEGER NOT NULL,
            wire TEXT NOT NULL,
            PRIMARY KEY (bundle_id, version)
        )
    ''')
    columns = {row[1] for row in db.execute('PRAGMA table_info(station_bundles)')}
    if 'version' not in columns:
        # A V1-era table keyed only by bundle ID: keep every fixed V1 artifact.
        # FK enforcement must be off while renaming a table that other schemas
        # reference, otherwise the rename fires cascading delete actions.
        foreign_keys = db.execute('PRAGMA foreign_keys').fetchone()[0]
        db.execute('PRAGMA foreign_keys = OFF')
        db.execute('ALTER TABLE station_bundles RENAME TO station_bundles_v1_legacy')
        db.execute('''
            CREATE TABLE station_bundles (
                bundle_id INTEGER NOT NULL REFERENCES reading_revisions(revision_id),
                version INTEGER NOT NULL,
                wire TEXT NOT NULL,
                PRIMARY KEY (bundle_id, version)
            )
        ''')
        db.execute('''
            INSERT INTO station_bundles (bundle_id, version, wire)
            SELECT bundle_id, 1, wire FROM station_bundles_v1_legacy
        ''')
        db.execute('DROP TABLE station_bundles_v1_legacy')
        if foreign_keys:
            db.execute('PRAGMA foreign_keys = ON')


def archive_station_bundle(
    db: sqlite3.Connection, plan: PlanSpec, station_id: str, revision_id: int,
    evidence: dict, version: int = 1,
) -> StationBundle:
    # Each frozen revision has one artifact identity per version in its issuing database.
    bundle = StationBundle(
        revision_id, plan.plan_id, plan.instrument, plan.unit, plan.reference.position,
        plan.reference.domain, plan.stations, station_id, revision_id, _json_text(evidence), version,
    )
    db.execute('BEGIN IMMEDIATE')
    with db:
        db.execute('''
            INSERT INTO station_bundles (bundle_id, version, wire) VALUES (?, ?, ?)
            ON CONFLICT(bundle_id, version) DO NOTHING
        ''', (bundle.bundle_id, bundle.version, bundle.to_json()))
        archived = load_station_bundle(db, bundle.bundle_id, bundle.version)
        if archived != bundle:
            raise ValueError('the frozen revision already has different archived evidence')
    return archived


def load_station_bundle(
    db: sqlite3.Connection, bundle_id: int, version: int = 1,
) -> StationBundle:
    if type(bundle_id) is not int or not 1 <= bundle_id < 2**63:
        raise ValueError('bundle ID must be a positive SQLite integer')
    if type(version) is not int or version not in (1, 2):
        raise ValueError('unsupported station bundle version')
    row = db.execute(
        'SELECT wire FROM station_bundles WHERE bundle_id = ? AND version = ?',
        (bundle_id, version),
    ).fetchone()
    if row is None:
        raise LookupError(f'unknown station bundle {bundle_id}')
    return StationBundle.from_json(row[0])
