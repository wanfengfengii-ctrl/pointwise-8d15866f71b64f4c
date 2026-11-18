from dataclasses import dataclass


@dataclass(frozen=True)
class Ticket:
    shift_id: str
    ticket_id: str
    beat: int
    probe: str
    target_um: int


def build_ticket(
    shift_id: str, ticket_id: str, beat: int, probe: str, target_um: int
) -> Ticket:
    for label, name in (('shift', shift_id), ('ticket', ticket_id), ('probe', probe)):
        if not isinstance(name, str) or not name:
            raise ValueError(f'{label} must be a nonempty string')
    if type(beat) is not int or beat <= 0:
        raise ValueError('beat must be a positive integer')
    if type(target_um) is not int:
        raise ValueError('target must be integer micrometers')
    return Ticket(shift_id, ticket_id, beat, probe, target_um)
