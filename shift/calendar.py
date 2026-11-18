from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from sqlite3 import Connection

from shift.calibration import Calibration, normalize_calibrations
from shift.probes import normalize_slot_probes
from shift.storage import decode_integer, encode_integer


@dataclass(frozen=True)
class Calendar:
    shift_id: str
    slots: tuple[tuple[int, tuple[str, ...]], ...]
    calibrations: tuple[tuple[str, Calibration], ...]

    def allows_probe(self, beat: int, probe: str) -> bool:
        for configured_beat, allowed in self.slots:
            if configured_beat == beat:
                return probe in allowed
        return False

    def next_beat_after(self, cursor: int) -> int | None:
        for beat, _ in self.slots:
            if beat > cursor:
                return beat
        return None

    def calibration_for(self, probe: str) -> Calibration:
        for name, calibration in self.calibrations:
            if name == probe:
                return calibration
        raise LookupError(f'unknown probe {probe!r} in shift {self.shift_id!r}')


def build_calendar(
    shift_id: str,
    slots: Mapping[int, Iterable[str]],
    calibrations: Mapping[str, Calibration],
) -> Calendar:
    _validate_shift_id(shift_id)
    if not isinstance(slots, Mapping):
        raise ValueError('slots must map beats to probe collections')
    normalized_calibrations = normalize_calibrations(calibrations)
    configured = {name for name, _ in normalized_calibrations}
    normalized_slots = []
    for beat, probes in slots.items():
        if type(beat) is not int or beat <= 0:
            raise ValueError('beat must be a positive integer')
        normalized_slots.append((beat, normalize_slot_probes(probes, configured)))
    return Calendar(shift_id, tuple(sorted(normalized_slots)), normalized_calibrations)


def _validate_shift_id(shift_id: str) -> None:
    if not isinstance(shift_id, str) or not shift_id:
        raise ValueError('shift number must be a nonempty string')


def create_calendar_schema(db: Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS calendars (
            shift_id TEXT PRIMARY KEY NOT NULL
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS calendar_calibrations (
            shift_id TEXT NOT NULL REFERENCES calendars(shift_id),
            probe TEXT NOT NULL,
            correction TEXT NOT NULL,
            tolerance TEXT NOT NULL,
            PRIMARY KEY (shift_id, probe)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS calendar_slots (
            shift_id TEXT NOT NULL REFERENCES calendars(shift_id),
            beat TEXT NOT NULL,
            PRIMARY KEY (shift_id, beat)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS calendar_slot_probes (
            shift_id TEXT NOT NULL,
            beat TEXT NOT NULL,
            probe TEXT NOT NULL,
            PRIMARY KEY (shift_id, beat, probe),
            FOREIGN KEY (shift_id, beat) REFERENCES calendar_slots(shift_id, beat),
            FOREIGN KEY (shift_id, probe) REFERENCES calendar_calibrations(shift_id, probe)
        )
    ''')


def insert_calendar(db: Connection, calendar: Calendar) -> None:
    shift_id = calendar.shift_id
    db.execute('INSERT INTO calendars (shift_id) VALUES (?)', (shift_id,))
    db.executemany(
        '''INSERT INTO calendar_calibrations (shift_id, probe, correction, tolerance)
           VALUES (?, ?, ?, ?)''',
        (
            (shift_id, name, encode_integer(cal.correction_um), encode_integer(cal.tolerance_um))
            for name, cal in calendar.calibrations
        ),
    )
    for beat, probes in calendar.slots:
        stored = encode_integer(beat)
        db.execute(
            'INSERT INTO calendar_slots (shift_id, beat) VALUES (?, ?)', (shift_id, stored)
        )
        db.executemany(
            'INSERT INTO calendar_slot_probes (shift_id, beat, probe) VALUES (?, ?, ?)',
            ((shift_id, stored, probe) for probe in probes),
        )


def load_calendar(db: Connection, shift_id: str) -> Calendar:
    _validate_shift_id(shift_id)
    if db.execute('SELECT 1 FROM calendars WHERE shift_id = ?', (shift_id,)).fetchone() is None:
        raise LookupError(f'unknown shift {shift_id!r}')
    calibrations = {
        name: Calibration(decode_integer(correction), decode_integer(tolerance))
        for name, correction, tolerance in db.execute(
            'SELECT probe, correction, tolerance FROM calendar_calibrations WHERE shift_id = ?',
            (shift_id,),
        )
    }
    slots: dict[int, list[str]] = {}
    for stored_beat, name in db.execute(
        '''SELECT slot.beat, allowed.probe
           FROM calendar_slots AS slot
           LEFT JOIN calendar_slot_probes AS allowed
              ON allowed.shift_id = slot.shift_id AND allowed.beat = slot.beat
           WHERE slot.shift_id = ?''',
        (shift_id,),
    ):
        allowed = slots.setdefault(decode_integer(stored_beat), [])
        if name is not None:
            allowed.append(name)
    return build_calendar(shift_id, slots, calibrations)
