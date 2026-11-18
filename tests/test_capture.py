import sqlite3

from device.journal import list_shift_triggers, read_cursor
from device.receive import receive
from inspection.judgment import Verdict
from inspection.receipts import receipt
from sampling.planner import book
from shift.calibration import Calibration
from shift.register import register
from tests.support import WorkspaceTest, deny_commit


class CaptureTests(WorkspaceTest):
    def test_empty_beat_then_endpoint_measurement(self):
        self.register_day({1: ['P'], 2: ['P']})
        book(self.db, 'day', 'edge', 2, 'P', 100)
        self.assertIsNone(self.device.advance(self.db, 'day', 1))
        self.assertEqual(self.device.commands, {})
        command = self.device.advance(self.db, 'day', 2)
        result = self.device.release(self.db, command.trigger_id, 101)
        self.assertEqual((result.trigger_id, result.ticket_id, result.probe, result.beat),
                         (command.trigger_id, 'edge', 'P', 2))
        measured = result.measurement
        self.assertEqual((measured.raw_um, measured.correction_um, measured.target_um,
                          measured.corrected_um, measured.deviation_um, result.tolerance_um),
                         (101, 2, 100, 103, 3, 3))
        self.assertEqual(result.verdict, Verdict.PASS)
        self.assertEqual(receipt(self.db, result.receipt_id), result)

    def test_tick_order_with_gaps(self):
        self.register_day({9: ['P'], 2: ['P']})
        book(self.db, 'day', 'ninth', 9, 'P', 100)
        with self.assertRaisesRegex(ValueError, 'out of order'):
            self.device.advance(self.db, 'day', 9)
        with self.assertRaises(LookupError):
            self.device.advance(self.db, 'missing', 2)
        self.assertEqual(read_cursor(self.db, 'day'), 0)
        self.assertIsNone(self.device.advance(self.db, 'day', 2))
        with self.assertRaisesRegex(ValueError, 'out of order'):
            self.device.advance(self.db, 'day', 2)
        self.assertEqual(self.device.commands, {})
        command = self.device.advance(self.db, 'day', 9)
        with self.assertRaisesRegex(ValueError, 'no more'):
            self.device.advance(self.db, 'day', 9)
        self.assertEqual(list(self.device.commands), [command.trigger_id])
        self.assertEqual((command.ticket_id, command.beat), ('ninth', 9))

    def test_tick_commit_failure_leaves_original_booking_and_cursor(self):
        self.register_day({2: ['P']})
        book(self.db, 'day', 'original', 2, 'P', 100)
        with deny_commit(self.db), self.assertRaisesRegex(sqlite3.DatabaseError, 'not authorized'):
            self.device.advance(self.db, 'day', 2)
        self.assertEqual(read_cursor(self.db, 'day'), 0)
        self.assertEqual(list_shift_triggers(self.db, 'day'), ())
        self.assertEqual(self.device.commands, {})
        command = self.device.advance(self.db, 'day', 2)
        self.assertEqual((command.ticket_id, command.beat), ('original', 2))
        self.assertEqual(len(list_shift_triggers(self.db, 'day')), 1)

    def test_integer_boundaries_and_exact_round_trip(self):
        wide = 1 << 63
        large = 1 << 128
        cases = (
            ('P', 100, 101, 103, 3, Verdict.PASS),
            ('P', 100, 95, 97, -3, Verdict.PASS),
            ('P', 100, 102, 104, 4, Verdict.OUTSIDE),
            ('P', 100, 94, 96, -4, Verdict.OUTSIDE),
            ('Z', 100, 100, 100, 0, Verdict.PASS),
            ('Z', 100, 101, 101, 1, Verdict.OUTSIDE),
            ('H', -wide, wide + 1, 2 * wide + 1, 3 * wide + 1, Verdict.PASS),
            ('L', 0, -large + 7, 7, 7, Verdict.PASS),
        )
        calibrations = {'P': Calibration(2, 3), 'Z': Calibration(0, 0),
                        'H': Calibration(wide, 3 * wide + 1), 'L': Calibration(large, 7)}
        slots = {beat: [case[0]] for beat, case in enumerate(cases, 1)}
        register(self.db, 'day', slots, calibrations)
        saved = []
        for beat, (probe, target, raw, corrected, deviation, verdict) in enumerate(cases, 1):
            with self.subTest(beat=beat, probe=probe):
                book(self.db, 'day', f't{beat}', beat, probe, target)
                command = self.device.advance(self.db, 'day', beat)
                result = self.device.release(self.db, command.trigger_id, raw)
                values = result.measurement
                self.assertEqual((values.raw_um, values.target_um, values.corrected_um,
                                  values.deviation_um, result.verdict),
                                 (raw, target, corrected, deviation, verdict))
                saved.append(result)
        self.reopen()
        for result in saved:
            self.assertEqual(receipt(self.db, result.receipt_id), result)

    def test_reverse_reply_order_and_duplicate(self):
        first, second = self._two_in_flight()
        later = self.device.release(self.db, second.trigger_id, 96)
        earlier = self.device.release(self.db, first.trigger_id, 101)
        self.assertEqual((later.ticket_id, later.trigger_id, later.beat, later.probe,
                          later.measurement.raw_um, later.measurement.corrected_um, later.verdict),
                         ('second', second.trigger_id, 9, 'Q', 96, 95, Verdict.OUTSIDE))
        self.assertEqual((earlier.ticket_id, earlier.trigger_id, earlier.beat, earlier.probe,
                          earlier.measurement.raw_um, earlier.measurement.corrected_um),
                         ('first', first.trigger_id, 2, 'P', 101, 103))
        self.assertNotEqual(later.receipt_id, earlier.receipt_id)
        self.assertEqual(self.device.release(self.db, second.trigger_id, 96), later)
        self.assertEqual(self.db.execute('SELECT count(*) FROM receipts').fetchone(), (2,))

    def test_reopen_keeps_original_receipt_for_replay(self):
        first, second = self._two_in_flight()
        signed = self.device.release(self.db, second.trigger_id, 96)
        self.reopen()
        replay = self.device.release(self.db, second.trigger_id, 96)
        self.assertEqual(replay, signed)
        self.assertEqual((replay.trigger_id, replay.shift_id, replay.ticket_id, replay.beat,
                          replay.probe, replay.measurement.raw_um),
                         (second.trigger_id, 'day', 'second', 9, 'Q', 96))
        self.assertEqual(receipt(self.db, signed.receipt_id), signed)
        other = self.device.release(self.db, first.trigger_id, 101)
        self.assertEqual((other.ticket_id, other.beat), ('first', 2))

    def test_bad_replies_and_unknown_receipt_leave_pending_work_usable(self):
        first, second = self._two_in_flight()
        original = self.device.release(self.db, second.trigger_id, 96)
        with self.assertRaisesRegex(ValueError, 'conflicts'):
            receive(self.db, second.trigger_id, 97)
        with self.assertRaises(LookupError):
            receive(self.db, 999, 97)
        with self.assertRaises(ValueError):
            receive(self.db, first.trigger_id, True)
        with self.assertRaises(LookupError):
            receipt(self.db, 999)
        self.assertEqual(receipt(self.db, original.receipt_id), original)
        remaining = self.device.release(self.db, first.trigger_id, 101)
        self.assertEqual((remaining.ticket_id, remaining.beat, remaining.measurement.raw_um),
                         ('first', 2, 101))
        self.assertEqual(self.db.execute('SELECT count(*) FROM receipts').fetchone(), (2,))

    def _two_in_flight(self):
        self.register_day({2: ['P'], 9: ['Q']})
        book(self.db, 'day', 'first', 2, 'P', 100)
        book(self.db, 'day', 'second', 9, 'Q', 100)
        return self.device.advance(self.db, 'day', 2), self.device.advance(self.db, 'day', 9)
