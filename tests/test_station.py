import json
import sqlite3
import unittest
from contextlib import closing
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

from issue.artifacts import StationBundle, load_station_bundle
from issue.evidence import decode_evidence
from issue.local_fit import minimum_at_reference
from issue.publication import issue
from plan.reference import ReferenceSpec
from plan.registration import PlanSpec, load_plan, register
from station import initialize_station
from station.intake import record
from station.ledger import load_draft
from station.revisions import freeze_revision, load_revision


class StationTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory(prefix='hengqiao-station-'))
        self.path = Path(directory) / 'station.sqlite'
        self.db = self.enterContext(closing(sqlite3.connect(self.path)))
        self.plan = PlanSpec('HB', 'gauge', 'mm', ReferenceSpec(0), ('A', 'B'))
        initialize_station(self.db)
        register(self.db, self.plan)

    def read(self, name, position, value, weight=1):
        return record(self.db, 'HB', 'A', name, position, value, weight)

    def issue_draft(self):
        revision = freeze_revision(self.db, 'HB', 'A')
        return revision, *issue(self.db, 'HB', 'A', revision.revision_id)

    def test_repeated_position(self):
        self.read('repeat-low', -1, 2)
        self.read('repeat-high', -1, 8)
        revision, check, bundle = self.issue_draft()
        error = decode_evidence(bundle.evidence, bundle.domain).evaluate(10)
        members = sorted(reading.reading_id for reading in check.readings)
        self.assertEqual(error, 6)
        self.assertEqual(check.minimum_error, 6)
        self.assertEqual(members, ['repeat-high', 'repeat-low'])
        self.assertEqual(check.readings, load_revision(self.db, revision.revision_id).readings)
        print('POINTWISE_SCENARIO ' + json.dumps({
            'case_id': 'v1',
            'input': {'position': -1, 'reference': '10', 'readings': [['2', '1'], ['8', '1']]},
            'expected': {'error': '6'},
            'actual': {'error': str(error)},
            'downstream_expected': {'members': ['repeat-high', 'repeat-low']},
            'downstream_actual': {'members': members},
            'code_paths': ['station/positions.py', 'issue/local_fit.py', 'issue/publication.py'],
            'oracle': 'The triangle inequality gives a lower bound of 6, attained by one common fit anywhere from 2 to 8.',
        }))

    def test_invalid_weight(self):
        valid = self.read('valid', -1, 4)
        with self.assertRaisesRegex(ValueError, 'positive'):
            self.read('invalid', -1, 9, 0)
        with closing(sqlite3.connect(self.path)) as reopened:
            initialize_station(reopened)
            self.assertEqual(load_draft(reopened, 'HB', 'A'), (valid,))
            revision = freeze_revision(reopened, 'HB', 'A')
            check, bundle = issue(reopened, 'HB', 'A', revision.revision_id)
        self.assertEqual(check.readings, (valid,))
        self.assertEqual(decode_evidence(bundle.evidence, bundle.domain).evaluate(0), 4)

    def test_plan_registration(self):
        self.assertEqual(register(self.db, self.plan), 'HB')
        for invalid in (
            replace(self.plan, unit='cm'),
            replace(self.plan, reference=ReferenceSpec('0')),
            replace(self.plan, reference=ReferenceSpec(True)),
            replace(self.plan, reference=ReferenceSpec(0, 0, 9)),
            replace(self.plan, stations=('A', 'A')),
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                register(self.db, invalid)
        self.assertEqual(load_plan(self.db, 'HB'), self.plan)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM calibration_plans').fetchone(), (1,))

    def test_intake_boundaries(self):
        valid = self.read('valid', -5, '5/3', '2/7')
        for value, weight in [(-1, 1), (11, 1), (None, 1), (4, None), (4, -1), (4, '1/0'), (1.5, 1), (True, 1)]:
            with self.subTest(value=value, weight=weight), self.assertRaises(ValueError):
                self.read('invalid', -5, value, weight)
        with self.assertRaises(ValueError):
            record(self.db, 'HB', 'outside', 'unexpected', -5, 4, 1)
        with self.assertRaises(ValueError):
            self.read('valid', -2, 3)
        with self.assertRaises(ValueError):
            self.read('invalid-position', '-1', 4)
        self.assertEqual(load_draft(self.db, 'HB', 'A'), (valid,))
        self.assertEqual((valid.value, valid.weight), (Fraction(5, 3), Fraction(2, 7)))
        _, check, bundle = self.issue_draft()
        self.assertEqual(check.readings, (valid,))
        self.assertEqual(decode_evidence(bundle.evidence, bundle.domain).evaluate(0), Fraction(10, 21))

    def test_ordered_fit(self):
        self.read('listed-first', -1, 2)
        self.read('listed-second', -2, 8)
        revision, check, bundle = self.issue_draft()
        error = minimum_at_reference(revision, self.plan.reference, 10)
        self.assertEqual(error, 6)
        self.assertEqual(decode_evidence(bundle.evidence, bundle.domain).evaluate(10), 6)
        self.assertEqual([reading.value for reading in check.readings], [8, 2])

    def test_order_reverse(self):
        self.read('upper-position-arrived-first', -1, 8)
        self.read('lower-position-arrived-second', -2, 2)
        revision, check, bundle = self.issue_draft()
        self.assertEqual(minimum_at_reference(revision, self.plan.reference, 10), 0)
        self.assertEqual(decode_evidence(bundle.evidence, bundle.domain).evaluate(10), 0)
        self.assertEqual(check.reference_interval, (8, 10))

    def test_anchor_fraction(self):
        self.read('anchor', 0, '3/2', '1/2')
        revision, check, bundle = self.issue_draft()
        self.assertEqual(minimum_at_reference(revision, self.plan.reference, '5/2'), Fraction(1, 2))
        self.assertEqual(decode_evidence(bundle.evidence, bundle.domain).evaluate('5/2'), Fraction(1, 2))
        self.assertEqual(check.reference_interval, (Fraction(3, 2), Fraction(3, 2)))

    def test_conditioned_evidence(self):
        self.read('private-initial-reading', -231, 8)
        revision, old_check, old = self.issue_draft()
        record(self.db, 'HB', 'A', 'private-corrected-reading', -231, 8, 2,
               base_revision_id=revision.revision_id, replaces='private-initial-reading')
        _, new_check, new = self.issue_draft()
        for check, bundle, interior, left in ((old_check, old, Fraction(9, 2), 8), (new_check, new, 9, 16)):
            self.assertEqual(check.reference_interval, (8, 10))
            self.assertEqual(check.minimum_error, 0)
            wire = bundle.to_json()
            recovered = StationBundle.from_json(wire)
            evidence = decode_evidence(recovered.evidence, recovered.domain)
            self.assertEqual(evidence.evaluate('7/2'), interior)
            self.assertEqual(evidence.evaluate(0), left)
            self.assertEqual(evidence.evaluate(10), 0)
            self.assertNotIn('private-initial-reading', wire)
            self.assertNotIn('private-corrected-reading', wire)
            self.assertNotIn('-231', wire)
            self.assertNotIn('readings', json.loads(wire))
            self.assertEqual(load_station_bundle(self.db, bundle.bundle_id), recovered)

    def test_invalid_revision(self):
        with self.assertRaisesRegex(ValueError, 'no effective'):
            freeze_revision(self.db, 'HB', 'A')
        self.read('available', -1, 4)
        revision = freeze_revision(self.db, 'HB', 'A')
        register(self.db, replace(self.plan, plan_id='OTHER'))
        with self.assertRaises(LookupError):
            issue(self.db, 'HB', 'A', 9999)
        with self.assertRaises(ValueError):
            issue(self.db, 'HB', 'B', revision.revision_id)
        with self.assertRaises(ValueError):
            issue(self.db, 'OTHER', 'A', revision.revision_id)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM station_bundles').fetchone(), (0,))
        check, bundle = issue(self.db, 'HB', 'A', revision.revision_id)
        self.assertEqual(check.revision_id, revision.revision_id)
        self.assertEqual(load_station_bundle(self.db, bundle.bundle_id), bundle)
