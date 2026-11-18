from contextlib import closing
from dataclasses import asdict
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from device.clock import advance, enable_recovery
from device.receive import receive
from device.recovery_port import Evidence, Exposure, ReplyLost
from handover.publish import publish
from handover.read import get_report
from inspection.receipts import receipt
from recovery_fixture import ScriptedHead
from sampling.planner import book
from shift.bootstrap import open_workspace
from shift.calibration import Calibration
from shift.register import register
from tests.support import ScriptedDevice


def receipt_facts(result):
    measured = result.measurement
    return [result.receipt_id, result.trigger_id, result.shift_id, result.ticket_id, result.probe,
            result.beat, measured.raw_um, measured.corrected_um, measured.deviation_um,
            result.verdict.value]


def report_facts(report):
    return {
        'report_id': report.report_id,
        'shift_id': report.shift_id,
        'totals': asdict(report.totals),
        'rows': [
            [row.ticket_id, row.beat, row.status.value, row.trigger_id, row.receipt_id,
             row.raw_um, row.corrected_um, row.deviation_um]
            for row in report.rows
        ],
    }


RECOVERY_HANDOVER_NOTE = (
    '交接说明：advance 发令前先把当拍原预约和设备基准（世代、执行序号）持久化到工作库；'
    '只有设备回证与基准相符——同世代、序号恰增一、最近曝光的班号拍号探头一致——'
    '才在同一次 SQLite 提交里登记触发号、推进拍钟并清除待确认，发令证据由此成为可收值触发。'
    '确认前交接把当拍预约列为 untriggered，触发号尚不存在，原入口无法收值；'
    '丢回复或确认提交失败后重开工作库，按原基准接管、取证，同世代序号未变则补发一次，'
    '序号恰增一且最近曝光相符则确认原曝光，其余观察暂停并挡住后继拍；'
    '确认后凭原触发号 receive 收值，同值重投领原回执，历史交接保持签发时状态。'
)


def run_recovery_demo(path):
    """Two interrupted beats recover through real reopens and receive by original numbers."""
    head = ScriptedHead(sequence=40, latest=Exposure('setup', 8, 'Q'))
    interruptions = []
    with closing(open_workspace(path)) as db:
        register(db, 'day', {9: ['Q'], 10: ['Q']}, {'Q': Calibration(-1, 2)})
        book(db, 'day', 'A', 9, 'Q', 100)
        book(db, 'day', 'B', 10, 'Q', 100)
        enable_recovery(db, 'day', head.device_id)
        head.arm_loss('before')
        try:
            advance(db, 'day', 9, head)
        except ReplyLost:
            interruptions.append('beat 9: reply lost before the exposure, workspace closed')
    with closing(open_workspace(path)) as db:
        pending_report = get_report(db, publish(db, 'day'))
        confirmed9 = advance(db, 'day', 9, head)
        head.arm_loss('after')
        try:
            advance(db, 'day', 10, head)
        except ReplyLost:
            interruptions.append('beat 10: reply lost after the exposure, workspace closed')
    with closing(open_workspace(path)) as db:
        confirmed10 = advance(db, 'day', 10, head)
        receipt_a = receive(db, confirmed9.command.trigger_id, 101)
        replay_a = receive(db, confirmed9.command.trigger_id, 101)
        receipt_b = receive(db, confirmed10.command.trigger_id, 99)
        done_report = get_report(db, publish(db, 'day'))
    with closing(open_workspace(path)) as db:
        evidence = head.observe(head.takeover())
        archived = [receipt(db, item.receipt_id) for item in (receipt_a, receipt_b)]
        if archived != [receipt_a, receipt_b] or replay_a != receipt_a:
            raise AssertionError('reopen or replay changed a signed receipt')
        if get_report(db, pending_report.report_id) != pending_report:
            raise AssertionError('reopen changed the pending handover')
        done_rows = get_report(db, done_report.report_id)
    if evidence != Evidence('G', 42, Exposure('day', 10, 'Q')):
        raise AssertionError(f'unexpected device evidence {evidence!r}')
    if confirmed9.evidence != Evidence('G', 41, Exposure('day', 9, 'Q')):
        raise AssertionError(f'unexpected beat 9 proof {confirmed9.evidence!r}')
    if confirmed10.evidence != evidence:
        raise AssertionError(f'unexpected beat 10 proof {confirmed10.evidence!r}')
    outcomes = {
        'interruptions': interruptions,
        'device': {
            'device_id': head.device_id,
            'generation': evidence.generation,
            'sequence': evidence.sequence,
            'latest': asdict(evidence.latest),
        },
        'confirmed': {
            'A': {'trigger_id': confirmed9.command.trigger_id, 'beat': 9,
                  'evidence': asdict(confirmed9.evidence)},
            'B': {'trigger_id': confirmed10.command.trigger_id, 'beat': 10,
                  'evidence': asdict(confirmed10.evidence)},
        },
        'receipts': [receipt_facts(item) for item in archived],
        'replayed_receipt_id': replay_a.receipt_id,
        'handovers': {'pending': report_facts(pending_report), 'done': report_facts(done_rows)},
    }
    expected_receipts = [
        [1, 1, 'day', 'A', 'Q', 9, 101, 100, 0, 'pass'],
        [2, 2, 'day', 'B', 'Q', 10, 99, 98, -2, 'pass'],
    ]
    if outcomes['receipts'] != expected_receipts:
        raise AssertionError(f"recovery receipts differ: {outcomes['receipts']!r}")
    pending_rows = [['A', 9, 'untriggered', None, None, None, None, None],
                    ['B', 10, 'untriggered', None, None, None, None, None]]
    done_rows = [['A', 9, 'passed', 1, 1, 101, 100, 0],
                 ['B', 10, 'passed', 2, 2, 99, 98, -2]]
    if outcomes['handovers']['pending']['rows'] != pending_rows:
        raise AssertionError(f"pending handover differs: {outcomes['handovers']['pending']!r}")
    if outcomes['handovers']['done']['rows'] != done_rows:
        raise AssertionError(f"done handover differs: {outcomes['handovers']['done']!r}")
    return outcomes


