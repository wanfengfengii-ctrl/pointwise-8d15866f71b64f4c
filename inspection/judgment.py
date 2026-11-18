from enum import Enum


class Verdict(str, Enum):
    PASS = 'pass'
    OUTSIDE = 'outside'


def judge(deviation_um: int, tolerance_um: int) -> Verdict:
    if type(deviation_um) is not int or type(tolerance_um) is not int or tolerance_um < 0:
        raise ValueError('invalid deviation or tolerance')
    if abs(deviation_um) <= tolerance_um:
        return Verdict.PASS
    return Verdict.OUTSIDE
