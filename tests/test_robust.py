import json
import sqlite3
import unittest
from contextlib import closing
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

from center import initialize_center
from center.catalog import load_accepted
from center.imports import accept
from issue.artifacts import StationBundle
from issue.publication import issue
from issue.robust_evidence import decode_robust_evidence
from issue.robust_publication import issue_robust
from plan.reference import ReferenceSpec
from plan.registration import PlanSpec, register
from report.publication import finalize
from report.robust_publication import finalize_robust
from report.robust_retrieval import retrieve_robust
from station import initialize_station
from station.intake import record
from station.revisions import freeze_revision


def install_robust_commit_failure(db):
    db.executescript('''
        CREATE TABLE fixture_gate (station TEXT PRIMARY KEY);
        CREATE TABLE fixture_deferred (
            station TEXT REFERENCES fixture_gate(station) DEFERRABLE INITIALLY DEFERRED
        );
        CREATE TRIGGER fixture_fail_commit AFTER INSERT ON robust_report_sources
        WHEN NEW.slot = 1
        BEGIN
            INSERT INTO fixture_deferred (station) VALUES (NEW.station_id);
        END;
    ''')


class RobustLab:
    """Two independent station databases plus an independently opened center."""

    def __init__(self, root: Path, plan: PlanSpec):
        self.root = root
        self.plan = plan
        self.center_path = root / 'center.sqlite'
        self.paths = {station: root / f'{station}.sqlite' for station in plan.stations}
        self.revisions = {}
        self.bundles_v2 = {}
        self.accepted_v2 = {}

    def center(self):
        return closing(sqlite3.connect(self.center_path))

    def source(self, station):
        return closing(sqlite3.connect(self.paths[station]))

    def record_case(self, readings):
        for data in readings:
            station = data['station_id']
            with self.source(station) as source:
                initialize_station(source)
                register(source, self.plan)
                record(source, self.plan.plan_id, **data)
                self.revisions[station] = freeze_revision(source, self.plan.plan_id, station)

    def issue_v2(self, station):
        with self.source(station) as source:
            bundle = issue_robust(
                source, self.plan.plan_id, station, self.revisions[station].revision_id,
            )
        self.bundles_v2[station] = bundle
        return bundle

    def prepare(self, readings):
        self.record_case(readings)
        with self.center() as center:
            initialize_center(center)
            register(center, self.plan)
            for station in self.plan.stations:
                bundle = self.issue_v2(station)
                self.accepted_v2[station] = accept(center, bundle.to_json())
        return self

    def detach_sources(self):
        for path in self.paths.values():
            path.replace(path.with_suffix('.offline'))

    def finalize(self, center, first=None, second=None):
        return finalize_robust(
            center, self.plan.plan_id,
            self.accepted_v2['A'] if first is None else first,
            self.accepted_v2['B'] if second is None else second,
        )


SAMPLE = json.loads(
    Path(__file__).resolve().parent.parent.joinpath('shared_offset_sample.json').read_text('utf-8')
)
CASES = {case['plan_id']: case for case in SAMPLE['cases']}


def make_plan(case_id: str) -> PlanSpec:
    case = CASES[case_id]
    return PlanSpec(
        case['plan_id'], SAMPLE['instrument'], SAMPLE['unit'],
        ReferenceSpec(SAMPLE['reference_position']), tuple(SAMPLE['stations']),
        tuple(Fraction(token) for token in case['offset_interval']),
    )


class RobustChainTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory(prefix='hengqiao-robust-'))
        self.root = Path(directory)

    def lab(self, case_id):
        path = self.root / case_id
        path.mkdir()
        return RobustLab(path, make_plan(case_id)).prepare(CASES[case_id]['readings'])

    def assert_robust(self, report, interval, error):
        self.assertEqual(report.reference_interval, tuple(Fraction(x) for x in interval))
        self.assertEqual(report.minimum_error, Fraction(error))

    def test_sample_interior_and_zero_offset(self):
        for case_id, expected in (
            ('HB-interior', (['9/2', '9/2'], '4')),
            ('HB-zero-offset', (['2', '8'], '6')),
        ):
            with self.subTest(case_id=case_id):
                lab = self.lab(case_id)
                lab.detach_sources()
                with lab.center() as center:
                    report = lab.finalize(center)
                    fixed = retrieve_robust(center, report.report_id)
                self.assert_robust(report, *expected)
                self.assertEqual(fixed, report)
                self.assertEqual(report.offset_interval, lab.plan.offset_interval)
                self.assertEqual(
                    [source.station_id for source in report.sources],
                    list(lab.plan.stations),
                )

    def test_sign_change_with_correction_and_reopen(self):
        lab = self.lab('HB-sign-change')
        # The original A artifact is kept while station B corrects its sensitivity.
        correction = dict(CASES['HB-sign-change']['correction'])
        station = correction.pop('station_id')
        with lab.source(station) as source:
            initialize_station(source)
            record(
                source, lab.plan.plan_id, station,
                base_revision_id=lab.revisions[station].revision_id, **correction,
            )
            corrected_revision = freeze_revision(source, lab.plan.plan_id, station)
            _, v1_bundle = issue(
                source, lab.plan.plan_id, station, corrected_revision.revision_id,
            )
            corrected_v2 = issue_robust(
                source, lab.plan.plan_id, station, corrected_revision.revision_id,
            )
        lab.detach_sources()
        with lab.center() as center:
            original = lab.finalize(center)
            corrected_acceptance = accept(center, corrected_v2.to_json())
            corrected_report = lab.finalize(center, second=corrected_acceptance)
        expected_original = CASES['HB-sign-change']['expected']
        expected_after = CASES['HB-sign-change']['expected_after_correction']
        self.assert_robust(
            original, expected_original['reference_interval'],
            expected_original['minimum_error'],
        )
        self.assert_robust(
            corrected_report, expected_after['reference_interval'],
            expected_after['minimum_error'],
        )
        # Reopen the center later and collect both fixed conclusions by report number.
        with lab.center() as reopened:
            first = retrieve_robust(reopened, original.report_id)
            second = retrieve_robust(reopened, corrected_report.report_id)
        self.assertEqual((first, second), (original, corrected_report))
        self.assertEqual(first.sources, original.sources)
        self.assertEqual(second.sources[1].acceptance_id, corrected_acceptance)
        self.assertEqual(second.sources[0], original.sources[0])
        self.assertEqual(v1_bundle.version, 1)
        self.assertEqual(corrected_v2.version, 2)
        self.assertEqual(corrected_v2.bundle_id, corrected_revision.revision_id)

    def test_dual_versions_coexist_and_resubmission(self):
        lab = self.lab('HB-zero-offset')
        station = 'A'
        with lab.source(station) as source:
            v1_check, v1_bundle = issue(
                source, lab.plan.plan_id, station, lab.revisions[station].revision_id,
            )
            v2_bundle = issue_robust(
                source, lab.plan.plan_id, station, lab.revisions[station].revision_id,
            )
            reissued_v1, same_v1 = issue(
                source, lab.plan.plan_id, station, lab.revisions[station].revision_id,
            )
            same_v2 = issue_robust(
                source, lab.plan.plan_id, station, lab.revisions[station].revision_id,
            )
        self.assertEqual(same_v1, v1_bundle)
        self.assertEqual(same_v2, v2_bundle)
        self.assertNotEqual(v1_bundle, v2_bundle)
        wire_v2 = v2_bundle.to_json()
        self.assertIn('"version":2', wire_v2)
        evidence = decode_robust_evidence(v2_bundle.evidence, v2_bundle.domain)
        self.assertEqual(evidence.offset_interval, (Fraction(0), Fraction(0)))
        # No readings, reading numbers, sensitivities or private positions leave the station.
        for secret in ('A-base', 'sensitivity', 'readings'):
            self.assertNotIn(secret, wire_v2)
        with lab.center() as center:
            v1_acceptance = accept(center, v1_bundle)
            v2_acceptance = accept(center, v2_bundle)
            # Object, canonical JSON, altered whitespace and key order all re-resolve.
            self.assertEqual(accept(center, v2_bundle), v2_acceptance)
            reordered = json.dumps(json.loads(wire_v2), indent=2)
            self.assertEqual(accept(center, reordered), v2_acceptance)
            shuffled = json.dumps(
                dict(reversed(list(json.loads(wire_v2).items()))), sort_keys=False,
            )
            self.assertEqual(accept(center, shuffled), v2_acceptance)
            self.assertEqual(
                load_accepted(center, v2_acceptance).bundle, v2_bundle,
            )
            self.assertNotEqual(v1_acceptance, v2_acceptance)
            # V1 and V2 finalization each require their own homogeneous versions.
            with self.assertRaisesRegex(ValueError, 'version'):
                finalize(
                    center, lab.plan.plan_id, v1_acceptance, lab.accepted_v2['B'],
                )
            with self.assertRaisesRegex(ValueError, 'version'):
                finalize_robust(
                    center, lab.plan.plan_id, v1_acceptance, lab.accepted_v2['B'],
                )
        self.assertEqual(v1_bundle.bundle_id, v2_bundle.bundle_id)

    def test_identity_conflict_rejected(self):
        lab = self.lab('HB-zero-offset')
        good = lab.bundles_v2['A']
        evidence = decode_robust_evidence(good.evidence, good.domain)
        tampered = replace(
            good,
            evidence_json=json.dumps({
                'offset_interval': ['0', '0'],
                'lower_loss': {'points': [['0', '1'], ['10', '1']]},
                'upper_loss': {'points': [['0', '1'], ['10', '1']]},
            }, sort_keys=True, separators=(',', ':')),
        )
        with lab.center() as center:
            prior = center.execute('SELECT COUNT(*) FROM accepted_bundles').fetchone()
            with self.assertRaisesRegex(ValueError, 'identity'):
                accept(center, tampered)
            self.assertEqual(
                center.execute('SELECT COUNT(*) FROM accepted_bundles').fetchone(), prior,
            )
            self.assertEqual(accept(center, good.to_json()), lab.accepted_v2['A'])
        self.assertEqual(evidence.offset_interval, (Fraction(0), Fraction(0)))

    def test_wrong_offset_interval_rejected(self):
        lab = self.lab('HB-zero-offset')
        good = lab.bundles_v2['A']
        rewritten = json.loads(good.to_json())
        rewritten['evidence']['offset_interval'] = ['-1', '1']
        with lab.center() as center:
            with self.assertRaisesRegex(ValueError, 'offset interval differs'):
                accept(center, json.dumps(rewritten))

    def test_missing_sensitivity_blocks_v2(self):
        plan = make_plan('HB-interior')
        root = self.root / 'missing-sensitivity'
        root.mkdir()
        lab = RobustLab(root, plan)
        lab.record_case(CASES['HB-interior']['readings'])
        with lab.source('A') as source:
            record(
                source, plan.plan_id, 'A', 'A-extra', -2, 3, 1,
                base_revision_id=lab.revisions['A'].revision_id,
                replaces='A-base',
            )
            revision = freeze_revision(source, plan.plan_id, 'A')
            with self.assertRaisesRegex(ValueError, 'sensitivity'):
                issue_robust(source, plan.plan_id, 'A', revision.revision_id)
            # Nominal V1 issuance remains available on the same frozen revision.
            check, v1_bundle = issue(source, plan.plan_id, 'A', revision.revision_id)
            self.assertEqual(v1_bundle.version, 1)
            self.assertEqual(check.revision_id, revision.revision_id)

    def test_out_of_domain_corrected_reading_blocks_v2(self):
        # value 9 with sensitivity 2 reaches 11 at the upper endpoint d = 1.
        plan = PlanSpec(
            'HB-out', 'gauge', 'mm', ReferenceSpec(0), ('A', 'B'),
            (Fraction(-1), Fraction(1)),
        )
        root = self.root / 'out-of-domain'
        root.mkdir()
        with closing(sqlite3.connect(root / 'A.sqlite')) as source:
            initialize_station(source)
            register(source, plan)
            record(source, plan.plan_id, 'A', 'A-base', 0, 9, 1, sensitivity=2)
            revision = freeze_revision(source, plan.plan_id, 'A')
            with self.assertRaisesRegex(ValueError, 'leaves'):
                issue_robust(source, plan.plan_id, 'A', revision.revision_id)
            # The blocked issuance leaves no V2 artifact behind.
            self.assertEqual(
                source.execute(
                    "SELECT COUNT(*) FROM station_bundles WHERE version = 2"
                ).fetchone(),
                (0,),
            )

    def test_plan_without_offset_rejects_v2(self):
        plan = PlanSpec('HB-v1-only', 'gauge', 'mm', ReferenceSpec(0), ('A', 'B'))
        root = self.root / 'no-offset'
        root.mkdir()
        with closing(sqlite3.connect(root / 'A.sqlite')) as source:
            initialize_station(source)
            register(source, plan)
            record(source, plan.plan_id, 'A', 'A-base', 0, 5, 1, sensitivity=1)
            revision = freeze_revision(source, plan.plan_id, 'A')
            with self.assertRaisesRegex(ValueError, 'offset interval'):
                issue_robust(source, plan.plan_id, 'A', revision.revision_id)
        with closing(sqlite3.connect(root / 'center.sqlite')) as center:
            initialize_center(center)
            register(center, plan)
            with self.assertRaisesRegex(ValueError, 'offset interval'):
                finalize_robust(center, plan.plan_id, 1, 2)

    def test_same_station_unknown_and_lookup_numbers(self):
        lab = self.lab('HB-interior')
        lab.detach_sources()
        with lab.center() as center:
            with self.assertRaisesRegex(ValueError, 'each'):
                lab.finalize(center, second=lab.accepted_v2['A'])
            with self.assertRaises(LookupError):
                lab.finalize(center, second=9999)
            with self.assertRaises(LookupError):
                retrieve_robust(center, 9999)

    def test_rollback_then_retry(self):
        lab = self.lab('HB-interior')
        lab.detach_sources()
        with lab.center() as center:
            initialize_center(center)
            install_robust_commit_failure(center)
            with self.assertRaises(sqlite3.IntegrityError):
                lab.finalize(center)
            self.assertFalse(center.in_transaction)
            self.assertEqual(
                center.execute('SELECT COUNT(*) FROM robust_signed_reports').fetchone(), (0,),
            )
            self.assertEqual(
                center.execute('SELECT COUNT(*) FROM robust_report_sources').fetchone(), (0,),
            )
            center.execute('DROP TRIGGER fixture_fail_commit')
            report = lab.finalize(center)
        self.assert_robust(report, ['9/2', '9/2'], '4')
        with lab.center() as reopened:
            self.assertEqual(retrieve_robust(reopened, report.report_id), report)

    def test_v1_era_database_migrates_with_fixed_results(self):
        station_path = self.root / 'legacy-station.sqlite'
        center_path = self.root / 'legacy-center.sqlite'
        wire = StationBundle(
            1, 'HB', 'gauge', 'mm', 0, (Fraction(0), Fraction(10)), ('A', 'B'),
            'A', 1, json.dumps({'points': [['0', '5'], ['5', '0'], ['10', '0']]},
                               sort_keys=True, separators=(',', ':')),
        ).to_json()
        with closing(sqlite3.connect(station_path)) as db:
            db.executescript('''
            CREATE TABLE calibration_plans (plan_id TEXT PRIMARY KEY NOT NULL, instrument TEXT NOT NULL,
                unit TEXT NOT NULL, reference_position TEXT NOT NULL, lower_bound TEXT NOT NULL,
                upper_bound TEXT NOT NULL, first_station TEXT NOT NULL, second_station TEXT NOT NULL,
                CHECK (first_station <> second_station));
            CREATE TABLE reading_facts (plan_id TEXT NOT NULL, station_id TEXT NOT NULL,
                reading_id TEXT NOT NULL, position TEXT NOT NULL, value TEXT NOT NULL,
                weight TEXT NOT NULL, replaces TEXT,
                PRIMARY KEY (plan_id, station_id, reading_id));
            CREATE TABLE station_drafts (plan_id TEXT NOT NULL, station_id TEXT NOT NULL,
                base_revision_id INTEGER, PRIMARY KEY (plan_id, station_id));
            CREATE TABLE draft_members (plan_id TEXT NOT NULL, station_id TEXT NOT NULL,
                reading_id TEXT NOT NULL, PRIMARY KEY (plan_id, station_id, reading_id));
            CREATE TABLE reading_revisions (revision_id INTEGER PRIMARY KEY, plan_id TEXT NOT NULL,
                station_id TEXT NOT NULL, base_revision_id INTEGER);
            CREATE TABLE revision_members (revision_id INTEGER NOT NULL, plan_id TEXT NOT NULL,
                station_id TEXT NOT NULL, ordinal INTEGER NOT NULL, reading_id TEXT NOT NULL,
                PRIMARY KEY (revision_id, ordinal));
            CREATE TABLE station_bundles (bundle_id INTEGER PRIMARY KEY, wire TEXT NOT NULL);
            INSERT INTO calibration_plans VALUES ('HB','gauge','mm','0','0','10','A','B');
            INSERT INTO reading_facts VALUES ('HB','A','r1','0','5','1',NULL);
            INSERT INTO station_drafts VALUES ('HB','A',NULL);
            INSERT INTO draft_members VALUES ('HB','A','r1');
            INSERT INTO reading_revisions VALUES (1,'HB','A',NULL);
            INSERT INTO revision_members VALUES (1,'HB','A',0,'r1');
            ''')
            db.execute('INSERT INTO station_bundles VALUES (1, ?)', (wire,))
            db.commit()
        with closing(sqlite3.connect(center_path)) as db:
            db.executescript('''
            CREATE TABLE calibration_plans (plan_id TEXT PRIMARY KEY NOT NULL, instrument TEXT NOT NULL,
                unit TEXT NOT NULL, reference_position TEXT NOT NULL, lower_bound TEXT NOT NULL,
                upper_bound TEXT NOT NULL, first_station TEXT NOT NULL, second_station TEXT NOT NULL,
                CHECK (first_station <> second_station));
            CREATE TABLE accepted_bundles (acceptance_id INTEGER PRIMARY KEY, plan_id TEXT NOT NULL,
                station_id TEXT NOT NULL, bundle_id INTEGER NOT NULL, wire TEXT NOT NULL,
                UNIQUE (plan_id, station_id, bundle_id));
            CREATE TABLE signed_reports (report_id INTEGER PRIMARY KEY, plan_id TEXT NOT NULL,
                lower_reference TEXT NOT NULL, upper_reference TEXT NOT NULL, minimum_error TEXT NOT NULL);
            CREATE TABLE report_sources (report_id INTEGER NOT NULL, slot INTEGER NOT NULL,
                station_id TEXT NOT NULL, acceptance_id INTEGER NOT NULL, bundle_id INTEGER NOT NULL,
                revision_id INTEGER NOT NULL, PRIMARY KEY (report_id, slot));
            INSERT INTO calibration_plans VALUES ('HB','gauge','mm','0','0','10','A','B');
            ''')
            db.execute('INSERT INTO accepted_bundles VALUES (7,?,?,?,?)',
                       ('HB', 'A', 1, wire))
            db.commit()
        from issue.artifacts import load_station_bundle
        with closing(sqlite3.connect(station_path)) as db:
            initialize_station(db)
            initialize_station(db)
            self.assertEqual(load_station_bundle(db, 1).to_json(), wire)
            self.assertEqual(load_station_bundle(db, 1, 1).version, 1)
        with closing(sqlite3.connect(center_path)) as db:
            initialize_center(db)
            initialize_center(db)
            entry = load_accepted(db, 7)
            self.assertEqual(entry.acceptance_id, 7)
            self.assertEqual(entry.bundle.version, 1)

    def test_plan_offset_conflict_and_single_point(self):
        plan = make_plan('HB-interior')
        with closing(sqlite3.connect(self.root / 'plan.sqlite')) as db:
            initialize_station(db)
            register(db, plan)
            conflicting = replace(plan, offset_interval=(Fraction(0), Fraction(0)))
            with self.assertRaisesRegex(ValueError, 'different fixed'):
                register(db, conflicting)
            with self.assertRaises(ValueError):
                register(db, replace(plan, offset_interval=(Fraction(1), Fraction(0))))
            self.assertEqual(
                register(db, plan), plan.plan_id,
            )
        single = PlanSpec(
            'HB-point', 'gauge', 'mm', ReferenceSpec(0), ('A', 'B'),
            (Fraction(2), Fraction(2)),
        )
        root = self.root / 'single-point'
        root.mkdir()
        with closing(sqlite3.connect(root / 'A.sqlite')) as source:
            initialize_station(source)
            register(source, single)
            record(source, single.plan_id, 'A', 'A-base', 0, 4, 1, sensitivity='3/2')
            revision = freeze_revision(source, single.plan_id, 'A')
            bundle = issue_robust(source, single.plan_id, 'A', revision.revision_id)
        evidence = decode_robust_evidence(bundle.evidence, bundle.domain)
        self.assertEqual(evidence.offset_interval, (Fraction(2), Fraction(2)))
        self.assertEqual(evidence.lower_loss.points, evidence.upper_loss.points)


if __name__ == '__main__':
    unittest.main()
