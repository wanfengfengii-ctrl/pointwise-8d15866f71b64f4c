from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    trigger_id: int
    shift_id: str
    ticket_id: str
    probe: str
    beat: int


def device_payload(command: Command) -> dict[str, int | str]:
    return {
        'trigger_id': command.trigger_id,
        'shift_id': command.shift_id,
        'ticket_id': command.ticket_id,
        'probe': command.probe,
        'beat': command.beat,
    }
