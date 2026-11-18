from __future__ import annotations

from dataclasses import dataclass
from sqlite3 import Connection

from device.commands import Command
from shift.storage import decode_integer, encode_integer, validate_record_id


@dataclass(frozen=True)
class Trigger:
    command: Command
    receipt_id: int | None


def create_device_schema(db: Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS beat_cursors (
            shift_id TEXT PRIMARY KEY NOT NULL,
            beat TEXT NOT NULL,
            FOREIGN KEY (shift_id, beat) REFERENCES calendar_slots(shift_id, beat)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS triggers (
            trigger_id INTEGER PRIMARY KEY,
            shift_id TEXT NOT NULL,
            ticket_id TEXT NOT NULL UNIQUE REFERENCES tickets(ticket_id),
            probe TEXT NOT NULL,
            beat TEXT NOT NULL,
            receipt_id INTEGER UNIQUE REFERENCES receipts(receipt_id),
            UNIQUE (shift_id, beat),
            FOREIGN KEY (shift_id, beat, probe)
                REFERENCES calendar_slot_probes(shift_id, beat, probe)
        )
    ''')


def read_cursor(db: Connection, shift_id: str) -> int:
    row = db.execute(
        'SELECT beat FROM beat_cursors WHERE shift_id = ?', (shift_id,)
    ).fetchone()
    return 0 if row is None else decode_integer(row[0])


def advance_cursor(db: Connection, shift_id: str, beat: int) -> None:
    db.execute(
        '''INSERT INTO beat_cursors (shift_id, beat) VALUES (?, ?)
           ON CONFLICT (shift_id) DO UPDATE SET beat = excluded.beat''',
        (shift_id, encode_integer(beat)),
    )


def record_trigger(
    db: Connection, shift_id: str, ticket_id: str, probe: str, beat: int
) -> Command:
    inserted = db.execute(
        'INSERT INTO triggers (shift_id, ticket_id, probe, beat) VALUES (?, ?, ?, ?)',
        (shift_id, ticket_id, probe, encode_integer(beat)),
    )
    return Command(inserted.lastrowid, shift_id, ticket_id, probe, beat)


def _restore_trigger(row: tuple[int, str, str, str, str, int | None]) -> Trigger:
    trigger_id, shift_id, ticket_id, probe, beat, receipt_id = row
    command = Command(trigger_id, shift_id, ticket_id, probe, decode_integer(beat))
    return Trigger(command, receipt_id)


def load_trigger(db: Connection, trigger_id: int) -> Trigger:
    validate_record_id(trigger_id, 'trigger')
    row = db.execute(
        '''SELECT trigger_id, shift_id, ticket_id, probe, beat, receipt_id
           FROM triggers WHERE trigger_id = ?''',
        (trigger_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f'unknown trigger {trigger_id}')
    return _restore_trigger(row)


def list_shift_triggers(db: Connection, shift_id: str) -> tuple[Trigger, ...]:
    rows = db.execute(
        '''SELECT trigger_id, shift_id, ticket_id, probe, beat, receipt_id
           FROM triggers WHERE shift_id = ?''',
        (shift_id,),
    )
    return tuple(_restore_trigger(row) for row in rows)


def complete_trigger(db: Connection, trigger_id: int, receipt_id: int) -> None:
    updated = db.execute(
        '''UPDATE triggers SET receipt_id = ?
           WHERE trigger_id = ? AND receipt_id IS NULL
             AND EXISTS (SELECT 1 FROM receipts WHERE receipt_id = ? AND trigger_id = ?)''',
        (receipt_id, trigger_id, receipt_id, trigger_id),
    )
    if updated.rowcount != 1:
        raise ValueError('completion requires a pending trigger and its own receipt')
