from __future__ import annotations

from sqlite3 import Connection

from sampling.tickets import Ticket
from shift.storage import decode_integer, encode_integer


def create_booking_schema(db: Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS tickets (
            ticket_id TEXT PRIMARY KEY NOT NULL,
            shift_id TEXT NOT NULL,
            beat TEXT NOT NULL,
            probe TEXT NOT NULL,
            target TEXT NOT NULL,
            UNIQUE (shift_id, beat),
            FOREIGN KEY (shift_id, beat, probe)
                REFERENCES calendar_slot_probes(shift_id, beat, probe)
        )
    ''')


def reserve_beat(db: Connection, ticket: Ticket) -> None:
    inserted = db.execute(
        '''INSERT INTO tickets (shift_id, ticket_id, beat, probe, target)
           VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING''',
        (
            ticket.shift_id,
            ticket.ticket_id,
            encode_integer(ticket.beat),
            ticket.probe,
            encode_integer(ticket.target_um),
        ),
    )
    if inserted.rowcount != 1:
        raise ValueError('ticket number or shift beat is already reserved')


def _restore_ticket(row: tuple[str, str, str, str, str]) -> Ticket:
    shift_id, ticket_id, beat, probe, target = row
    return Ticket(shift_id, ticket_id, decode_integer(beat), probe, decode_integer(target))


def load_ticket(db: Connection, ticket_id: str) -> Ticket:
    row = db.execute(
        'SELECT shift_id, ticket_id, beat, probe, target FROM tickets WHERE ticket_id = ?',
        (ticket_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f'unknown ticket {ticket_id!r}')
    return _restore_ticket(row)


def load_beat_booking(db: Connection, shift_id: str, beat: int) -> Ticket | None:
    row = db.execute(
        '''SELECT shift_id, ticket_id, beat, probe, target
           FROM tickets WHERE shift_id = ? AND beat = ?''',
        (shift_id, encode_integer(beat)),
    ).fetchone()
    return None if row is None else _restore_ticket(row)


def list_shift_bookings(db: Connection, shift_id: str) -> tuple[Ticket, ...]:
    rows = db.execute(
        'SELECT shift_id, ticket_id, beat, probe, target FROM tickets WHERE shift_id = ?',
        (shift_id,),
    )
    return tuple(_restore_ticket(row) for row in rows)
