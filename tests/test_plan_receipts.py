"""Offline regressions for short plan evidence references and snapshot identity."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.context import CommandRecord, ContextManager
from src.executor import ExecutionResult
from src.parser import Command
from src.task_plan import validate_plan


def plan(*evidence):
    return {'explanation': 'Verified local work', 'steps': [{
        'id': 'implementation', 'title': 'Implementation', 'acceptance': 'Local checks pass',
        'status': 'completed', 'evidence': list(evidence),
    }]}


def record(context, identifier, *, status='success', tool='bash', path='check.py'):
    context.record(Command(id=identifier, tool=tool, path=path),
        ExecutionResult(success=status == 'success', tool=tool, status=status))
    return context.records[-1]


def receipt(identifier, reference, *, status='success'):
    result = CommandRecord(id=identifier, tool='bash', path='checks/verify.py', status=status)
    # Assignment also lets the pre-change validator exercise collision cases.
    result.evidence_ref = reference
    return result


class PlanReceiptTests(unittest.TestCase):
    def test_short_success_normalizes_without_mutating_input(self):
        records = [receipt('provider_call_long_id', 'receipt:1')]
        candidate = plan('receipt:1')
        original = copy.deepcopy(candidate)
        validated = validate_plan(candidate, records=records)
        self.assertEqual(validated['steps'][0]['evidence'], ['provider_call_long_id'])
        self.assertEqual(candidate, original)
        self.assertEqual(validate_plan(plan('provider_call_long_id'), records=records), validated)
        self.assertEqual(validate_plan(candidate, records=records, previous=validated), validated)

    def test_failed_alias_cannot_borrow_success_of_same_command_id(self):
        records = [receipt('same', 'receipt:1'), receipt('same', 'receipt:2', status='failed')]
        with self.assertRaisesRegex(ValueError, 'receipt:2'):
            validate_plan(plan('receipt:2'), records=records)
        self.assertEqual(validate_plan(plan('receipt:1'), records=records)['steps'][0]['evidence'], ['same'])
        for status in ('skipped', 'unknown', 'cancelled', 'pending'):
            with self.subTest(status=status):
                with self.assertRaises(ValueError):
                    validate_plan(plan('receipt:3'), records=[receipt('other', 'receipt:3', status=status)])

    def test_alias_and_real_id_collision_is_rejected(self):
        records = [receipt('provider_a', 'receipt:1'), receipt('receipt:1', 'receipt:2')]
        with self.assertRaisesRegex(ValueError, 'receipt:1'):
            validate_plan(plan('receipt:1'), records=records)
        with self.assertRaises(ValueError):
            validate_plan(plan('receipt:1'), records=[
                receipt('provider_a', 'receipt:1'), receipt('provider_b', 'receipt:1')])

    def test_record_references_survive_compaction_and_snapshot(self):
        context = ContextManager()
        first = record(context, 'provider_a')
        second = record(context, 'provider_b')
        self.assertEqual(first.evidence_ref, 'receipt:1')
        self.assertEqual(second.evidence_ref, 'receipt:2')
        self.assertIn('evidence_ref="receipt:1"', first.to_injection_string())
        first.injected = second.injected = 1
        context.compact_history(keep_records=1)
        self.assertEqual([r.id for r in context.records], ['provider_b'])
        restored = ContextManager.from_dict(context.to_dict())
        self.assertEqual(restored.evidence_ref_for('provider_b'), 'receipt:2')
        self.assertEqual(record(restored, 'provider_c').evidence_ref, 'receipt:3')
        for item in restored.records:
            item.injected = 1
        restored.compact_history(keep_records=0)
        empty_restored = ContextManager.from_dict(restored.to_dict())
        self.assertEqual(record(empty_restored, 'provider_d').evidence_ref, 'receipt:4')

    def test_replayed_receipt_lookup_keeps_original_non_skipped_reference(self):
        context = ContextManager()
        record(context, 'same', status='skipped')
        original = record(context, 'same')
        record(context, 'same', status='failed')
        self.assertEqual(context.evidence_ref_for('same'), original.evidence_ref)
        self.assertEqual(context.evidence_ref_for('same'), original.evidence_ref)
        self.assertEqual(len(context.records), 3)
        self.assertIsNone(context.evidence_ref_for('absent'))
        only_skipped = ContextManager()
        record(only_skipped, 'skipped', status='skipped')
        self.assertIsNone(only_skipped.evidence_ref_for('skipped'))

    def test_legacy_snapshot_migration_is_stable_after_resaving(self):
        context = ContextManager()
        record(context, 'legacy_a')
        record(context, 'legacy_b')
        legacy = context.to_dict()
        legacy.pop('next_receipt_ref', None)
        for entry in legacy['records']:
            entry.pop('evidence_ref', None)
        restored = ContextManager.from_dict(legacy)
        references = [r.evidence_ref for r in restored.records]
        self.assertEqual(len(set(references)), 2)
        self.assertTrue(all(ref.startswith('receipt:') for ref in references))
        again = ContextManager.from_dict(restored.to_dict())
        self.assertEqual([r.evidence_ref for r in again.records], references)
        self.assertNotIn(record(again, 'new').evidence_ref, references)

    def test_duplicate_persisted_alias_is_rejected(self):
        context = ContextManager()
        record(context, 'a')
        record(context, 'b')
        snapshot = context.to_dict()
        for entry in snapshot['records']:
            entry['evidence_ref'] = 'receipt:7'
        with self.assertRaises(ValueError):
            ContextManager.from_dict(snapshot)

    def test_mixed_snapshot_keeps_refs_and_repairs_low_counter(self):
        context = ContextManager()
        record(context, 'existing')
        record(context, 'legacy')
        snapshot = context.to_dict()
        snapshot['records'][0]['evidence_ref'] = 'receipt:8'
        snapshot['records'][1].pop('evidence_ref')
        snapshot['next_receipt_ref'] = 2
        restored = ContextManager.from_dict(snapshot)
        self.assertEqual(restored.evidence_ref_for('existing'), 'receipt:8')
        self.assertEqual(restored.evidence_ref_for('legacy'), 'receipt:9')
        self.assertEqual(record(restored, 'next').evidence_ref, 'receipt:10')

    def test_invalid_snapshot_reference_and_counter_are_rejected(self):
        context = ContextManager()
        record(context, 'existing')
        for reference in (None, 0, 'receipt:0', 'receipt:01', 'unqualified'):
            with self.subTest(reference=reference):
                snapshot = context.to_dict()
                snapshot['records'][0]['evidence_ref'] = reference
                with self.assertRaises(ValueError):
                    ContextManager.from_dict(snapshot)
        for counter in (0, -1, True, '2'):
            with self.subTest(counter=counter):
                snapshot = context.to_dict()
                snapshot['next_receipt_ref'] = counter
                with self.assertRaises(ValueError):
                    ContextManager.from_dict(snapshot)

    def test_unchanged_compacted_completion_still_accepts_canonical_id(self):
        previous = plan('archived_provider_id')
        self.assertEqual(validate_plan(copy.deepcopy(previous), records=[], previous=previous), previous)
        altered = copy.deepcopy(previous)
        altered['steps'][0]['acceptance'] = 'A new condition'
        with self.assertRaises(ValueError):
            validate_plan(altered, records=[], previous=previous)
        with self.assertRaises(ValueError):
            validate_plan(plan('receipt:999'), records=[], previous=previous)

    def test_feedback_lists_invalid_and_at_most_six_compact_choices(self):
        records = [receipt('very_long_provider_identifier_' + str(i), f'receipt:{i}') for i in range(1, 10)]
        with self.assertRaises(ValueError) as raised:
            validate_plan(plan('misspelled'), records=records)
        message = str(raised.exception)
        self.assertIn('misspelled', message)
        self.assertIn('receipt:9', message)
        self.assertNotIn('very_long_provider_identifier_', message)
        self.assertLessEqual(message.count('receipt:'), 6)
        self.assertIn('bash', message)


if __name__ == '__main__':
    unittest.main()
