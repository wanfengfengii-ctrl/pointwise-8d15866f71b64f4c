from __future__ import annotations

from dataclasses import dataclass
from sqlite3 import Connection

from device.commands import Command
from device.dispatch import dispatch_due
from device.journal import advance_cursor, load_trigger, read_cursor, record_trigger
from device.recovery import (
    PendingBeat,
    delete_pending,
    insert_confirmed,
    insert_pending,
    load_binding,
    load_confirmed,
    load_pending,
    save_binding,
)
from device.recovery_port import Evidence, Exposure, RecoveryPort
from sampling.bookings import load_beat_booking
from shift.calendar import load_calendar
from shift.storage import write_transaction


@dataclass(frozen=True)
class Confirmed:
    """A dispatched beat whose proof, trigger and cursor were fixed in one commit."""
    command: Command
    evidence: Evidence


@dataclass(frozen=True)
class Paused:
    """The pending beat does not match the observed evidence; later beats wait."""
    evidence: Evidence


def tick(db: Connection, shift_id: str, beat: int) -> Command | None:
    if type(beat) is not int or beat <= 0:
        raise ValueError('beat must be a positive integer')
    with write_transaction(db):
        calendar = load_calendar(db, shift_id)
        if load_binding(db, shift_id) is not None:
            raise ValueError('shift is enabled for recovery; use advance')
        next_beat = calendar.next_beat_after(read_cursor(db, shift_id))
        if next_beat is None:
            raise ValueError('the shift has no more scheduled beats')
        if beat != next_beat:
            raise ValueError('beat is out of order')
        command = dispatch_due(db, shift_id, beat)
        advance_cursor(db, shift_id, beat)
    return command


def enable_recovery(db: Connection, shift_id: str, device_id: str) -> str:
    if not isinstance(device_id, str) or not device_id:
        raise ValueError('device id must be a nonempty string')
    with write_transaction(db):
        load_calendar(db, shift_id)
        bound = load_binding(db, shift_id)
        if bound is not None:
            if bound != device_id:
                raise ValueError('shift is already bound to a different device')
            return shift_id
        if read_cursor(db, shift_id) != 0:
            raise ValueError('cannot enable recovery after the first beat')
        save_binding(db, shift_id, device_id)
    return shift_id


def advance(
    db: Connection, shift_id: str, beat: int, device: RecoveryPort
) -> Confirmed | Paused | None:
    if type(beat) is not int or beat <= 0:
        raise ValueError('beat must be a positive integer')
    with write_transaction(db):
        calendar = load_calendar(db, shift_id)
        bound = load_binding(db, shift_id)
        if bound is None:
            raise ValueError('shift is not enabled for recovery')
        if getattr(device, 'device_id', None) != bound:
            raise ValueError('device does not match the shift binding')
        cursor = read_cursor(db, shift_id)
        if beat == cursor:
            return _replay(db, shift_id, beat)
        pending = load_pending(db, shift_id)
        if pending is not None:
            if beat != pending.beat:
                raise ValueError('beat is out of order')
            booking = None
        else:
            next_beat = calendar.next_beat_after(cursor)
            if next_beat is None:
                raise ValueError('the shift has no more scheduled beats')
            if beat != next_beat:
                raise ValueError('beat is out of order')
            booking = load_beat_booking(db, shift_id, beat)
            if booking is None:
                advance_cursor(db, shift_id, beat)
                insert_confirmed(db, shift_id, beat, None, None)
                return None
    if pending is not None:
        return _recover_pending(db, pending, device)
    return _dispatch_booking(db, shift_id, booking, device)


def _replay(db: Connection, shift_id: str, beat: int) -> Confirmed | None:
    record = load_confirmed(db, shift_id, beat)
    if record is None:
        raise ValueError('beat is out of order')
    if record.trigger_id is None:
        return None
    return Confirmed(load_trigger(db, record.trigger_id).command, record.evidence)


def _dispatch_booking(db: Connection, shift_id: str, booking, device: RecoveryPort) -> Confirmed:
    lease = device.takeover()
    baseline = device.observe(lease)
    pending = PendingBeat(
        shift_id, booking.beat, booking.ticket_id, booking.probe,
        baseline.generation, baseline.sequence,
    )
    with write_transaction(db):
        if load_pending(db, shift_id) is not None or read_cursor(db, shift_id) >= booking.beat:
            raise ValueError('beat is out of order')
        insert_pending(db, pending)
    proof = device.expose(lease, Exposure(shift_id, booking.beat, booking.probe))
    _check_proof(pending, proof)
    return _confirm(db, pending, proof)


def _recover_pending(
    db: Connection, pending: PendingBeat, device: RecoveryPort
) -> Confirmed | Paused:
    lease = device.takeover()
    evidence = device.observe(lease)
    expected = Exposure(pending.shift_id, pending.beat, pending.probe)
    if evidence.generation == pending.generation:
        if evidence.sequence == pending.sequence:
            proof = device.expose(lease, expected)
            _check_proof(pending, proof)
            return _confirm(db, pending, proof)
        if evidence.sequence == pending.sequence + 1 and evidence.latest == expected:
            return _confirm(db, pending, evidence)
    return Paused(evidence)


def _check_proof(pending: PendingBeat, proof: Evidence) -> None:
    expected = Exposure(pending.shift_id, pending.beat, pending.probe)
    if (
        proof.generation != pending.generation
        or proof.sequence != pending.sequence + 1
        or proof.latest != expected
    ):
        raise ValueError('device proof does not follow the pending baseline')


def _confirm(db: Connection, pending: PendingBeat, proof: Evidence) -> Confirmed:
    with write_transaction(db):
        if load_pending(db, pending.shift_id) != pending:
            raise ValueError('the pending beat changed during dispatch')
        command = record_trigger(
            db, pending.shift_id, pending.ticket_id, pending.probe, pending.beat
        )
        advance_cursor(db, pending.shift_id, pending.beat)
        insert_confirmed(db, pending.shift_id, pending.beat, command.trigger_id, proof)
        delete_pending(db, pending.shift_id)
    return Confirmed(command, proof)
