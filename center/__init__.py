import sqlite3

from center.catalog import initialize_catalog
from plan.registration import initialize_plans
from report.ledger import initialize_reports
from report.robust_ledger import initialize_robust_reports


def initialize_center(db: sqlite3.Connection) -> None:
    """Prepare a center database without opening either station database."""
    db.execute('PRAGMA foreign_keys = ON')
    initialize_plans(db)
    initialize_catalog(db)
    initialize_reports(db)
    initialize_robust_reports(db)
    if db.in_transaction:
        db.commit()
