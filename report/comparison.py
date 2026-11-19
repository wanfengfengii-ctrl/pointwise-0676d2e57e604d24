import sqlite3
from dataclasses import dataclass

from report.ledger import ReportSource, SignedReport
from report.retrieval import retrieve


@dataclass(frozen=True)
class SourceChange:
    station_id: str
    old: ReportSource
    new: ReportSource


@dataclass(frozen=True)
class ReportComparison:
    old: SignedReport
    new: SignedReport
    changed_sources: tuple[SourceChange, ...]


def compare(db: sqlite3.Connection, old_report_id: int, new_report_id: int) -> ReportComparison:
    old = retrieve(db, old_report_id)
    new = retrieve(db, new_report_id)
    if old.plan_id != new.plan_id:
        raise ValueError('only reports from the same fixed plan can be compared')
    new_by_station = {source.station_id: source for source in new.sources}
    changes = []
    for original in old.sources:
        replacement = new_by_station[original.station_id]
        if (original.bundle_id, original.revision_id) != (replacement.bundle_id, replacement.revision_id):
            changes.append(SourceChange(original.station_id, original, replacement))
    return ReportComparison(old, new, tuple(changes))
