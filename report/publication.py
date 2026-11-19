import sqlite3

from center.selection import select_bundles
from issue.evidence import decode_evidence
from plan.registration import load_plan
from report.joint_fit import compute_joint_optimum
from report.ledger import ReportSource, SignedReport, commit_report


def finalize(
    db: sqlite3.Connection, plan_id: str, first_acceptance_id: int, second_acceptance_id: int,
) -> SignedReport:
    plan = load_plan(db, plan_id)
    selected = select_bundles(db, plan, first_acceptance_id, second_acceptance_id)
    first, second = selected.entries
    optimum = compute_joint_optimum(
        decode_evidence(first.bundle.evidence, plan.reference.domain),
        decode_evidence(second.bundle.evidence, plan.reference.domain),
    )
    sources = tuple(
        ReportSource(entry.bundle.station_id, entry.acceptance_id, entry.bundle.bundle_id, entry.bundle.revision_id)
        for entry in (first, second)
    )
    return commit_report(
        db, selected.plan_id, optimum.reference_interval, optimum.minimum_error, sources,
    )
