from sqlite3 import Connection

from device.journal import read_cursor
from sampling.bookings import reserve_beat
from sampling.tickets import build_ticket
from shift.calendar import load_calendar
from shift.storage import write_transaction


def book(
    db: Connection, shift_id: str, ticket_id: str, beat: int, probe: str, target_um: int
) -> str:
    ticket = build_ticket(shift_id, ticket_id, beat, probe, target_um)
    with write_transaction(db):
        calendar = load_calendar(db, ticket.shift_id)
        if ticket.beat <= read_cursor(db, ticket.shift_id):
            raise ValueError('the requested beat has already passed')
        if not calendar.allows_probe(ticket.beat, ticket.probe):
            raise ValueError('the probe is not permitted at the requested beat')
        reserve_beat(db, ticket)
    return ticket.ticket_id
