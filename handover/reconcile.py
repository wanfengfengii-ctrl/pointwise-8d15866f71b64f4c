from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from sqlite3 import Connection

from device.journal import Trigger, list_shift_triggers
from inspection.judgment import Verdict
from inspection.receipts import receipt
from sampling.queue import shift_bookings
from sampling.tickets import Ticket


class HandoverStatus(str, Enum):
    UNTRIGGERED = 'untriggered'
    PENDING = 'pending'
    PASSED = 'passed'
    OUTSIDE = 'outside'


@dataclass(frozen=True)
class HandoverRow:
    ticket_id: str
    beat: int
    probe: str
    status: HandoverStatus
    trigger_id: int | None = None
    receipt_id: int | None = None
    raw_um: int | None = None
    corrected_um: int | None = None
    deviation_um: int | None = None


def _reconcile_ticket(db: Connection, ticket: Ticket, trigger: Trigger | None) -> HandoverRow:
    if trigger is None:
        return HandoverRow(ticket.ticket_id, ticket.beat, ticket.probe, HandoverStatus.UNTRIGGERED)
    trigger_id = trigger.command.trigger_id
    if trigger.receipt_id is None:
        return HandoverRow(
            ticket.ticket_id, ticket.beat, ticket.probe, HandoverStatus.PENDING,
            trigger_id=trigger_id,
        )
    result = receipt(db, trigger.receipt_id)
    status = HandoverStatus.PASSED if result.verdict == Verdict.PASS else HandoverStatus.OUTSIDE
    return HandoverRow(
        ticket.ticket_id, ticket.beat, ticket.probe, status,
        trigger_id=trigger_id, receipt_id=result.receipt_id,
        raw_um=result.measurement.raw_um, corrected_um=result.measurement.corrected_um,
        deviation_um=result.measurement.deviation_um,
    )


def reconcile_shift(db: Connection, shift_id: str) -> tuple[HandoverRow, ...]:
    """Project the fixed bookings and their capture outcomes in the caller's transaction."""
    bookings = sorted(shift_bookings(db, shift_id), key=lambda ticket: ticket.beat)
    triggers = {item.command.ticket_id: item for item in list_shift_triggers(db, shift_id)}
    return tuple(
        _reconcile_ticket(db, ticket, triggers.get(ticket.ticket_id)) for ticket in bookings
    )
