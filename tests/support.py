from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from device.clock import tick
from device.commands import Command, device_payload
from device.receive import receive
from inspection.receipts import Receipt
from shift.bootstrap import open_workspace
from shift.calibration import Calibration
from shift.register import register


class ScriptedDevice:
    def __init__(self) -> None:
        self.commands: dict[int, dict[str, int | str]] = {}

    def advance(self, db: sqlite3.Connection, shift_id: str, beat: int) -> Command | None:
        command = tick(db, shift_id, beat)
        if command is not None:
            if command.trigger_id in self.commands:
                raise AssertionError('device received a duplicate trigger number')
            self.commands[command.trigger_id] = device_payload(command)
        return command

    def release(self, db: sqlite3.Connection, trigger_id: int, raw_um: int) -> Receipt:
        if trigger_id not in self.commands:
            raise AssertionError('device cannot reply to an unseen trigger')
        return receive(db, trigger_id, raw_um)


class WorkspaceTest(unittest.TestCase):
    def setUp(self) -> None:
        workspace = TemporaryDirectory(prefix='fixed-sampling-test-')
        self.addCleanup(workspace.cleanup)
        self.path = Path(workspace.name) / 'lab.sqlite3'
        self.db = open_workspace(self.path)
        self.addCleanup(self.close_workspace)
        self.device = ScriptedDevice()

    def close_workspace(self) -> None:
        self.db.close()

    def reopen(self) -> None:
        self.close_workspace()
        self.db = open_workspace(self.path)

    def register_day(self, slots) -> str:
        return register(self.db, 'day', slots, {'P': Calibration(2, 3), 'Q': Calibration(-1, 2)})


@contextmanager
def deny_commit(db: sqlite3.Connection):
    """Use SQLite's authorizer to make the real COMMIT statement fail."""
    def authorize(action, operation, *_):
        if action == sqlite3.SQLITE_TRANSACTION and operation == 'COMMIT':
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    db.set_authorizer(authorize)
    try:
        yield
    finally:
        db.set_authorizer(lambda *_: sqlite3.SQLITE_OK)
