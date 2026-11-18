from sqlite3 import Connection

from handover.archive import insert_report_snapshot
from handover.reconcile import reconcile_shift
from handover.totals import count_statuses
from shift.calendar import load_calendar
from shift.storage import write_transaction


def publish(db: Connection, shift_id: str) -> int:
    with write_transaction(db):
        calendar = load_calendar(db, shift_id)
        rows = reconcile_shift(db, calendar.shift_id)
        totals = count_statuses(rows)
        report_id = insert_report_snapshot(db, calendar.shift_id, rows, totals)
    return report_id
