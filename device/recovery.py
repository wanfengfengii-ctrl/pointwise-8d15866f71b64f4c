from __future__ import annotations

from dataclasses import dataclass
from sqlite3 import Connection

from device.recovery_port import Evidence, Exposure
from shift.storage import decode_integer, encode_integer


@dataclass(frozen=True)
class PendingBeat:
    """A dispatched beat awaiting confirmation, with its device baseline."""
    shift_id: str
    beat: int
    ticket_id: str
    probe: str
    generation: str
    sequence: int


@dataclass(frozen=True)
class ConfirmedBeat:
    """The stored outcome of a confirmed beat, used for the single-beat replay."""
    trigger_id: int | None
    evidence: Evidence | None


def create_recovery_schema(db: Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS recovery_bindings (
            shift_id TEXT PRIMARY KEY NOT NULL REFERENCES calendars(shift_id),
            device_id TEXT NOT NULL
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS recovery_pending (
            shift_id TEXT PRIMARY KEY NOT NULL REFERENCES calendars(shift_id),
            beat TEXT NOT NULL,
            ticket_id TEXT NOT NULL REFERENCES tickets(ticket_id),
            probe TEXT NOT NULL,
            generation TEXT NOT NULL,
            sequence TEXT NOT NULL
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS recovery_confirmed (
            shift_id TEXT NOT NULL REFERENCES calendars(shift_id),
            beat TEXT NOT NULL,
            trigger_id INTEGER REFERENCES triggers(trigger_id),
            generation TEXT,
            sequence TEXT,
            latest_shift_id TEXT,
            latest_beat TEXT,
            latest_probe TEXT,
            PRIMARY KEY (shift_id, beat)
        )
    ''')


def save_binding(db: Connection, shift_id: str, device_id: str) -> None:
    db.execute(
        'INSERT INTO recovery_bindings (shift_id, device_id) VALUES (?, ?)',
        (shift_id, device_id),
    )


def load_binding(db: Connection, shift_id: str) -> str | None:
    row = db.execute(
        'SELECT device_id FROM recovery_bindings WHERE shift_id = ?', (shift_id,)
    ).fetchone()
    return None if row is None else row[0]


def insert_pending(db: Connection, pending: PendingBeat) -> None:
    db.execute(
        '''INSERT INTO recovery_pending (shift_id, beat, ticket_id, probe, generation, sequence)
           VALUES (?, ?, ?, ?, ?, ?)''',
        (
            pending.shift_id,
            encode_integer(pending.beat),
            pending.ticket_id,
            pending.probe,
            pending.generation,
            encode_integer(pending.sequence),
        ),
    )


def load_pending(db: Connection, shift_id: str) -> PendingBeat | None:
    row = db.execute(
        '''SELECT beat, ticket_id, probe, generation, sequence
           FROM recovery_pending WHERE shift_id = ?''',
        (shift_id,),
    ).fetchone()
    if row is None:
        return None
    beat, ticket_id, probe, generation, sequence = row
    return PendingBeat(
        shift_id, decode_integer(beat), ticket_id, probe, generation, decode_integer(sequence)
    )


def delete_pending(db: Connection, shift_id: str) -> None:
    db.execute('DELETE FROM recovery_pending WHERE shift_id = ?', (shift_id,))


def insert_confirmed(
    db: Connection,
    shift_id: str,
    beat: int,
    trigger_id: int | None,
    evidence: Evidence | None,
) -> None:
    if evidence is None or evidence.latest is None:
        latest = (None, None, None)
    else:
        latest = (
            evidence.latest.shift_id,
            encode_integer(evidence.latest.beat),
            evidence.latest.probe,
        )
    db.execute(
        '''INSERT INTO recovery_confirmed
               (shift_id, beat, trigger_id, generation, sequence,
                latest_shift_id, latest_beat, latest_probe)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
        (
            shift_id,
            encode_integer(beat),
            trigger_id,
            None if evidence is None else evidence.generation,
            None if evidence is None else encode_integer(evidence.sequence),
            *latest,
        ),
    )


def load_confirmed(db: Connection, shift_id: str, beat: int) -> ConfirmedBeat | None:
    row = db.execute(
        '''SELECT trigger_id, generation, sequence, latest_shift_id, latest_beat, latest_probe
           FROM recovery_confirmed WHERE shift_id = ? AND beat = ?''',
        (shift_id, encode_integer(beat)),
    ).fetchone()
    if row is None:
        return None
    trigger_id, generation, sequence, latest_shift, latest_beat, latest_probe = row
    if trigger_id is None:
        return ConfirmedBeat(None, None)
    latest = None
    if latest_shift is not None:
        latest = Exposure(latest_shift, decode_integer(latest_beat), latest_probe)
    return ConfirmedBeat(trigger_id, Evidence(generation, decode_integer(sequence), latest))
