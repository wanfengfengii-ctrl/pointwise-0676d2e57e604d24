import sqlite3

from station.ledger import Reading, replace_draft_member
from station.revisions import load_revision


def stage_correction(db: sqlite3.Connection, replacement: Reading, base_revision_id: int) -> None:
    """Validate the frozen source before changing the working queue."""
    base = load_revision(db, base_revision_id)
    if (base.plan_id, base.station_id) != (replacement.plan_id, replacement.station_id):
        raise ValueError('correction base does not belong to the requested plan and station')
    if not any(reading.reading_id == replacement.replaces for reading in base.readings):
        raise ValueError('correction target is not an effective member of the chosen revision')
    replace_draft_member(db, replacement, base.revision_id, base.readings)
