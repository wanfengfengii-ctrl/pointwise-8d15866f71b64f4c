from sqlite3 import Connection

from device.journal import complete_trigger, load_trigger
from inspection.issue import issue_receipt
from inspection.receipts import Receipt, receipt
from shift.storage import write_transaction


def receive(db: Connection, trigger_id: int, raw_um: int) -> Receipt:
    if type(raw_um) is not int:
        raise ValueError('reading must be integer micrometers')
    with write_transaction(db):
        trigger = load_trigger(db, trigger_id)
        if trigger.receipt_id is None:
            result = issue_receipt(db, trigger.command, raw_um)
            complete_trigger(db, trigger.command.trigger_id, result.receipt_id)
        else:
            result = receipt(db, trigger.receipt_id)
            if raw_um != result.measurement.raw_um:
                raise ValueError('reading conflicts with the signed receipt')
    return result
