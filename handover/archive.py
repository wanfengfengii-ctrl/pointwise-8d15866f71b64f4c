from __future__ import annotations

from sqlite3 import Connection

from handover.reconcile import HandoverRow, HandoverStatus
from handover.totals import ReportTotals
from shift.storage import decode_integer, encode_integer


def create_report_schema(db: Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS handover_reports (
            report_id INTEGER PRIMARY KEY,
            shift_id TEXT NOT NULL REFERENCES calendars(shift_id),
            untriggered INTEGER NOT NULL CHECK (untriggered >= 0),
            pending INTEGER NOT NULL CHECK (pending >= 0),
            passed INTEGER NOT NULL CHECK (passed >= 0),
            outside INTEGER NOT NULL CHECK (outside >= 0)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS handover_rows (
            report_id INTEGER NOT NULL REFERENCES handover_reports(report_id),
            ordinal INTEGER NOT NULL,
            ticket_id TEXT NOT NULL REFERENCES tickets(ticket_id),
            beat TEXT NOT NULL,
            probe TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('untriggered', 'pending', 'passed', 'outside')),
            trigger_id INTEGER REFERENCES triggers(trigger_id),
            receipt_id INTEGER REFERENCES receipts(receipt_id),
            raw TEXT,
            corrected TEXT,
            deviation TEXT,
            PRIMARY KEY (report_id, ordinal),
            UNIQUE (report_id, ticket_id)
        )
    ''')


def _encode_optional(value: int | None) -> str | None:
    return None if value is None else encode_integer(value)


def _decode_optional(value: str | None) -> int | None:
    return None if value is None else decode_integer(value)


def insert_report_snapshot(
    db: Connection, shift_id: str, rows: tuple[HandoverRow, ...], totals: ReportTotals
) -> int:
    header = db.execute(
        '''INSERT INTO handover_reports (shift_id, untriggered, pending, passed, outside)
           VALUES (?, ?, ?, ?, ?)''',
        (shift_id, totals.untriggered, totals.pending, totals.passed, totals.outside),
    )
    report_id = header.lastrowid
    db.executemany(
        '''INSERT INTO handover_rows (report_id, ordinal, ticket_id, beat, probe, status,
                                      trigger_id, receipt_id, raw, corrected, deviation)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (
            (
                report_id, ordinal, row.ticket_id, encode_integer(row.beat), row.probe,
                row.status.value, row.trigger_id, row.receipt_id, _encode_optional(row.raw_um),
                _encode_optional(row.corrected_um), _encode_optional(row.deviation_um),
            )
            for ordinal, row in enumerate(rows)
        ),
    )
    return report_id


def select_report_rows(
    db: Connection, report_id: int
) -> tuple[str, ReportTotals, tuple[HandoverRow, ...]]:
    header = db.execute(
        '''SELECT shift_id, untriggered, pending, passed, outside
           FROM handover_reports WHERE report_id = ?''',
        (report_id,),
    ).fetchone()
    if header is None:
        raise LookupError(f'unknown report {report_id}')
    shift_id, untriggered, pending, passed, outside = header
    totals = ReportTotals(untriggered, pending, passed, outside)
    records = db.execute(
        '''SELECT ticket_id, beat, probe, status, trigger_id, receipt_id, raw, corrected, deviation
           FROM handover_rows WHERE report_id = ? ORDER BY ordinal''',
        (report_id,),
    )
    rows = tuple(
        HandoverRow(
            ticket_id, decode_integer(beat), probe, HandoverStatus(status), trigger_id, receipt_id,
            _decode_optional(raw), _decode_optional(corrected), _decode_optional(deviation),
        )
        for ticket_id, beat, probe, status, trigger_id, receipt_id, raw, corrected, deviation in records
    )
    return shift_id, totals, rows
