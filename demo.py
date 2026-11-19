import json
import sqlite3
from contextlib import closing
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

from center import initialize_center
from center.imports import accept
from issue.publication import issue
from issue.robust_publication import issue_robust
from plan.rationals import encode_rational
from plan.reference import ReferenceSpec
from plan.registration import PlanSpec, register
from report.comparison import compare
from report.ledger import SignedReport
from report.publication import finalize
from report.robust_publication import finalize_robust
from report.robust_retrieval import retrieve_robust
from station import initialize_station
from station.intake import record
from station.revisions import freeze_revision


def summarize(report: SignedReport) -> dict:
    return {
        'interval': [encode_rational(bound) for bound in report.reference_interval],
        'error': encode_rational(report.minimum_error),
    }


def run_v1_handoff(sample: dict, directory: Path) -> None:
    """The original V1 shift handoff, unchanged in entry points and conclusions."""
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
        fixed_old = retrieve_v1(center, old.report_id)
        fixed_new = retrieve_v1(center, new.report_id)
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


def retrieve_v1(db, report_id):
    from report.retrieval import retrieve
    return retrieve(db, report_id)


def robust_summary(report) -> dict:
    return {
        'offset_interval': [encode_rational(bound) for bound in report.offset_interval],
        'reference_interval': [encode_rational(bound) for bound in report.reference_interval],
        'minimum_error': encode_rational(report.minimum_error),
        'sources': [
            {
                'station_id': source.station_id,
                'acceptance_id': source.acceptance_id,
                'bundle_id': source.bundle_id,
                'revision_id': source.revision_id,
            }
            for source in report.sources
        ],
    }


def _robust_plan(sample: dict, case: dict) -> PlanSpec:
    return PlanSpec(
        case['plan_id'], sample['instrument'], sample['unit'],
        ReferenceSpec(sample['reference_position']), tuple(sample['stations']),
        tuple(Fraction(token) for token in case['offset_interval']),
    )


