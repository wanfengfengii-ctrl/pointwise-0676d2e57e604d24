import sqlite3

from issue.artifacts import initialize_station_artifacts
from plan.registration import initialize_plans
from station.ledger import initialize_readings
from station.revisions import initialize_revisions


def initialize_station(db: sqlite3.Connection) -> None:
    """Prepare a new or existing station database for use."""
    db.execute('PRAGMA foreign_keys = ON')
    initialize_plans(db)
    initialize_readings(db)
    initialize_revisions(db)
    initialize_station_artifacts(db)
    if db.in_transaction:
        db.commit()
