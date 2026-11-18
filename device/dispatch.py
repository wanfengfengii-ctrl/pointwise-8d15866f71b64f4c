from __future__ import annotations

from sqlite3 import Connection

from device.commands import Command
from device.journal import record_trigger
from sampling.queue import due_task


def dispatch_due(db: Connection, shift_id: str, beat: int) -> Command | None:
    task = due_task(db, shift_id, beat)
    if task is None:
        return None
    return record_trigger(db, task.shift_id, task.ticket_id, task.probe, task.beat)
