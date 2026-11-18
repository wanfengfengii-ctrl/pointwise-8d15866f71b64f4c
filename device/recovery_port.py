from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Exposure:
    shift_id: str
    beat: int
    probe: str


@dataclass(frozen=True)
class Evidence:
    generation: str
    sequence: int
    latest: Exposure | None


class ReplyLost(TimeoutError):
    """The caller did not learn whether the exposure ran."""


class FenceLost(RuntimeError):
    """This device lease can no longer run commands."""


class RecoveryPort(Protocol):
    @property
    def device_id(self) -> str: ...

    def takeover(self) -> int: ...

    def observe(self, lease: int) -> Evidence: ...

    def expose(self, lease: int, exposure: Exposure) -> Evidence: ...
