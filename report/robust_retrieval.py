import sqlite3

from report.robust_ledger import RobustSignedReport, load_robust_report


def retrieve_robust(db: sqlite3.Connection, report_id: int) -> RobustSignedReport:
    """Read the fixed robust report and its original sources by report number."""
    return load_robust_report(db, report_id)
