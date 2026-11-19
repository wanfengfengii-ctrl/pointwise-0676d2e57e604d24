import sqlite3
from dataclasses import dataclass
from fractions import Fraction

from plan.rationals import encode_rational, parse_rational
from report.ledger import ReportSource


@dataclass(frozen=True)
class RobustSignedReport:
    report_id: int
    plan_id: str
    offset_interval: tuple[Fraction, Fraction]
    reference_interval: tuple[Fraction, Fraction]
    minimum_error: Fraction
    sources: tuple[ReportSource, ReportSource]


def initialize_robust_reports(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS robust_signed_reports (
            report_id INTEGER PRIMARY KEY,
            plan_id TEXT NOT NULL REFERENCES calibration_plans(plan_id),
            lower_offset TEXT NOT NULL,
            upper_offset TEXT NOT NULL,
            lower_reference TEXT NOT NULL,
            upper_reference TEXT NOT NULL,
            minimum_error TEXT NOT NULL
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS robust_report_sources (
            report_id INTEGER NOT NULL REFERENCES robust_signed_reports(report_id),
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


def commit_robust_report(
    db: sqlite3.Connection, plan_id: str,
    offset_interval: tuple[Fraction, Fraction],
    reference_interval: tuple[Fraction, Fraction],
    minimum_error: Fraction, sources: tuple[ReportSource, ReportSource],
) -> RobustSignedReport:
    if len(sources) != 2 or sources[0].station_id == sources[1].station_id:
        raise ValueError('a report requires two distinct station sources')
    lower, upper = reference_interval
    offset_lower, offset_upper = offset_interval
    db.execute('BEGIN IMMEDIATE')
    with db:
        report_id = db.execute('''
            INSERT INTO robust_signed_reports (
                plan_id, lower_offset, upper_offset, lower_reference,
                upper_reference, minimum_error
            ) VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            plan_id, encode_rational(offset_lower), encode_rational(offset_upper),
            encode_rational(lower), encode_rational(upper), encode_rational(minimum_error),
        )).lastrowid
        db.executemany('''
            INSERT INTO robust_report_sources (
                report_id, slot, station_id, acceptance_id, bundle_id, revision_id
            )
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            (report_id, slot, source.station_id, source.acceptance_id,
             source.bundle_id, source.revision_id)
            for slot, source in enumerate(sources)
        ))
    return RobustSignedReport(
        report_id, plan_id, offset_interval, reference_interval, minimum_error, sources,
    )


def load_robust_report(db: sqlite3.Connection, report_id: int) -> RobustSignedReport:
    if type(report_id) is not int or not 1 <= report_id < 2**63:
        raise ValueError('report ID must be a positive SQLite integer')
    row = db.execute('''
        SELECT plan_id, lower_offset, upper_offset, lower_reference,
               upper_reference, minimum_error
        FROM robust_signed_reports WHERE report_id = ?
    ''', (report_id,)).fetchone()
    if row is None:
        raise LookupError(f'unknown robust report {report_id}')
    plan_id, offset_lower, offset_upper, lower, upper, error = row
    rows = db.execute('''
        SELECT station_id, acceptance_id, bundle_id, revision_id
        FROM robust_report_sources WHERE report_id = ? ORDER BY slot
    ''', (report_id,)).fetchall()
    if len(rows) != 2:
        raise sqlite3.DatabaseError(f'robust report {report_id} has incomplete fixed sources')
    return RobustSignedReport(
        report_id, plan_id,
        (parse_rational(offset_lower), parse_rational(offset_upper)),
        (parse_rational(lower), parse_rational(upper)), parse_rational(error),
        (ReportSource(*rows[0]), ReportSource(*rows[1])),
    )
