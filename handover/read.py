from dataclasses import dataclass
from sqlite3 import Connection

from handover.archive import select_report_rows
from handover.reconcile import HandoverRow
from handover.totals import ReportTotals
from shift.storage import validate_record_id


@dataclass(frozen=True)
class Report:
    report_id: int
    shift_id: str
    rows: tuple[HandoverRow, ...]
    totals: ReportTotals


def get_report(db: Connection, report_id: int) -> Report:
    validate_record_id(report_id, 'report')
    shift_id, totals, rows = select_report_rows(db, report_id)
    return Report(report_id, shift_id, rows, totals)
