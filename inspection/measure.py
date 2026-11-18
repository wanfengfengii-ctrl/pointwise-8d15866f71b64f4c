from dataclasses import dataclass


@dataclass(frozen=True)
class Measurement:
    raw_um: int
    correction_um: int
    target_um: int
    corrected_um: int
    deviation_um: int


def measure(raw_um: int, correction_um: int, target_um: int) -> Measurement:
    if any(type(value) is not int for value in (raw_um, correction_um, target_um)):
        raise ValueError('measurement inputs must be integer micrometers')
    corrected = raw_um + correction_um
    return Measurement(raw_um, correction_um, target_um, corrected, corrected - target_um)
