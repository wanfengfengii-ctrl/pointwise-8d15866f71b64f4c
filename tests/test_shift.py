from device.journal import read_cursor
from inspection.judgment import Verdict
from sampling.bookings import load_ticket
from sampling.planner import book
from shift.bootstrap import initialize_workspace
from shift.calendar import load_calendar
from shift.calibration import Calibration
from shift.register import register
from tests.support import WorkspaceTest


class ShiftTests(WorkspaceTest):
    def test_equal_registration_is_order_independent(self):
        self.assertEqual(self.register_day({9: ['Q', 'P', 'Q'], 2: []}), 'day')
        self.assertEqual(
            register(self.db, 'day', {2: (), 9: {'P', 'Q'}},
                     {'Q': Calibration(-1, 2), 'P': Calibration(2, 3)}),
            'day',
        )
        self.assertEqual(load_calendar(self.db, 'day').slots, ((2, ()), (9, ('P', 'Q'))))
        self.assertEqual(book(self.db, 'day', 'q9', 9, 'Q', 100), 'q9')

    def test_reopen_keeps_cursor_booking_and_calibration(self):
        self.register_day({2: ['Q'], 9: ['P']})
        book(self.db, 'day', 'saved', 9, 'P', 100)
        self.assertIsNone(self.device.advance(self.db, 'day', 2))
        self.reopen()
        initialize_workspace(self.db)
        self.assertEqual(self.register_day({9: ['P'], 2: ['Q']}), 'day')
        self.assertEqual(read_cursor(self.db, 'day'), 2)
        saved = load_ticket(self.db, 'saved')
        self.assertEqual((saved.beat, saved.probe, saved.target_um), (9, 'P', 100))
        self.assertEqual(load_calendar(self.db, 'day').calibration_for('P'), Calibration(2, 3))
        with self.assertRaisesRegex(ValueError, 'already passed'):
            book(self.db, 'day', 'late', 2, 'Q', 100)
        command = self.device.advance(self.db, 'day', 9)
        self.assertEqual((command.ticket_id, command.beat), ('saved', 9))
        result = self.device.release(self.db, command.trigger_id, 101)
        self.assertEqual((result.measurement.corrected_um, result.verdict), (103, Verdict.PASS))

    def test_different_configuration_preserves_original(self):
        self.register_day({3: ['P']})
        with self.assertRaisesRegex(ValueError, 'different calendar'):
            register(self.db, 'day', {3: ['P']}, {'P': Calibration(7, 3), 'Q': Calibration(-1, 2)})
        with self.assertRaisesRegex(ValueError, 'different calendar'):
            self.register_day({3: ['Q']})
        self.assertEqual(load_calendar(self.db, 'day').calibration_for('P'), Calibration(2, 3))
        book(self.db, 'day', 'original', 3, 'P', 100)
        command = self.device.advance(self.db, 'day', 3)
        result = self.device.release(self.db, command.trigger_id, 101)
        self.assertEqual((result.measurement.corrected_um, result.measurement.deviation_um), (103, 3))
        self.assertEqual(result.verdict, Verdict.PASS)

    def test_invalid_configuration_does_not_publish(self):
        for slots, calibrations in (
            ({0: ['P'], 9: ['P']}, {'P': Calibration(0, 3)}),
            ({True: ['P']}, {'P': Calibration(0, 3)}),
            ({2: ['P'], 9: ['Q']}, {'P': Calibration(0, 3)}),
            ({2: ['P']}, {'P': Calibration(0, -1)}),
            ({2: ['P']}, {'P': Calibration(False, 0)}),
        ):
            with self.subTest(slots=slots, calibrations=calibrations):
                with self.assertRaises(ValueError):
                    register(self.db, 'day', slots, calibrations)
                with self.assertRaises(LookupError):
                    load_calendar(self.db, 'day')
        self.assertEqual(self.register_day({2: ['P']}), 'day')
        self.assertEqual(book(self.db, 'day', 'after-invalid', 2, 'P', 100), 'after-invalid')