def run_robust_case(sample: dict, case: dict, directory: Path) -> dict:
    directory.mkdir(exist_ok=True)
    plan = _robust_plan(sample, case)
    station_paths = {
        station: directory / f'{case["plan_id"]}-{station}.sqlite'
        for station in plan.stations
    }
    revisions = {}
    v2_bundles = {}
    for data in case['readings']:
        station = data['station_id']
        with closing(sqlite3.connect(station_paths[station])) as source:
            initialize_station(source)
            register(source, plan)
            record(source, plan.plan_id, **data)
            revisions[station] = freeze_revision(source, plan.plan_id, station)
            v2_bundles[station] = issue_robust(
                source, plan.plan_id, station, revisions[station].revision_id,
            )

    center_path = directory / f'{case["plan_id"]}-center.sqlite'
    with closing(sqlite3.connect(center_path)) as center:
        initialize_center(center)
        register(center, plan)
        accepted = {
            station: accept(center, bundle.to_json())
            for station, bundle in v2_bundles.items()
        }
        # The two station source databases go offline before the center signs.
        for path in station_paths.values():
            path.replace(path.with_suffix('.offline'))
        report = finalize_robust(
            center, plan.plan_id, accepted[plan.stations[0]], accepted[plan.stations[1]],
        )
        print(f'断源稳健定稿 {report.report_id}（{plan.plan_id}） {robust_summary(report)}')

    corrected_acceptance = None
    corrected_report = None
    if 'correction' in case:
        correction = dict(case['correction'])
        changed_station = correction.pop('station_id')
        offline = station_paths[changed_station].with_suffix('.offline')
        reopened_path = station_paths[changed_station]
        offline.replace(reopened_path)
        with closing(sqlite3.connect(reopened_path)) as source:
            initialize_station(source)
            record(
                source, plan.plan_id, changed_station,
                base_revision_id=revisions[changed_station].revision_id, **correction,
            )
            corrected_revision = freeze_revision(source, plan.plan_id, changed_station)
            corrected_bundle = issue_robust(
                source, plan.plan_id, changed_station, corrected_revision.revision_id,
            )
        reopened_path.replace(offline)
        with closing(sqlite3.connect(center_path)) as center:
            corrected_acceptance = accept(center, corrected_bundle.to_json())
            selection = {plan.stations[0]: accepted[plan.stations[0]],
                         plan.stations[1]: accepted[plan.stations[1]]}
            selection[changed_station] = corrected_acceptance
            corrected_report = finalize_robust(
                center, plan.plan_id, selection[plan.stations[0]], selection[plan.stations[1]],
            )
            print(f'更正后稳健定稿 {corrected_report.report_id} {robust_summary(corrected_report)}')

    # Reopen the center later and retrieve every fixed result strictly by number.
    with closing(sqlite3.connect(center_path)) as reopened:
        fixed = retrieve_robust(reopened, report.report_id)
        fixed_corrected = (
            retrieve_robust(reopened, corrected_report.report_id)
            if corrected_report is not None else None
        )
    if fixed != report:
        raise RuntimeError('重开中心库后稳健报告与定稿不一致')
    actual = robust_summary(fixed)
    expected = {
        'offset_interval': list(case['offset_interval']),
        'reference_interval': case['expected']['reference_interval'],
        'minimum_error': case['expected']['minimum_error'],
    }
    comparison = {
        'offset_interval': actual['offset_interval'],
        'reference_interval': actual['reference_interval'],
        'minimum_error': actual['minimum_error'],
    }
    if comparison != expected:
        raise RuntimeError(f'{case["plan_id"]} 稳健核账与样例预期不符: {comparison} != {expected}')
    result = {'case_id': case['plan_id'], 'expected': expected, 'actual': comparison,
              'sources': actual['sources']}
    if fixed_corrected is not None:
        after = robust_summary(fixed_corrected)
        expected_after = case['expected_after_correction']
        if (after['reference_interval'], after['minimum_error']) != (
            expected_after['reference_interval'], expected_after['minimum_error'],
        ):
            raise RuntimeError('更正后稳健核账与样例预期不符')
        result['expected_after_correction'] = expected_after
        result['actual_after_correction'] = {
            'reference_interval': after['reference_interval'],
            'minimum_error': after['minimum_error'],
        }
        result['changed_acceptance_id'] = corrected_acceptance
    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': f'robust-{case["plan_id"]}',
        'input': {'readings': case['readings'], 'correction': case.get('correction')},
        'expected': expected,
        'actual': comparison,
        'downstream_expected': {'recovered': True,
                                'recovered_corrected': fixed_corrected is not None},
        'downstream_actual': {
            'recovered': fixed == report,
            'recovered_corrected': (
                fixed_corrected is not None and fixed_corrected == corrected_report
            ),
        },
        'code_paths': ['issue/robust_publication.py', 'center/imports.py',
                       'report/robust_fit.py', 'report/robust_publication.py',
                       'report/robust_retrieval.py'],
        'oracle': '修正值=原值+灵敏度*偏移；共同 t、d 先合计两站损失，再在固定偏移闭区间取最坏值。',
    }, ensure_ascii=False, sort_keys=True))
    return result


def main() -> None:
    root = Path(__file__).resolve().parent
    sample = json.loads(root.joinpath('sample.json').read_text(encoding='utf-8'))
    robust_sample = json.loads(root.joinpath('shared_offset_sample.json').read_text(encoding='utf-8'))
    with TemporaryDirectory(prefix='hengqiao-demo-') as directory:
        workdir = Path(directory)
        print('== V1 交班链路 ==')
        v1_dir = workdir / 'v1'
        v1_dir.mkdir()
        run_v1_handoff(sample, v1_dir)
        print('== V2 共享偏移稳健链路 ==')
        for case in robust_sample['cases']:
            run_robust_case(robust_sample, case, workdir / case['plan_id'])


if __name__ == '__main__':
    main()
