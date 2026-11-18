from sampling.planner import book
from tests.support import WorkspaceTest


class BookingTests(WorkspaceTest):
    def test_two_tickets_competing_at_one_beat(self):
        self.register_day({2: ['P']})
        self.assertEqual(book(self.db, 'day', 'one', 2, 'P', 100), 'one')
        with self.assertRaisesRegex(ValueError, 'already reserved'):
            book(self.db, 'day', 'two', 2, 'P', 120)
        command = self.device.advance(self.db, 'day', 2)
        self.assertEqual(list(self.device.commands.values()), [{
            'trigger_id': command.trigger_id, 'shift_id': 'day', 'ticket_id': 'one',
            'probe': 'P', 'beat': 2,
        }])

    def test_two_probes_compete_for_the_same_physical_beat(self):
        self.register_day({2: ['P'], 9: ['P', 'Q']})
        book(self.db, 'day', 'first', 9, 'P', 100)
        with self.assertRaisesRegex(ValueError, 'already reserved'):
            book(self.db, 'day', 'second', 9, 'Q', 100)
        self.assertIsNone(self.device.advance(self.db, 'day', 2))
        command = self.device.advance(self.db, 'day', 9)
        self.assertEqual((command.ticket_id, command.beat, command.probe), ('first', 9, 'P'))
        self.assertEqual(list(self.device.commands), [command.trigger_id])

    def test_rejected_bookings_leave_a_later_beat_available(self):
        self.register_day({1: ['P'], 2: ['P'], 9: ['Q']})
        book(self.db, 'day', 'first', 2, 'P', 100)
        self.assertIsNone(self.device.advance(self.db, 'day', 1))
        for ticket_id, beat, probe, target in (
            ('unknown-beat', 3, 'P', 100),
            ('wrong-probe', 9, 'P', 100),
            ('old-beat', 1, 'P', 100),
            ('first', 9, 'Q', 100),
            ('false-target', 9, 'Q', False),
        ):
            with self.subTest(ticket=ticket_id):
                with self.assertRaises(ValueError):
                    book(self.db, 'day', ticket_id, beat, probe, target)
        with self.assertRaises(LookupError):
            book(self.db, 'missing', 'other', 9, 'Q', 100)
        self.assertEqual(book(self.db, 'day', 'last', 9, 'Q', 90), 'last')
        first = self.device.advance(self.db, 'day', 2)
        last = self.device.advance(self.db, 'day', 9)
        self.assertEqual([(first.ticket_id, first.beat), (last.ticket_id, last.beat)],
                         [('first', 2), ('last', 9)])
