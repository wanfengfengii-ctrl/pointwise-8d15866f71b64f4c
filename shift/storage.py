from contextlib import contextmanager
from decimal import Decimal
from sqlite3 import Connection
from typing import Iterator


_MAX_ROW_ID = (1 << 63) - 1


@contextmanager
def write_transaction(db: Connection) -> Iterator[None]:
    """Hold one SQLite writer transaction across the business read and write."""
    db.execute('BEGIN IMMEDIATE')
    try:
        yield
        db.commit()
    except BaseException:
        if db.in_transaction:
            db.rollback()
        raise


def encode_integer(value: int) -> str:
    if type(value) is not int:
        raise ValueError('expected an integer')
    # Decimal converts the Python integer exactly, including very long inputs.
    return format(Decimal(value), 'f')


def decode_integer(text: str) -> int:
    if not isinstance(text, str):
        raise ValueError('expected canonical integer text')
    digits = text[1:] if text.startswith('-') else text
    if not digits.isascii() or not digits.isdecimal():
        raise ValueError('invalid integer text')
    if digits.startswith('0') and text != '0':
        raise ValueError('noncanonical integer text')
    return int(Decimal(text))


def validate_record_id(value: int, kind: str) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f'{kind} number must be a positive integer')
    if value > _MAX_ROW_ID:
        raise LookupError(f'unknown {kind} number')
