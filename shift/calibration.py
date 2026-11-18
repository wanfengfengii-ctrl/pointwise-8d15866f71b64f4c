from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Calibration:
    correction_um: int
    tolerance_um: int


def normalize_calibrations(
    calibrations: Mapping[str, Calibration],
) -> tuple[tuple[str, Calibration], ...]:
    if not isinstance(calibrations, Mapping):
        raise ValueError('calibrations must map probe names to calibration values')
    for name, value in calibrations.items():
        if not isinstance(name, str) or not name:
            raise ValueError('probe name must be a nonempty string')
        if not isinstance(value, Calibration):
            raise ValueError(f'expected Calibration for probe {name!r}')
        if type(value.correction_um) is not int or type(value.tolerance_um) is not int:
            raise ValueError('correction and tolerance must be integer micrometers')
        if value.tolerance_um < 0:
            raise ValueError('tolerance must be nonnegative')
    return tuple(sorted(calibrations.items()))
