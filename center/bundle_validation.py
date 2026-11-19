from issue.artifacts import StationBundle
from issue.evidence import decode_evidence
from issue.robust_evidence import decode_robust_evidence
from plan.reference import validate_position
from plan.registration import PlanSpec
from plan.stations import require_applicable_station, validate_station_pair


def validate_bundle(bundle: StationBundle, plan: PlanSpec) -> None:
    if type(bundle.version) is not int or bundle.version not in (1, 2):
        raise ValueError('unsupported station bundle version')
    for identifier in (bundle.bundle_id, bundle.revision_id):
        if type(identifier) is not int or not 1 <= identifier < 2**63:
            raise ValueError('bundle and revision IDs must be positive SQLite integers')
    require_applicable_station(bundle.station_id, plan.stations)
    stations = validate_station_pair(bundle.stations)
    position = validate_position(bundle.reference_position)
    if (bundle.plan_id, bundle.instrument, bundle.unit, position, stations) != (
        plan.plan_id, plan.instrument, plan.unit, plan.reference.position, plan.stations,
    ):
        raise ValueError('bundle declaration differs from the fixed plan')
    if bundle.domain != plan.reference.domain:
        raise ValueError('bundle domain differs from the fixed plan')
    if bundle.version == 1:
        decode_evidence(bundle.evidence, bundle.domain)
        return
    evidence = decode_robust_evidence(bundle.evidence, bundle.domain)
    if plan.offset_interval is None:
        raise ValueError('the fixed plan declares no shared offset interval')
    if evidence.offset_interval != plan.offset_interval:
        raise ValueError('robust evidence offset interval differs from the fixed plan')
