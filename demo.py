from contextlib import closing
from dataclasses import asdict
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from handover.publish import publish
from handover.read import get_report
from inspection.receipts import receipt
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


if __name__ == '__main__':
    main()
