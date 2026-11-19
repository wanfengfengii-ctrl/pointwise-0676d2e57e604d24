import sqlite3

from report.ledger import SignedReport, load_report


def retrieve(db: sqlite3.Connection, report_id: int) -> SignedReport:
    """Read the fixed report and its original sources by report number."""
    return load_report(db, report_id)
