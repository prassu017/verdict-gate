"""Shared value types: verdicts, check statuses and check results."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Verdict(StrEnum):
    ADMIT = "ADMIT"
    HALT = "HALT"
    DEFER = "DEFER"
    REFER = "REFER"


class Status(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"  # evidence missing, unreadable or stale
    NOT_APPLICABLE = "N/A"


@dataclass(frozen=True)
class CheckResult:
    check: str
    status: Status
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"check": self.check, "status": self.status.value, "detail": self.detail}
