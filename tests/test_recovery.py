from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing
import sqlite3

from device.clock import Confirmed, Paused, advance, enable_recovery, tick
from device.journal import read_cursor
from device.receive import receive
from device.recovery_port import Evidence, Exposure, FenceLost, ReplyLost
from handover.publish import publish
from handover.read import get_report
from inspection.judgment import Verdict
from recovery_fixture import ScriptedHead, check_fixture
from sampling.planner import book
from shift.bootstrap import open_workspace
from tests.support import WorkspaceTest, deny_commit


class SpyHead:
    """Record port calls so tests can prove a path never touched the device."""

    def __init__(self, inner):
        self.inner = inner
        self.calls = []

    @property
    def device_id(self):
        return self.inner.device_id

    def takeover(self):
        self.calls.append('takeover')
        return self.inner.takeover()

    def observe(self, lease):
        self.calls.append('observe')
        return self.inner.observe(lease)

    def expose(self, lease, exposure):
        self.calls.append('expose')
        return self.inner.expose(lease, exposure)


class RecoveryTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.head = ScriptedHead(sequence=40, latest=Exposure('setup', 8, 'Q'))

    def prepare(self, slots=None):
        self.register_day({9: ['Q'], 10: ['Q']} if slots is None else slots)
        book(self.db, 'day', 'A', 9, 'Q', 100)
        self.assertEqual(enable_recovery(self.db, 'day', 'head-Q'), 'day')

    def head_evidence(self):
        return self.head.observe(self.head.takeover())

    def test_reply_lost_before_exposure_redispatches_once_after_reopen(self):
        self.prepare()
        self.head.arm_loss('before')
        with self.assertRaises(ReplyLost):
            advance(self.db, 'day', 9, self.head)
        self.assertEqual(read_cursor(self.db, 'day'), 0)
        self.assertEqual(self.head_evidence().sequence, 40)
        self.reopen()
        result = advance(self.db, 'day', 9, self.head)
        self.assertIsInstance(result, Confirmed)
        self.assertEqual((result.command.ticket_id, result.command.beat, result.command.probe),
                         ('A', 9, 'Q'))
        self.assertEqual(result.evidence, Evidence('G', 41, Exposure('day', 9, 'Q')))
        self.assertEqual(self.head_evidence().sequence, 41)

    def test_reply_lost_after_exposure_confirms_original_after_reopen(self):
        self.prepare()
        self.head.arm_loss('after')
        with self.assertRaises(ReplyLost):
            advance(self.db, 'day', 9, self.head)
        self.assertEqual(self.head_evidence(), Evidence('G', 41, Exposure('day', 9, 'Q')))
        self.reopen()
        result = advance(self.db, 'day', 9, self.head)
        self.assertEqual(result.evidence, Evidence('G', 41, Exposure('day', 9, 'Q')))
        self.assertEqual((result.command.ticket_id, result.command.beat), ('A', 9))
        self.assertEqual(self.head_evidence().sequence, 41)

    def test_late_old_command_is_fenced_and_never_counted(self):
        self.prepare()
        gate = self.head.hold_next()
        with ThreadPoolExecutor(max_workers=1) as pool:
            stuck = pool.submit(self._advance_on_own_connection)
            self.assertTrue(gate.ready.wait(5), 'old command never reached the gate')
            try:
                result = advance(self.db, 'day', 9, self.head)
            finally:
                gate.release.set()
            with self.assertRaises(FenceLost):
                stuck.result(timeout=5)
        self.assertEqual(result.evidence, Evidence('G', 41, Exposure('day', 9, 'Q')))
        self.assertEqual(self.head_evidence().sequence, 41)
        self.reopen()
        self.assertEqual(advance(self.db, 'day', 9, self.head), result)

    def _advance_on_own_connection(self):
        with closing(open_workspace(self.path)) as db:
            return advance(db, 'day', 9, self.head)

    def test_generation_change_pauses_and_blocks_later_beats(self):
        self.prepare()
        self.head.arm_loss('before')
        with self.assertRaises(ReplyLost):
            advance(self.db, 'day', 9, self.head)
        self.head.reset('H')
        self.reopen()
        self.assertEqual(advance(self.db, 'day', 9, self.head), Paused(Evidence('H', 0, None)))
        report = get_report(self.db, publish(self.db, 'day'))
        self.assertEqual([(row.ticket_id, row.status.value) for row in report.rows],
                         [('A', 'untriggered')])
        with self.assertRaisesRegex(ValueError, 'out of order'):
            advance(self.db, 'day', 10, self.head)
        self.assertEqual(advance(self.db, 'day', 9, self.head), Paused(Evidence('H', 0, None)))
        self.assertEqual(read_cursor(self.db, 'day'), 0)

    def test_foreign_latest_exposure_pauses_and_keeps_baseline(self):
        self.prepare()
        self.head.arm_loss('before')
        with self.assertRaises(ReplyLost):
            advance(self.db, 'day', 9, self.head)
        lease = self.head.takeover()
        self.head.expose(lease, Exposure('day', 9, 'P'))
        self.reopen()
        paused = advance(self.db, 'day', 9, self.head)
        self.assertEqual(paused, Paused(Evidence('G', 41, Exposure('day', 9, 'P'))))
        report = get_report(self.db, publish(self.db, 'day'))
        self.assertEqual([row.status.value for row in report.rows], ['untriggered'])
        with self.assertRaisesRegex(ValueError, 'out of order'):
            advance(self.db, 'day', 10, self.head)

    def test_confirm_commit_failure_recovers_without_an_extra_exposure(self):
        self.prepare()
        with ExitStack() as faults:
            self.head.after_next_exposure(
                lambda: faults.enter_context(deny_commit(self.db))
            )
            with self.assertRaisesRegex(sqlite3.DatabaseError, 'not authorized'):
                advance(self.db, 'day', 9, self.head)
        self.assertEqual(self.head_evidence().sequence, 41)
        self.assertEqual(read_cursor(self.db, 'day'), 0)
        with self.assertRaises(LookupError):
            receive(self.db, 1, 101)
        report = get_report(self.db, publish(self.db, 'day'))
        self.assertEqual([(row.ticket_id, row.status.value) for row in report.rows],
                         [('A', 'untriggered')])
        self.reopen()
        result = advance(self.db, 'day', 9, self.head)
        self.assertEqual(result.evidence, Evidence('G', 41, Exposure('day', 9, 'Q')))
        self.assertEqual(self.head_evidence().sequence, 41)
        signed = receive(self.db, result.command.trigger_id, 101)
        self.assertEqual((signed.ticket_id, signed.beat, signed.verdict),
                         ('A', 9, Verdict.PASS))

    def test_confirmed_trigger_receives_and_replays_by_original_numbers(self):
        self.prepare()
        before_id = publish(self.db, 'day')
        result = advance(self.db, 'day', 9, self.head)
        trigger_id = result.command.trigger_id
        first = receive(self.db, trigger_id, 101)
        measured = first.measurement
        self.assertEqual((first.ticket_id, first.beat, measured.raw_um, measured.corrected_um,
                          measured.deviation_um, first.verdict),
                         ('A', 9, 101, 100, 0, Verdict.PASS))
        self.assertEqual(receive(self.db, trigger_id, 101), first)
        old = get_report(self.db, before_id)
        self.assertEqual([row.status.value for row in old.rows], ['untriggered'])
        after = get_report(self.db, publish(self.db, 'day'))
        self.assertEqual([(row.status.value, row.receipt_id) for row in after.rows],
                         [('passed', first.receipt_id)])
        self.reopen()
        self.assertEqual(receive(self.db, trigger_id, 101), first)
        self.assertEqual(get_report(self.db, before_id), old)

    def test_replay_scope_is_the_latest_confirmed_beat_only(self):
        self.register_day({2: [], 9: ['Q'], 10: ['Q'], 12: ['Q']})
        book(self.db, 'day', 'A', 9, 'Q', 100)
        book(self.db, 'day', 'B', 10, 'Q', 100)
        enable_recovery(self.db, 'day', 'head-Q')
        spy = SpyHead(self.head)
        self.assertIsNone(advance(self.db, 'day', 2, spy))
        self.assertEqual(spy.calls, [])
        self.assertIsNone(advance(self.db, 'day', 2, spy))
        self.assertEqual(spy.calls, [])
        confirmed = advance(self.db, 'day', 9, spy)
        used = len(spy.calls)
        lease = self.head.takeover()
        self.head.expose(lease, Exposure('elsewhere', 1, 'Q'))
        self.assertEqual(advance(self.db, 'day', 9, spy), confirmed)
        self.assertEqual(len(spy.calls), used)
        self.head.arm_loss('before')
        with self.assertRaises(ReplyLost):
            advance(self.db, 'day', 10, spy)
        self.assertEqual(advance(self.db, 'day', 9, spy), confirmed)
        final = advance(self.db, 'day', 10, spy)
        self.assertEqual(final.evidence, Evidence('G', 43, Exposure('day', 10, 'Q')))
        with self.assertRaisesRegex(ValueError, 'out of order'):
            advance(self.db, 'day', 9, spy)
        with self.assertRaisesRegex(ValueError, 'out of order'):
            advance(self.db, 'day', 2, spy)

    def test_enable_recovery_binding_rules(self):
        self.register_day({9: ['Q']})
        with self.assertRaises(LookupError):
            enable_recovery(self.db, 'missing', 'head-Q')
        for shift_id, device_id in (('', 'head-Q'), ('day', ''), ('day', 9), (9, 'head-Q')):
            with self.subTest(shift_id=shift_id, device_id=device_id):
                with self.assertRaises(ValueError):
                    enable_recovery(self.db, shift_id, device_id)
        self.assertEqual(enable_recovery(self.db, 'day', 'head-Q'), 'day')
        self.assertEqual(enable_recovery(self.db, 'day', 'head-Q'), 'day')
        with self.assertRaisesRegex(ValueError, 'different device'):
            enable_recovery(self.db, 'day', 'other-head')
        with self.assertRaisesRegex(ValueError, 'enabled for recovery'):
            tick(self.db, 'day', 9)

    def test_enable_recovery_rejects_a_shift_with_passed_beats(self):
        self.register_day({2: [], 9: ['Q']})
        self.assertIsNone(tick(self.db, 'day', 2))
        with self.assertRaisesRegex(ValueError, 'after the first beat'):
            enable_recovery(self.db, 'day', 'head-Q')
        self.assertIsNone(tick(self.db, 'day', 9))

    def test_advance_rejections_preserve_capture_state(self):
        self.register_day({9: ['Q']})
        book(self.db, 'day', 'A', 9, 'Q', 100)
        with self.assertRaises(LookupError):
            advance(self.db, 'missing', 9, self.head)
        with self.assertRaisesRegex(ValueError, 'not enabled'):
            advance(self.db, 'day', 9, self.head)
        enable_recovery(self.db, 'day', 'head-Q')
        for bad_beat in (0, -3, True, '9'):
            with self.subTest(beat=bad_beat):
                with self.assertRaises(ValueError):
                    advance(self.db, 'day', bad_beat, self.head)
        other = ScriptedHead(device_id='other-head', sequence=40,
                             latest=Exposure('setup', 8, 'Q'))
        with self.assertRaisesRegex(ValueError, 'does not match'):
            advance(self.db, 'day', 9, other)
        with self.assertRaisesRegex(ValueError, 'out of order'):
            advance(self.db, 'day', 10, self.head)
        self.assertEqual(read_cursor(self.db, 'day'), 0)
        result = advance(self.db, 'day', 9, self.head)
        self.assertEqual((result.command.ticket_id, result.evidence.sequence), ('A', 41))

    def test_device_fixture(self):
        check_fixture()
