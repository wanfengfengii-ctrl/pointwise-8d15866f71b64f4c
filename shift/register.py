from collections.abc import Iterable, Mapping
from sqlite3 import Connection

from shift.calendar import build_calendar, insert_calendar, load_calendar
from shift.calibration import Calibration
from shift.storage import write_transaction


def register(
    db: Connection,
    shift_id: str,
    slots: Mapping[int, Iterable[str]],
    calibrations: Mapping[str, Calibration],
) -> str:
    proposed = build_calendar(shift_id, slots, calibrations)
    with write_transaction(db):
        try:
            published = load_calendar(db, shift_id)
        except LookupError:
            insert_calendar(db, proposed)
        else:
            if published != proposed:
                raise ValueError(f'shift {shift_id!r} already has a different calendar')
    return shift_id