def run_demo(sample, path):
    shift_id = sample['shift_id']
    calibrations = {
        name: Calibration(correction, tolerance)
        for name, (correction, tolerance) in sample['calibrations'].items()
    }
    device = ScriptedDevice()
    empty_beats = []
    triggers = {}
    with closing(open_workspace(path)) as db:
        register(db, shift_id, dict(sample['slots']), calibrations)
        for ticket_id, beat, probe, target in sample['tickets']:
            book(db, shift_id, ticket_id, beat, probe, target)
        for beat in sample['advance']:
            command = device.advance(db, shift_id, beat)
            if command is None:
                empty_beats.append(beat)
            else:
                triggers[beat] = command.trigger_id
        signed = [
            device.release(db, triggers[beat], raw) for beat, raw in sample['early_replies']
        ]
        beat, raw = sample['replay']
        replay = device.release(db, triggers[beat], raw)
        before = publish(db, shift_id)
        beat, raw = sample['late_reply']
        signed.append(device.release(db, triggers[beat], raw))
        after = publish(db, shift_id)

    with closing(open_workspace(path)) as reopened:
        archived = [receipt(reopened, item.receipt_id) for item in signed]
        if archived != signed or replay != signed[0]:
            raise AssertionError('reopen or replay changed a signed receipt')
        actual = {
            'empty_beats': empty_beats,
            'commands': list(device.commands.values()),
            'receipts': [receipt_facts(item) for item in archived],
            'replayed_receipt_id': replay.receipt_id,
        }
        downstream = {
            'before': report_facts(get_report(reopened, before)),
            'after': report_facts(get_report(reopened, after)),
        }
    return actual, downstream


def main():
    sample_path = Path(__file__).with_name('demo-shift.json')
    sample = json.loads(sample_path.read_text(encoding='utf-8'))
    with TemporaryDirectory(prefix='fixed-sampling-demo-') as directory:
        actual, downstream = run_demo(sample, Path(directory) / 'lab.sqlite3')
    with TemporaryDirectory(prefix='fixed-sampling-recovery-') as directory:
        recovery = run_recovery_demo(Path(directory) / 'lab.sqlite3')

    expected = {
        'empty_beats': [1],
        'commands': [
            {'trigger_id': 1, 'shift_id': 'CC-day', 'ticket_id': 'S2', 'probe': 'P', 'beat': 2},
            {'trigger_id': 2, 'shift_id': 'CC-day', 'ticket_id': 'S9', 'probe': 'Q', 'beat': 9},
            {'trigger_id': 3, 'shift_id': 'CC-day', 'ticket_id': 'S10', 'probe': 'P', 'beat': 10},
        ],
        'receipts': [
            [1, 2, 'CC-day', 'S9', 'Q', 9, 96, 95, -5, 'outside'],
            [2, 1, 'CC-day', 'S2', 'P', 2, 101, 103, 3, 'pass'],
            [3, 3, 'CC-day', 'S10', 'P', 10, 97, 99, -1, 'pass'],
        ],
        'replayed_receipt_id': 1,
    }
    before_rows = [
        ['S2', 2, 'passed', 1, 2, 101, 103, 3],
        ['S9', 9, 'outside', 2, 1, 96, 95, -5],
        ['S10', 10, 'pending', 3, None, None, None, None],
        ['S12', 12, 'untriggered', None, None, None, None, None],
    ]
    after_rows = [*before_rows[:2], ['S10', 10, 'passed', 3, 3, 97, 99, -1], before_rows[3]]
    downstream_expected = {
        'before': {'report_id': 1, 'shift_id': 'CC-day', 'rows': before_rows,
                   'totals': {'untriggered': 1, 'pending': 1, 'passed': 1, 'outside': 1}},
        'after': {'report_id': 2, 'shift_id': 'CC-day', 'rows': after_rows,
                  'totals': {'untriggered': 1, 'pending': 0, 'passed': 2, 'outside': 1}},
    }
    if actual != expected:
        raise AssertionError(f'capture outcomes differ: {actual!r}')
    if downstream != downstream_expected:
        raise AssertionError(f'archived reports differ: {downstream!r}')
    scenario = {
        'case_id': 'v8',
        'input': sample,
        'expected': expected,
        'actual': actual,
        'downstream_expected': downstream_expected,
        'downstream_actual': downstream,
        'code_paths': ['sampling/planner.py', 'device/clock.py', 'device/receive.py',
                       'inspection/measure.py', 'inspection/judgment.py', 'handover/reconcile.py',
                       'handover/publish.py', 'handover/read.py'],
        'oracle': '按样例命令顺序核对触发，手算 96−1=95、101+2=103、97+2=99，逐件确认两次交接的在途状态、原拍及原回执。',
    }
    print('POINTWISE_SCENARIO ' + json.dumps(scenario, ensure_ascii=False, sort_keys=True))
    print('RECOVERY_DEMO ' + json.dumps(recovery, ensure_ascii=False, sort_keys=True))
    print(RECOVERY_HANDOVER_NOTE)


if __name__ == '__main__':
    main()
