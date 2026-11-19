import sqlite3

from center.selection import select_bundles
from issue.robust_evidence import decode_robust_evidence
from plan.registration import load_plan
from report.ledger import ReportSource
from report.robust_fit import compute_robust_optimum
from report.robust_ledger import commit_robust_report


def finalize_robust(
    db: sqlite3.Connection, plan_id: str,
    first_acceptance_id: int, second_acceptance_id: int,
):
    """Sign the robust report from exactly two V2 acceptances, one per plan station."""
    plan = load_plan(db, plan_id)
    if plan.offset_interval is None:
        raise ValueError('the fixed plan declares no shared offset interval')
    selected = select_bundles(db, plan, first_acceptance_id, second_acceptance_id, 2)
    first, second = selected.entries
    first_evidence = decode_robust_evidence(first.bundle.evidence, plan.reference.domain)
    second_evidence = decode_robust_evidence(second.bundle.evidence, plan.reference.domain)
    if first_evidence.offset_interval != plan.offset_interval or \
            second_evidence.offset_interval != plan.offset_interval:
        raise ValueError('robust evidence offset interval differs from the fixed plan')
    optimum = compute_robust_optimum(first_evidence, second_evidence)
    sources = tuple(
        ReportSource(
            entry.bundle.station_id, entry.acceptance_id,
            entry.bundle.bundle_id, entry.bundle.revision_id,
        )
        for entry in (first, second)
    )
    # The report body and both sources commit together or roll back together.
    return commit_robust_report(
        db, selected.plan_id, plan.offset_interval,
        optimum.reference_interval, optimum.minimum_error, sources,
    )
