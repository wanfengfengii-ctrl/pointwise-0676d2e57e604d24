import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from center import initialize_center
from center.catalog import load_accepted
from center.imports import accept
from issue.artifacts import load_station_bundle
from issue.evidence import decode_evidence
from issue.publication import issue
from plan.registration import load_plan, register
from report.comparison import compare
from report.publication import finalize
from report.retrieval import retrieve
from station import initialize_station
from station.intake import record
from station.ledger import load_draft, load_reading
from station.revisions import freeze_revision, load_revision
from tests.test_center import Lab


class RevisionTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory(prefix='hengqiao-revisions-'))
        self.root = Path(directory)

    def lab(self, name='default', *, plan_id='HB', weights=(1, 1)):
        path = self.root / name
        path.mkdir()
        return Lab(path, plan_id).prepare(weights)

    def test_correction_and_reopen(self):
        lab = self.lab()
        with lab.center() as center:
            original = lab.finalize(center)
        _, _, new_a = lab.correct_a(2)
        lab.detach_sources()
        with lab.center() as center:
            corrected = lab.finalize(center, first=new_a)
        with lab.center() as reopened:
            old = retrieve(reopened, original.report_id)
            new = retrieve(reopened, corrected.report_id)
            result = compare(reopened, old.report_id, new.report_id)
        self.assertEqual((old, new), (original, corrected))
        self.assertEqual((old.reference_interval, old.minimum_error), ((2, 8), 6))
        self.assertEqual((new.reference_interval, new.minimum_error), ((8, 8), 6))
        self.assertEqual((result.old, result.new), (old, new))
        self.assertEqual([change.station_id for change in result.changed_sources], ['A'])
        change = result.changed_sources[0]
        self.assertEqual((change.old.acceptance_id, change.new.acceptance_id), (lab.accepted['A'], new_a))
        self.assertEqual(old.sources[1], new.sources[1])

    def test_frozen_members(self):
        lab = self.lab()
        with lab.source() as source:
            record(source, 'HB', 'A', 'A-low', -1, 2, 1)
            old = freeze_revision(source, 'HB', 'A')
            _, old_bundle = issue(source, 'HB', 'A', old.revision_id)
            record(source, 'HB', 'A', 'unfrozen-extra', -4, 4, 1)
            prior = load_draft(source, 'HB', 'A')
            with self.assertRaisesRegex(ValueError, 'effective member'):
                record(source, 'HB', 'A', 'bad-correction', -1, 8, 2,
                       base_revision_id=old.revision_id, replaces='unfrozen-extra')
            self.assertEqual(load_draft(source, 'HB', 'A'), prior)
            with self.assertRaises(LookupError):
                load_reading(source, 'HB', 'A', 'bad-correction')
            record(source, 'HB', 'A', 'A-corrected', -1, 8, 2,
                   base_revision_id=old.revision_id, replaces='A-original')
            new = freeze_revision(source, 'HB', 'A')
            _, new_bundle = issue(source, 'HB', 'A', new.revision_id)
        with lab.source() as source:
            restored_old = load_revision(source, old.revision_id)
            restored_new = load_revision(source, new.revision_id)
            check, reissued = issue(source, 'HB', 'A', old.revision_id)
            self.assertEqual(load_station_bundle(source, old_bundle.bundle_id), old_bundle)
        self.assertEqual(restored_old, old)
        self.assertEqual(restored_new, new)
        self.assertEqual(new.base_revision_id, old.revision_id)
        self.assertEqual({r.reading_id: (r.value, r.weight, r.replaces) for r in old.readings},
                         {'A-low': (2, 1, None), 'A-original': (8, 1, None)})
        self.assertEqual({r.reading_id: (r.value, r.weight, r.replaces) for r in new.readings},
                         {'A-low': (2, 1, None), 'A-corrected': (8, 2, 'A-original')})
        self.assertEqual(check.readings, old.readings)
        self.assertEqual(reissued, old_bundle)
        self.assertEqual(decode_evidence(reissued.evidence, reissued.domain).evaluate(2), 6)
        self.assertEqual(decode_evidence(new_bundle.evidence, new_bundle.domain).evaluate(2), 12)

    def test_accepted_versions(self):
        lab = self.lab()
        revision, bundle, new_id = lab.correct_a(2)
        lab.detach_sources()
        with lab.center() as reopened:
            old = load_accepted(reopened, lab.accepted['A'])
            new = load_accepted(reopened, new_id)
            self.assertEqual(accept(reopened, bundle.to_json()), new_id)
        self.assertNotEqual(old.acceptance_id, new.acceptance_id)
        self.assertEqual(old.bundle, lab.bundles['A'])
        self.assertEqual(old.bundle.revision_id, lab.revisions['A'].revision_id)
        self.assertEqual(new.bundle, bundle)
        self.assertEqual(new.bundle.revision_id, revision.revision_id)

    def test_equal_outcome(self):
        lab = self.lab(weights=(2, 1))
        with lab.center() as center:
            original = lab.finalize(center)
        revision, bundle, new_id = lab.correct_a(3)
        lab.detach_sources()
        with lab.center() as center:
            corrected = lab.finalize(center, first=new_id)
        with lab.center() as center:
            result = compare(center, original.report_id, corrected.report_id)
        self.assertEqual((result.old.reference_interval, result.old.minimum_error), ((8, 8), 6))
        self.assertEqual((result.new.reference_interval, result.new.minimum_error), ((8, 8), 6))
        self.assertEqual([change.station_id for change in result.changed_sources], ['A'])
        change = result.changed_sources[0]
        self.assertEqual(change.old.revision_id, lab.revisions['A'].revision_id)
        self.assertEqual((change.new.acceptance_id, change.new.bundle_id, change.new.revision_id),
                         (new_id, bundle.bundle_id, revision.revision_id))

    def test_comparison_errors(self):
        lab = self.lab()
        other = self.lab('other', plan_id='OTHER')
        lab.detach_sources()
        other.detach_sources()
        with lab.center() as center:
            first = lab.finalize(center)
            register(center, other.plan)
            second = finalize(center, 'OTHER', *(accept(center, other.bundles[station]) for station in other.plan.stations))
            with self.assertRaises(LookupError):
                compare(center, first.report_id, 9999)
            with self.assertRaisesRegex(ValueError, 'same fixed plan'):
                compare(center, first.report_id, second.report_id)
            self.assertEqual(retrieve(center, first.report_id), first)
            self.assertEqual(retrieve(center, second.report_id), second)

    def test_reinitialize(self):
        lab = self.lab()
        with lab.center() as center:
            old_report = lab.finalize(center)
        new_revision, new_bundle, new_id = lab.correct_a(2)
        with lab.center() as center:
            new_report = lab.finalize(center, first=new_id)
        for station in lab.plan.stations:
            with closing(sqlite3.connect(lab.paths[station])) as source:
                initialize_station(source)
                initialize_station(source)
                original = lab.revisions[station]
                self.assertEqual(load_plan(source, 'HB'), lab.plan)
                self.assertEqual(load_revision(source, original.revision_id), original)
                self.assertEqual(load_station_bundle(source, lab.bundles[station].bundle_id), lab.bundles[station])
                if station == 'A':
                    self.assertEqual(load_revision(source, new_revision.revision_id), new_revision)
                    self.assertEqual(load_draft(source, 'HB', station), new_revision.readings)
                    self.assertEqual(load_station_bundle(source, new_bundle.bundle_id), new_bundle)
        lab.detach_sources()
        with lab.center() as reopened:
            initialize_center(reopened)
            initialize_center(reopened)
            self.assertEqual(load_plan(reopened, 'HB'), lab.plan)
            self.assertEqual(load_accepted(reopened, new_id).bundle, new_bundle)
            for station in lab.plan.stations:
                self.assertEqual(load_accepted(reopened, lab.accepted[station]).bundle, lab.bundles[station])
            self.assertEqual(retrieve(reopened, old_report.report_id), old_report)
            self.assertEqual(retrieve(reopened, new_report.report_id), new_report)
