"""One function per rule. Every check runs and reports PASS, FAIL, UNKNOWN or N/A with a reason."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from .canonical import format_decimal, parse_timestamp, to_decimal
from .model import CheckResult, Status
from .policy import Policy


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"


MISSING: Any = _Missing()

# Checks whose UNKNOWN status means "not enough evidence to decide".
EVIDENCE_CHECKS = ("P3", "P4", "P5", "P2")


@dataclass(frozen=True)
class RequestView:
    """The fields the rules read, pulled out of an untrusted request exactly once."""

    request_id: Any
    action_type: Any
    role: Any
    raw_amount: Any
    currency: Any
    destination: Any
    raw_balance: Any
    vendor_status: Any
    raw_checked_at: Any
    raw_evaluated_at: Any

    @property
    def amount(self) -> Decimal | None:
        return to_decimal(self.raw_amount)

    @property
    def balance(self) -> Decimal | None:
        return to_decimal(self.raw_balance)

    @property
    def checked_at(self) -> datetime | None:
        return parse_timestamp(self.raw_checked_at)

    @property
    def evaluated_at(self) -> datetime | None:
        return parse_timestamp(self.raw_evaluated_at)


def read_request(request: Any) -> RequestView:
    root = request if isinstance(request, Mapping) else {}
    actor, action, evidence = (_section(root, key) for key in ("actor", "action", "evidence"))
    return RequestView(
        request_id=root.get("request_id", MISSING),
        action_type=action.get("type", MISSING),
        role=actor.get("role", MISSING),
        raw_amount=action.get("amount", MISSING),
        currency=action.get("currency", MISSING),
        destination=action.get("destination", MISSING),
        raw_balance=evidence.get("account_balance", MISSING),
        vendor_status=evidence.get("vendor_status", MISSING),
        raw_checked_at=evidence.get("vendor_status_checked_at", MISSING),
        raw_evaluated_at=root.get("evaluated_at", MISSING),
    )


def _section(root: Mapping, key: str) -> Mapping:
    value = root.get(key)
    return value if isinstance(value, Mapping) else {}


def usd(amount: Decimal) -> str:
    text = f"{amount:,.2f}"
    return "$" + (text[:-3] if text.endswith(".00") else text)


def duration(seconds: float) -> str:
    total = round(abs(seconds))
    return f"{total // 60}m{total % 60:02d}s"


def shown(value: Any) -> str:
    return "nothing" if value is MISSING else json.dumps(value, default=str)


def _result(check: str, status: Status, detail: str) -> CheckResult:
    return CheckResult(check, status, detail)


def action_covered(view: RequestView, policy: Policy) -> CheckResult:
    """P6: any action type not covered by the supplied policy cannot be assumed permissible."""
    kind = view.action_type
    if not isinstance(kind, str) or not kind:
        return _result("P6", Status.FAIL, f"action.type must be a non-empty string (got {shown(kind)})")
    if kind not in policy.allowed_roles:
        return _result("P6", Status.FAIL, f"{kind} is not covered by the supplied policy")
    return _result("P6", Status.PASS, f"{kind} is covered by the policy")


def role_allowed(view: RequestView, policy: Policy) -> CheckResult:
    """P1: a finance_agent may propose a transfer_funds action."""
    role = view.role
    if not isinstance(role, str) or not role:
        return _result("P1", Status.FAIL, f"actor.role must be a non-empty string (got {shown(role)})")
    if role not in policy.allowed_roles[view.action_type]:
        return _result("P1", Status.FAIL, f"{role} may not propose {view.action_type}")
    return _result("P1", Status.PASS, f"{role} may propose {view.action_type}")


def request_well_formed(view: RequestView) -> CheckResult:
    """REQ (assumption): a request needs an id to be auditable and a positive numeric amount."""
    if not isinstance(view.request_id, str) or not view.request_id.strip():
        return _result("REQ", Status.FAIL, f"request_id must be a non-empty string (got {shown(view.request_id)})")
    amount = view.amount
    if amount is None:
        return _result("REQ", Status.FAIL, f"action.amount must be a number (got {shown(view.raw_amount)})")
    if amount <= 0:
        return _result("REQ", Status.FAIL, f"action.amount must be positive (got {format_decimal(amount)})")
    return _result("REQ", Status.PASS, f"request_id present and amount {usd(amount)} is positive")


def evidence_fresh(view: RequestView, policy: Policy) -> CheckResult:
    """P4: vendor-status evidence older than the window is not sufficient for a determination."""
    limit = policy.max_evidence_age_seconds
    evaluated_at, checked_at = view.evaluated_at, view.checked_at
    if evaluated_at is None:
        return _result("P4", Status.UNKNOWN, _timestamp_problem("evaluated_at", view.raw_evaluated_at))
    if checked_at is None:
        return _result("P4", Status.UNKNOWN, _timestamp_problem("vendor_status_checked_at", view.raw_checked_at))
    age = (evaluated_at - checked_at).total_seconds()
    if age < 0:
        return _result("P4", Status.UNKNOWN, f"vendor evidence is timestamped {duration(age)} after evaluated_at")
    if age > limit:
        return _result("P4", Status.UNKNOWN, f"vendor evidence is {duration(age)} old (limit {duration(limit)})")
    return _result("P4", Status.PASS, f"vendor evidence is {duration(age)} old (limit {duration(limit)})")


def _timestamp_problem(field: str, raw: Any) -> str:
    if raw is MISSING:
        return f"{field} is missing"
    return f"{field} is not an ISO-8601 timestamp with a timezone (got {shown(raw)})"


def vendor_approved(view: RequestView, freshness: CheckResult) -> CheckResult:
    """P3: the destination vendor must be approved, judged only on fresh evidence."""
    status = view.vendor_status
    destination = view.destination if isinstance(view.destination, str) else "the destination vendor"
    if status is MISSING or status is None or status == "":
        return _result("P3", Status.UNKNOWN, "vendor_status is missing")
    if freshness.status is not Status.PASS:
        return _result("P3", Status.UNKNOWN, f"vendor_status {shown(status)} can't be relied on because the evidence isn't fresh")
    if status == "approved":
        return _result("P3", Status.PASS, f"{destination} is approved")
    return _result("P3", Status.FAIL, f"{destination} is {shown(status)}, not approved")


def within_balance(view: RequestView) -> CheckResult:
    """P5: a transfer may not exceed the currently supplied account balance."""
    amount = view.amount
    if amount is None or amount <= 0:
        return _result("P5", Status.NOT_APPLICABLE, "not evaluated: amount is invalid")
    if view.raw_balance is MISSING or view.raw_balance is None:
        return _result("P5", Status.UNKNOWN, "account_balance is missing")
    balance = view.balance
    if balance is None:
        return _result("P5", Status.UNKNOWN, f"account_balance is not a number (got {shown(view.raw_balance)})")
    if amount > balance:
        return _result("P5", Status.FAIL, f"{usd(amount)} exceeds the supplied balance of {usd(balance)}")
    return _result("P5", Status.PASS, f"{usd(amount)} is within the balance of {usd(balance)}")


def within_authority(view: RequestView, policy: Policy) -> CheckResult:
    """P2: transfers greater than the limit require another authority."""
    amount = view.amount
    if amount is None or amount <= 0:
        return _result("P2", Status.NOT_APPLICABLE, "not evaluated: amount is invalid")
    limit = usd(policy.refer_above)
    if view.currency != policy.currency:
        return _result("P2", Status.UNKNOWN, f"currency {shown(view.currency)} can't be compared with the {limit} limit without exchange-rate evidence")
    if amount > policy.refer_above:
        return _result("P2", Status.FAIL, f"{usd(amount)} is above this agent's {limit} limit")
    return _result("P2", Status.PASS, f"{usd(amount)} is within the {limit} limit")


def run_checks(view: RequestView, policy: Policy) -> dict[str, CheckResult]:
    """All checks, in display order. Nothing short-circuits except an uncovered action type."""
    covered = action_covered(view, policy)
    if covered.status is Status.FAIL:
        skipped = "not evaluated: the action type is not covered"
        results = {"P6": covered}
        for name in ("P1", "REQ", "P3", "P5", "P2", "P4", "EVIDENCE"):
            results[name] = _result(name, Status.NOT_APPLICABLE, skipped)
        return results

    freshness = evidence_fresh(view, policy)
    results = {
        "P6": covered,
        "P1": role_allowed(view, policy),
        "REQ": request_well_formed(view),
        "P3": vendor_approved(view, freshness),
        "P5": within_balance(view),
        "P2": within_authority(view, policy),
        "P4": freshness,
    }
    lacking = [name for name in EVIDENCE_CHECKS if results[name].status is Status.UNKNOWN]
    results["EVIDENCE"] = (
        _result("EVIDENCE", Status.UNKNOWN, f"insufficient evidence for {', '.join(lacking)}")
        if lacking
        else _result("EVIDENCE", Status.PASS, "all evidence the checks need is present, readable and fresh")
    )
    return results
