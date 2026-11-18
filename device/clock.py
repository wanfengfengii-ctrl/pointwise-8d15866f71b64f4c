from __future__ import annotations

from sqlite3 import Connection

from device.commands import Command
from device.dispatch import dispatch_due
from device.journal import advance_cursor, read_cursor
from shift.calendar import load_calendar
from shift.storage import write_transaction


def tick(db: Connection, shift_id: str, beat: int) -> Command | None:
    if type(beat) is not int or beat <= 0:
        raise ValueError('beat must be a positive integer')
    with write_transaction(db):
        calendar = load_calendar(db, shift_id)
        next_beat = calendar.next_beat_after(read_cursor(db, shift_id))
        if next_beat is None:
            raise ValueError('the shift has no more scheduled beats')
        if beat != next_beat:
            raise ValueError('beat is out of order')
        command = dispatch_due(db, shift_id, beat)
        advance_cursor(db, shift_id, beat)
    return command
