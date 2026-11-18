from sqlite3 import Connection

from device.commands import Command
from inspection.judgment import judge
from inspection.measure import measure
from inspection.receipts import Receipt, insert_receipt
from sampling.bookings import load_ticket
from shift.calendar import load_calendar


def issue_receipt(db: Connection, command: Command, raw_um: int) -> Receipt:
    ticket = load_ticket(db, command.ticket_id)
    calendar = load_calendar(db, command.shift_id)
    calibration = calendar.calibration_for(command.probe)
    measurement = measure(raw_um, calibration.correction_um, ticket.target_um)
    verdict = judge(measurement.deviation_um, calibration.tolerance_um)
    return insert_receipt(db, command, measurement, calibration.tolerance_um, verdict)
