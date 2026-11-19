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
from issue.publication import issue
from plan.reference import ReferenceSpec
from plan.registration import PlanSpec, register
from report.publication import finalize
from report.retrieval import retrieve
from station import initialize_station
from station.intake import record
from station.revisions import freeze_revision


class Lab:
    """Independent on-disk stations and an independently opened center."""

    def __init__(self, root: Path, plan_id='HB'):
        self.root = root
        self.plan = PlanSpec(plan_id, 'gauge', 'mm', ReferenceSpec(0), ('A', 'B'))
        self.center_path = root / 'center.sqlite'
        self.paths = {station: root / f'{station}.sqlite' for station in self.plan.stations}
        self.revisions = {}
        self.bundles = {}
        self.accepted = {}

    def prepare(self, weights=(1, 1), values=(8, 2)):
        for station, position, value, weight in zip(self.plan.stations, (-1, 1), values, weights):
            with closing(sqlite3.connect(self.paths[station])) as source:
                initialize_station(source)
                register(source, self.plan)
                record(source, self.plan.plan_id, station, f'{station}-original', position, value, weight)
                revision = freeze_revision(source, self.plan.plan_id, station)
                _, bundle = issue(source, self.plan.plan_id, station, revision.revision_id)
            self.revisions[station] = revision
            self.bundles[station] = bundle
        with self.center() as center:
            initialize_center(center)
            register(center, self.plan)
            for station, bundle in self.bundles.items():
                self.accepted[station] = accept(center, bundle.to_json())
        return self

    def center(self):
        return closing(sqlite3.connect(self.center_path))

    def source(self, station='A'):
        return closing(sqlite3.connect(self.paths[station]))

    def correct_a(self, weight):
        old = self.revisions['A']
        reading = old.readings[0]
        with self.source() as source:
            initialize_station(source)
            record(source, self.plan.plan_id, 'A', 'A-corrected', reading.position, reading.value, weight,
                   base_revision_id=old.revision_id, replaces=reading.reading_id)
            revision = freeze_revision(source, self.plan.plan_id, 'A')
            _, bundle = issue(source, self.plan.plan_id, 'A', revision.revision_id)
        with self.center() as center:
            new_id = accept(center, bundle.to_json())
        return revision, bundle, new_id

    def detach_sources(self):
        for path in self.paths.values():
            path.replace(path.with_suffix('.offline'))

    def finalize(self, db, first=None, second=None):
        return finalize(db, self.plan.plan_id,
                        self.accepted['A'] if first is None else first,
                        self.accepted['B'] if second is None else second)


def counts(db):
    return tuple(db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                 for table in ('signed_reports', 'report_sources'))


def install_commit_failure(db):
    db.executescript('''
        CREATE TABLE fixture_gate (station TEXT PRIMARY KEY);
        CREATE TABLE fixture_deferred (
            station TEXT REFERENCES fixture_gate(station) DEFERRABLE INITIALLY DEFERRED
        );
        CREATE TRIGGER fixture_fail_commit AFTER INSERT ON report_sources
        WHEN NEW.slot = 1
        BEGIN
            INSERT INTO fixture_deferred (station) VALUES (NEW.station_id);
        END;
    ''')


class CenterTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory(prefix='hengqiao-center-'))
        self.root = Path(directory)

    def lab(self, name='default', *, plan_id='HB', weights=(1, 1), values=(8, 2)):
        path = self.root / name
        path.mkdir()
        return Lab(path, plan_id).prepare(weights, values)

    def assert_result(self, report, interval, error):
        self.assertEqual(report.reference_interval, interval)
        self.assertEqual(report.minimum_error, error)

    def test_bundle_plan_validation(self):
        lab = self.lab()
        good = lab.bundles['A']
        # This evidence covers the full declared [0,9], but the local plan says [0,10].
        wrong_domain = replace(good, domain=(Fraction(0), Fraction(9)), evidence_json='{"points":[["0","8"],["8","0"],["9","0"]]}')
        with lab.center() as center:
            prior = center.execute('SELECT COUNT(*) FROM accepted_bundles').fetchone()
            with self.assertRaisesRegex(ValueError, 'domain differs'):
                accept(center, wrong_domain.to_json())
            for bad in (
                replace(good, unit='cm'),
                replace(good, instrument='different gauge'),
                replace(good, reference_position=1),
                replace(good, station_id='outside'),
                replace(good, revision_id=0),
                replace(good, version=True),
                replace(good, evidence_json='{"points":[["0","0"],["5","5"],["10","0"]]}'),
                replace(good, evidence_json='{"points":[["0","8"],["9","0"]]}'),
                replace(good, evidence_json='{"points":[["0","0"],["10","-1"]]}'),
            ):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    accept(center, bad.to_json())
            with self.assertRaises(ValueError):
                accept(center, good.to_json().replace('"version":1', '"version":1,"version":1'))
            self.assertEqual(accept(center, good.to_json()), lab.accepted['A'])
            with self.assertRaisesRegex(ValueError, 'identity'):
                accept(center, replace(good, revision_id=good.revision_id + 10))
            self.assertEqual(center.execute('SELECT COUNT(*) FROM accepted_bundles').fetchone(), prior)
            self.assertEqual(load_accepted(center, lab.accepted['A']).bundle, good)

    def test_offline_common_plateau(self):
        lab = self.lab()
        lab.detach_sources()
        self.assertTrue(all(not path.exists() for path in lab.paths.values()))
        with lab.center() as center:
            result = lab.finalize(center)
            fixed = retrieve(center, result.report_id)
        self.assert_result(result, (2, 8), 6)
        self.assertEqual(fixed, result)
        actual = {'interval': [str(x) for x in result.reference_interval], 'error': str(result.minimum_error)}
        print('POINTWISE_SCENARIO ' + json.dumps({
            'case_id': 'v5',
            'input': {'A': [-1, '8', '1'], 'B': [1, '2', '1']},
            'expected': {'interval': ['2', '8'], 'error': '6'},
            'actual': actual,
            'downstream_expected': {'recovered': True},
            'downstream_actual': {'recovered': fixed == result},
            'code_paths': ['issue/publication.py', 'center/imports.py', 'report/joint_fit.py', 'report/retrieval.py'],
            'oracle': 'The hand-computed sum max(0,8-t) + max(0,t-2) is 6 throughout [2,8] and greater elsewhere.',
        }))

    def test_weight_variants(self):
        for weights, interval in (((2, 1), (8, 8)), ((1, 2), (2, 2)), ((1, 1), (2, 8))):
            with self.subTest(weights=weights):
                lab = self.lab(f'{weights[0]}-{weights[1]}', weights=weights)
                lab.detach_sources()
                with lab.center() as center:
                    self.assert_result(lab.finalize(center), interval, 6)

    def test_fractional_plateau(self):
        lab = self.lab(weights=('1/3', '1/3'), values=('15/2', '5/2'))
        lab.detach_sources()
        with lab.center() as center:
            report = lab.finalize(center)
        self.assert_result(report, (Fraction(5, 2), Fraction(15, 2)), Fraction(5, 3))

    def test_missing_or_mixed_selection(self):
        lab = self.lab()
        external = self.lab('external', plan_id='OTHER')
        lab.detach_sources()
        external.detach_sources()
        with lab.center() as center:
            good = lab.finalize(center)
            register(center, external.plan)
            wrong_plan = accept(center, external.bundles['B'].to_json())
            with self.assertRaises(LookupError):
                lab.finalize(center, second=9999)
            with self.assertRaisesRegex(ValueError, 'different plan'):
                lab.finalize(center, second=wrong_plan)
            self.assertEqual(counts(center), (1, 2))
            self.assertEqual(retrieve(center, good.report_id), good)

    def test_explicit_pair(self):
        lab = self.lab()
        revision, _, new_a = lab.correct_a(2)
        lab.detach_sources()
        with lab.center() as center:
            old = lab.finalize(center)
            new = finalize(center, lab.plan.plan_id, lab.accepted['B'], new_a)
            with self.assertRaisesRegex(ValueError, 'each'):
                lab.finalize(center, second=new_a)
            self.assertEqual(counts(center), (2, 4))
        self.assert_result(old, (2, 8), 6)
        self.assert_result(new, (8, 8), 6)
        self.assertEqual(tuple(source.acceptance_id for source in old.sources), (lab.accepted['A'], lab.accepted['B']))
        self.assertEqual(tuple(source.acceptance_id for source in new.sources), (new_a, lab.accepted['B']))
        self.assertEqual(new.sources[0].revision_id, revision.revision_id)

    def test_unknown_report(self):
        lab = self.lab()
        with lab.center() as center:
            with self.assertRaisesRegex(LookupError, 'unknown report'):
                retrieve(center, 9999)
            self.assertEqual(counts(center), (0, 0))

    def test_finalize_rollback(self):
        lab = self.lab()
        lab.detach_sources()
        with lab.center() as center:
            initialize_center(center)
            install_commit_failure(center)
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'FOREIGN KEY'):
                lab.finalize(center)
            self.assertFalse(center.in_transaction)
            self.assertEqual(counts(center), (0, 0))
            self.assertEqual(center.execute('SELECT COUNT(*) FROM fixture_deferred').fetchone(), (0,))
            center.execute('DROP TRIGGER fixture_fail_commit')
            report = lab.finalize(center)
            self.assertEqual(counts(center), (1, 2))
            self.assertEqual(retrieve(center, report.report_id), report)
            self.assert_result(report, (2, 8), 6)

    def test_no_partial_report(self):
        lab = self.lab()
        _, _, new_a = lab.correct_a(2)
        lab.detach_sources()
        with lab.center() as center:
            initialize_center(center)
            first = lab.finalize(center)
            install_commit_failure(center)
            with self.assertRaises(sqlite3.IntegrityError):
                lab.finalize(center, first=new_a)
        with lab.center() as center:
            initialize_center(center)
            self.assertEqual(counts(center), (1, 2))
            self.assertEqual(retrieve(center, first.report_id), first)
            self.assertEqual(load_accepted(center, new_a).bundle.station_id, 'A')
            center.execute('DROP TRIGGER fixture_fail_commit')
            second = lab.finalize(center, first=new_a)
            self.assertEqual(counts(center), (2, 4))
            self.assertEqual(retrieve(center, second.report_id), second)
        self.assert_result(second, (8, 8), 6)
        self.assertEqual(tuple(source.acceptance_id for source in second.sources), (new_a, lab.accepted['B']))
