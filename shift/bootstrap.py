from __future__ import annotations

from os import PathLike
import sqlite3

from device.journal import create_device_schema
from device.recovery import create_recovery_schema
from handover.archive import create_report_schema
from inspection.receipts import create_receipt_schema
from sampling.bookings import create_booking_schema
from shift.calendar import create_calendar_schema
from shift.storage import write_transaction


def initialize_workspace(db: sqlite3.Connection) -> None:
    with write_transaction(db):
        create_calendar_schema(db)
        create_booking_schema(db)
        create_device_schema(db)
        create_recovery_schema(db)
        create_receipt_schema(db)
        create_report_schema(db)


def open_workspace(
    path: str | PathLike[str], *, timeout: float = 5.0
) -> sqlite3.Connection:
    db = sqlite3.connect(path, timeout=timeout, isolation_level=None)
    try:
        db.execute('PRAGMA foreign_keys = ON')
        initialize_workspace(db)
    except BaseException:
        db.close()
        raise
    return db
