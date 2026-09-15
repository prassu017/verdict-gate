"""Example requests with expected verdicts. Shared by pytest and the console's Test run tab."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .policy import CURRENT_POLICY

CASES_DIR = Path(__file__).resolve().parent / "cases"


@dataclass(frozen=True)
class Case:
    id: str
    category: str
    title: str
    expected_verdict: str
    request: Any
    policy_version: str = CURRENT_POLICY.version
    expected_reason_code: str | None = None
    same_decision_as: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_cases(directory: Path = CASES_DIR) -> list[Case]:
    return [Case(**json.loads(path.read_text(encoding="utf-8"))) for path in sorted(directory.glob("*.json"))]
