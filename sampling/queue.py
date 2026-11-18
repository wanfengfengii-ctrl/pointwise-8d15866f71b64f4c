from __future__ import annotations

from dataclasses import dataclass
from sqlite3 import Connection

from sampling.bookings import list_shift_bookings, load_beat_booking
from sampling.tickets import Ticket


@dataclass(frozen=True)
class DueTask:
    shift_id: str
    ticket_id: str
    beat: int
    probe: str


def due_task(db: Connection, shift_id: str, beat: int) -> DueTask | None:
    booking = load_beat_booking(db, shift_id, beat)
    if booking is None:
        return None
    return DueTask(booking.shift_id, booking.ticket_id, booking.beat, booking.probe)


def shift_bookings(db: Connection, shift_id: str) -> tuple[Ticket, ...]:
    """Provide the original booking facts, including work without a trigger."""
    return list_shift_bookings(db, shift_id)
