from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from handover.reconcile import HandoverRow, HandoverStatus


@dataclass(frozen=True)
class ReportTotals:
    untriggered: int
    pending: int
    passed: int
    outside: int


def count_statuses(rows: Iterable[HandoverRow]) -> ReportTotals:
    counts = Counter(row.status for row in rows)
    return ReportTotals(
        untriggered=counts[HandoverStatus.UNTRIGGERED],
        pending=counts[HandoverStatus.PENDING],
        passed=counts[HandoverStatus.PASSED],
        outside=counts[HandoverStatus.OUTSIDE],
    )
