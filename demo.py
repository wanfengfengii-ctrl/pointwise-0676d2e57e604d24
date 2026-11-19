import json
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from center import initialize_center
from center.imports import accept
from issue.publication import issue
from plan.rationals import encode_rational
from plan.reference import ReferenceSpec
from plan.registration import PlanSpec, register
from report.comparison import compare
from report.ledger import SignedReport
from report.publication import finalize
from report.retrieval import retrieve
from station import initialize_station
from station.intake import record
from station.revisions import freeze_revision


def summarize(report: SignedReport) -> dict:
    return {
        'interval': [encode_rational(bound) for bound in report.reference_interval],
        'error': encode_rational(report.minimum_error),
    }


def run(sample: dict, directory: Path) -> None:
    declaration = sample['plan']
    plan = PlanSpec(
        declaration['plan_id'], declaration['instrument'], declaration['unit'],
        ReferenceSpec(declaration['reference_position']), tuple(declaration['stations']),
    )
    station_paths = {station: directory / f'station-{index}.sqlite' for index, station in enumerate(plan.stations)}
    revisions = {}
    outgoing = {}
    for data in sample['readings']:
        station = data['station_id']
        with closing(sqlite3.connect(station_paths[station])) as source:
            initialize_station(source)
            register(source, plan)
            record(source, plan.plan_id, **data)
            revisions[station] = freeze_revision(source, plan.plan_id, station)
            check, bundle = issue(source, plan.plan_id, station, revisions[station].revision_id)
            outgoing[station] = bundle.to_json()
            print(f'{station} 本站最优 {tuple(map(str, check.reference_interval))}，最小误差 {check.minimum_error}')

    center_path = directory / 'center.sqlite'
    first, second = plan.stations
    with closing(sqlite3.connect(center_path)) as center:
        initialize_center(center)
        register(center, plan)
        first_id, second_id = (accept(center, outgoing[station]) for station in plan.stations)
        old = finalize(center, plan.plan_id, first_id, second_id)
        print(f'定稿 {old.report_id} {summarize(old)}')

    correction = sample['correction']
    changed_station = correction['station_id']
    with closing(sqlite3.connect(station_paths[changed_station])) as source:
        record(source, plan.plan_id, base_revision_id=revisions[changed_station].revision_id, **correction)
        revision = freeze_revision(source, plan.plan_id, changed_station)
        _, corrected = issue(source, plan.plan_id, changed_station, revision.revision_id)

    selections = {first: first_id, second: second_id}
    with closing(sqlite3.connect(center_path)) as center:
        selections[changed_station] = accept(center, corrected.to_json())
        new = finalize(center, plan.plan_id, selections[first], selections[second])
        print(f'更正定稿 {new.report_id} {summarize(new)}')

    with closing(sqlite3.connect(center_path)) as center:
        fixed_old = retrieve(center, old.report_id)
        fixed_new = retrieve(center, new.report_id)
        comparison = compare(center, fixed_old.report_id, fixed_new.report_id)
    expected = {'old': {'interval': ['2', '8'], 'error': '6'}, 'new': {'interval': ['8', '8'], 'error': '6'}}
    actual = {'old': summarize(fixed_old), 'new': summarize(fixed_new)}
    expected_changes = ['甲站']
    changes = [change.station_id for change in comparison.changed_sources]
    if actual != expected or changes != expected_changes or (fixed_old, fixed_new) != (old, new):
        raise RuntimeError('交班核账与样例预期不符')
    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': 'v8',
        'input': {'readings': sample['readings'], 'correction': correction},
        'expected': expected,
        'actual': actual,
        'downstream_expected': {'changed_stations': expected_changes, 'history_preserved': True},
        'downstream_actual': {'changed_stations': changes, 'history_preserved': (fixed_old, fixed_new) == (old, new)},
        'code_paths': ['station/intake.py', 'issue/publication.py', 'center/imports.py', 'report/publication.py',
                       'report/retrieval.py', 'report/comparison.py'],
        'oracle': '手算 max(0,8-t)+max(0,t-2) 与更正后的 2*max(0,8-t)+max(0,t-2)，并按原报告号对照固定选件。',
    }, ensure_ascii=False, sort_keys=True))


def main() -> None:
    sample = json.loads(Path(__file__).with_name('sample.json').read_text(encoding='utf-8'))
    with TemporaryDirectory(prefix='hengqiao-demo-') as directory:
        run(sample, Path(directory))


if __name__ == '__main__':
    main()
