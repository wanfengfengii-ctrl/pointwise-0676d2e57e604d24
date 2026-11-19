import sqlite3
from dataclasses import dataclass

from center.catalog import AcceptedBundle, load_accepted
from plan.registration import PlanSpec
from plan.stations import require_applicable_station


@dataclass(frozen=True)
class SelectedBundles:
    plan_id: str
    entries: tuple[AcceptedBundle, AcceptedBundle]


def select_bundles(
    db: sqlite3.Connection, plan: PlanSpec, first_id: int, second_id: int,
) -> SelectedBundles:
    by_station = {}
    for acceptance_id in (first_id, second_id):
        entry = load_accepted(db, acceptance_id)
        if entry.bundle.plan_id != plan.plan_id:
            raise ValueError('selected bundle belongs to a different plan')
        station = require_applicable_station(entry.bundle.station_id, plan.stations)
        if station in by_station:
            raise ValueError('select one accepted bundle from each of the two plan stations')
        by_station[station] = entry
    first, second = plan.stations
    return SelectedBundles(plan.plan_id, (by_station[first], by_station[second]))
