from contextlib import closing
import sqlite3

from device.journal import load_trigger
from handover.publish import publish
from handover.read import get_report
from handover.reconcile import HandoverStatus
from inspection.judgment import Verdict
from inspection.receipts import receipt
from sampling.planner import book
from shift.bootstrap import open_workspace
from tests.support import WorkspaceTest, deny_commit


class ReceiptTests(WorkspaceTest):
    def test_commit_failure_rolls_back_receipt_and_completion(self):
        self.register_day({2: ['P']})
        book(self.db, 'day', 'in-flight', 2, 'P', 100)
        command = self.device.advance(self.db, 'day', 2)
        statements = []
        self.db.set_trace_callback(statements.append)
        try:
            with deny_commit(self.db), self.assertRaisesRegex(sqlite3.DatabaseError, 'not authorized'):
                self.device.release(self.db, command.trigger_id, 101)
        finally:
            self.db.set_trace_callback(None)
        self.assertTrue(any('INSERT INTO receipts' in sql for sql in statements))
        self.assertTrue(any('UPDATE triggers SET receipt_id' in sql for sql in statements))
        self.assertFalse(self.db.in_transaction)
        with closing(open_workspace(self.path)) as observer:
            self.assertEqual(observer.execute('SELECT count(*) FROM receipts').fetchone(), (0,))
            self.assertIsNone(load_trigger(observer, command.trigger_id).receipt_id)
            with self.assertRaises(LookupError):
                receipt(observer, 1)
        pending_id = publish(self.db, 'day')
        pending = get_report(self.db, pending_id)
        self.assertEqual(len(pending.rows), 1)
        row = pending.rows[0]
        self.assertEqual((row.status, row.trigger_id, row.receipt_id, row.raw_um),
                         (HandoverStatus.PENDING, command.trigger_id, None, None))

        result = self.device.release(self.db, command.trigger_id, 101)
        self.assertEqual((result.ticket_id, result.trigger_id, result.verdict),
                         ('in-flight', command.trigger_id, Verdict.PASS))
        self.assertEqual(load_trigger(self.db, command.trigger_id).receipt_id, result.receipt_id)
        self.assertEqual(receipt(self.db, result.receipt_id), result)
        self.assertEqual(self.device.release(self.db, command.trigger_id, 101), result)
        self.assertEqual(self.db.execute('SELECT count(*) FROM receipts').fetchone(), (1,))
        self.assertEqual(get_report(self.db, pending_id), pending)
        later = get_report(self.db, publish(self.db, 'day'))
        self.assertEqual((later.rows[0].status, later.rows[0].receipt_id, later.rows[0].raw_um),
                         (HandoverStatus.PASSED, result.receipt_id, 101))
