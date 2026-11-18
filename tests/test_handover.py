from contextlib import closing
import sqlite3

from device.receive import receive
from handover.publish import publish
from handover.read import get_report
from handover.reconcile import HandoverStatus
from handover.totals import ReportTotals
from inspection.receipts import receipt
from sampling.planner import book
from shift.bootstrap import open_workspace
from tests.support import WorkspaceTest, deny_commit


class HandoverTests(WorkspaceTest):
    def test_four_statuses_and_historical_reopen_after_late_reply(self):
        self._sample_bookings()
        self.assertIsNone(self.device.advance(self.db, 'day', 1))
        second = self.device.advance(self.db, 'day', 2)
        ninth = self.device.advance(self.db, 'day', 9)
        tenth = self.device.advance(self.db, 'day', 10)
        outside = self.device.release(self.db, ninth.trigger_id, 96)
        passed = self.device.release(self.db, second.trigger_id, 101)
        old_id = publish(self.db, 'day')
        old = get_report(self.db, old_id)
        self.assertEqual(old.shift_id, 'day')
        self.assertEqual(old.totals, ReportTotals(1, 1, 1, 1))
        self.assertEqual(self._row_facts(old), [
            ('s2', 2, 'P', 'passed', second.trigger_id, passed.receipt_id, 101, 103, 3),
            ('s9', 9, 'Q', 'outside', ninth.trigger_id, outside.receipt_id, 96, 95, -5),
            ('s10', 10, 'P', 'pending', tenth.trigger_id, None, None, None, None),
            ('s12', 12, 'Q', 'untriggered', None, None, None, None, None),
        ])
        late = self.device.release(self.db, tenth.trigger_id, 97)
        new_id = publish(self.db, 'day')
        self.assertNotEqual(new_id, old_id)
        self.reopen()
        self.assertEqual(get_report(self.db, old_id), old)
        newer = get_report(self.db, new_id)
        self.assertEqual(newer.totals, ReportTotals(1, 0, 2, 1))
        self.assertEqual(self._row_facts(newer), [
            ('s2', 2, 'P', 'passed', second.trigger_id, passed.receipt_id, 101, 103, 3),
            ('s9', 9, 'Q', 'outside', ninth.trigger_id, outside.receipt_id, 96, 95, -5),
            ('s10', 10, 'P', 'passed', tenth.trigger_id, late.receipt_id, 97, 99, -1),
            ('s12', 12, 'Q', 'untriggered', None, None, None, None, None),
        ])
        for result in (passed, outside, late):
            self.assertEqual(receipt(self.db, result.receipt_id), result)

    def test_numeric_order_is_archived_even_when_booked_in_reverse(self):
        self._sample_bookings()
        report_id = publish(self.db, 'day')
        expected = [('s2', 2), ('s9', 9), ('s10', 10), ('s12', 12)]
        self.assertEqual([(row.ticket_id, row.beat) for row in get_report(self.db, report_id).rows],
                         expected)
        self.reopen()
        report = get_report(self.db, report_id)
        self.assertEqual([(row.ticket_id, row.beat) for row in report.rows], expected)
        self.assertEqual(report.totals, ReportTotals(4, 0, 0, 0))

    def test_empty_shift_and_unknown_lookups(self):
        self.register_day({})
        with self.assertRaises(LookupError):
            publish(self.db, 'missing')
        first_id = publish(self.db, 'day')
        next_id = publish(self.db, 'day')
        self.assertNotEqual(first_id, next_id)
        self.reopen()
        for report_id in (first_id, next_id):
            report = get_report(self.db, report_id)
            self.assertEqual((report.report_id, report.shift_id, report.rows, report.totals),
                             (report_id, 'day', (), ReportTotals(0, 0, 0, 0)))
        with self.assertRaises(LookupError):
            get_report(self.db, 999)

    def test_report_commit_failure_rolls_back_header_and_rows(self):
        self.register_day({2: ['P']})
        book(self.db, 'day', 's2', 2, 'P', 100)
        old_id = publish(self.db, 'day')
        old = get_report(self.db, old_id)
        command = self.device.advance(self.db, 'day', 2)
        result = self.device.release(self.db, command.trigger_id, 101)
        statements = []
        self.db.set_trace_callback(statements.append)
        try:
            with deny_commit(self.db), self.assertRaisesRegex(sqlite3.DatabaseError, 'not authorized'):
                publish(self.db, 'day')
        finally:
            self.db.set_trace_callback(None)
        self.assertTrue(any('INSERT INTO handover_reports' in sql for sql in statements))
        self.assertTrue(any('INSERT INTO handover_rows' in sql for sql in statements))
        with closing(open_workspace(self.path)) as observer:
            self.assertEqual(get_report(observer, old_id), old)
            self.assertEqual(observer.execute('SELECT report_id FROM handover_reports').fetchall(),
                             [(old_id,)])
            self.assertEqual(observer.execute('SELECT report_id FROM handover_rows').fetchall(),
                             [(old_id,)])
        new_id = publish(self.db, 'day')
        current = get_report(self.db, new_id)
        self.assertNotEqual(new_id, old_id)
        self.assertEqual(current.totals, ReportTotals(0, 0, 1, 0))
        self.assertEqual(current.rows[0].receipt_id, result.receipt_id)
        self.assertEqual(get_report(self.db, old_id), old)

    def test_publish_reads_one_view_while_another_connection_completes(self):
        mode = self.db.execute('PRAGMA journal_mode = WAL').fetchone()[0]
        self.assertEqual(mode, 'wal')
        self.register_day({2: ['P'], 9: ['Q']})
        book(self.db, 'day', 's2', 2, 'P', 100)
        book(self.db, 'day', 's9', 9, 'Q', 100)
        command = self.device.advance(self.db, 'day', 2)
        attempts = []
        with closing(open_workspace(self.path, timeout=0)) as writer:
            def reply_between_reads(sql):
                # The bookings have been read when reconciliation starts reading triggers.
                if 'FROM triggers WHERE shift_id' not in ' '.join(sql.split()):
                    return
                try:
                    attempts.append(receive(writer, command.trigger_id, 101))
                except Exception as error:
                    attempts.append(error)

            self.db.set_trace_callback(reply_between_reads)
            try:
                old_id = publish(self.db, 'day')
            finally:
                self.db.set_trace_callback(None)
            self.assertEqual(len(attempts), 1)
            self.assertIsInstance(attempts[0], sqlite3.OperationalError)
            self.assertIn('locked', str(attempts[0]))
            self.assertEqual(writer.execute('SELECT count(*) FROM receipts').fetchone(), (0,))
            old = get_report(writer, old_id)
            self.assertEqual([row.status for row in old.rows],
                             [HandoverStatus.PENDING, HandoverStatus.UNTRIGGERED])
            self.assertEqual(old.totals, ReportTotals(1, 1, 0, 0))
            self.assertIsNone(old.rows[0].raw_um)
            result = self.device.release(writer, command.trigger_id, 101)
        newer = get_report(self.db, publish(self.db, 'day'))
        self.assertEqual([row.status for row in newer.rows],
                         [HandoverStatus.PASSED, HandoverStatus.UNTRIGGERED])
        self.assertEqual(newer.totals, ReportTotals(1, 0, 1, 0))
        self.assertEqual((newer.rows[0].receipt_id, newer.rows[0].raw_um), (result.receipt_id, 101))
        self.assertEqual(get_report(self.db, old_id), old)

    def _sample_bookings(self):
        self.register_day({1: ['P'], 2: ['P'], 9: ['Q'], 10: ['P'], 12: ['Q']})
        for beat, probe in ((12, 'Q'), (10, 'P'), (9, 'Q'), (2, 'P')):
            book(self.db, 'day', f's{beat}', beat, probe, 100)

    @staticmethod
    def _row_facts(report):
        return [
            (row.ticket_id, row.beat, row.probe, row.status.value, row.trigger_id, row.receipt_id,
             row.raw_um, row.corrected_um, row.deviation_um)
            for row in report.rows
        ]
