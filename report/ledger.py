import sqlite3
from dataclasses import dataclass
from fractions import Fraction

from plan.rationals import encode_rational, parse_rational


@dataclass(frozen=True)
class ReportSource:
    station_id: str
    acceptance_id: int
    bundle_id: int
    revision_id: int


@dataclass(frozen=True)
class SignedReport:
    report_id: int
    plan_id: str
    reference_interval: tuple[Fraction, Fraction]
    minimum_error: Fraction
    sources: tuple[ReportSource, ReportSource]


def initialize_reports(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS signed_reports (
            report_id INTEGER PRIMARY KEY,
            plan_id TEXT NOT NULL REFERENCES calibration_plans(plan_id),
            lower_reference TEXT NOT NULL,
            upper_reference TEXT NOT NULL,
            minimum_error TEXT NOT NULL
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS report_sources (
            report_id INTEGER NOT NULL REFERENCES signed_reports(report_id),
            slot INTEGER NOT NULL CHECK (slot IN (0, 1)),
            station_id TEXT NOT NULL,
            acceptance_id INTEGER NOT NULL REFERENCES accepted_bundles(acceptance_id),
            bundle_id INTEGER NOT NULL,
            revision_id INTEGER NOT NULL,
            PRIMARY KEY (report_id, slot),
            UNIQUE (report_id, station_id),
            UNIQUE (report_id, acceptance_id)
        )
    ''')


def commit_report(
    db: sqlite3.Connection, plan_id: str, reference_interval: tuple[Fraction, Fraction],
    minimum_error: Fraction, sources: tuple[ReportSource, ReportSource],
) -> SignedReport:
    if len(sources) != 2 or sources[0].station_id == sources[1].station_id:
        raise ValueError('a report requires two distinct station sources')
    lower, upper = reference_interval
    db.execute('BEGIN IMMEDIATE')
    with db:
        report_id = db.execute('''
            INSERT INTO signed_reports (plan_id, lower_reference, upper_reference, minimum_error)
            VALUES (?, ?, ?, ?)
        ''', (plan_id, encode_rational(lower), encode_rational(upper), encode_rational(minimum_error))).lastrowid
        db.executemany('''
            INSERT INTO report_sources (report_id, slot, station_id, acceptance_id, bundle_id, revision_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            (report_id, slot, source.station_id, source.acceptance_id, source.bundle_id, source.revision_id)
            for slot, source in enumerate(sources)
        ))
    return SignedReport(report_id, plan_id, reference_interval, minimum_error, sources)


def load_report(db: sqlite3.Connection, report_id: int) -> SignedReport:
    if type(report_id) is not int or not 1 <= report_id < 2**63:
        raise ValueError('report ID must be a positive SQLite integer')
    row = db.execute('''
        SELECT plan_id, lower_reference, upper_reference, minimum_error
        FROM signed_reports WHERE report_id = ?
    ''', (report_id,)).fetchone()
    if row is None:
        raise LookupError(f'unknown report {report_id}')
    plan_id, lower, upper, error = row
    rows = db.execute('''
        SELECT station_id, acceptance_id, bundle_id, revision_id
        FROM report_sources WHERE report_id = ? ORDER BY slot
    ''', (report_id,)).fetchall()
    if len(rows) != 2:
        raise sqlite3.DatabaseError(f'report {report_id} has incomplete fixed sources')
    return SignedReport(
        report_id, plan_id, (parse_rational(lower), parse_rational(upper)), parse_rational(error),
        (ReportSource(*rows[0]), ReportSource(*rows[1])),
    )
