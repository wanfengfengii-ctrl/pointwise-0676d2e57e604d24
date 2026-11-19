import sqlite3

from center.bundle_validation import validate_bundle
from center.catalog import store_accepted
from issue.artifacts import StationBundle
from plan.registration import load_plan


def accept(db: sqlite3.Connection, incoming: StationBundle | str) -> int:
    if isinstance(incoming, str):
        bundle = StationBundle.from_json(incoming)
    elif isinstance(incoming, StationBundle):
        bundle = incoming
    else:
        raise ValueError('expected a station bundle or its JSON text')
    plan = load_plan(db, bundle.plan_id)
    validate_bundle(bundle, plan)
    return store_accepted(db, bundle)
