"""The decision: checks in, one verdict out, then retry handling around it.

decide()          pure: same canonical inputs + same policy -> same Decision, no clock, no I/O.
DecisionService   adds the retry ledger (identical retry, changed payload, ledger failure).
evaluate()        the assignment's evaluate(request) -> decision, with an in-process ledger.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .canonical import fingerprint, format_timestamp, sha256_hex
from .checks import MISSING, RequestView, read_request, run_checks
from .ledger import InMemoryLedger, Ledger, LedgerRecord
from .model import CheckResult, Status, Verdict
from .policy import CURRENT_POLICY, Policy

# The precedence ladder. The first gate with a triggering check decides.
# Authority limits who may say yes, not who may say no: a determinable HALT beats REFER,
# and REFER beats DEFER because fresh evidence still couldn't let this service ADMIT.
GATES: tuple[tuple[int, tuple[str, ...], Status, Verdict, str], ...] = (
    (1, ("P6", "P1", "REQ"), Status.FAIL, Verdict.HALT, "INVALID_OR_UNCOVERED"),
    (2, ("P3", "P5"), Status.FAIL, Verdict.HALT, "POLICY_PROHIBITED"),
    (3, ("P2",), Status.FAIL, Verdict.REFER, "REQUIRES_OTHER_AUTHORITY"),
    (4, ("P3", "P4", "P5", "P2"), Status.UNKNOWN, Verdict.DEFER, "INSUFFICIENT_EVIDENCE"),
)
ADMIT_GATE = 5


@dataclass(frozen=True, eq=False)
class Decision:
    request_id: Any
    verdict: Verdict
    reason_code: str
    reason: str
    gate: int
    policy_version: str
    policy_hash: str
    evaluated_at: Any
    decision_id: str
    input_hash: str
    checks: tuple[CheckResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "verdict": self.verdict.value,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "decided_at_gate": self.gate,
            "policy_version": self.policy_version,
            "policy_hash": self.policy_hash,
            "evaluated_at": self.evaluated_at,
            "decision_id": self.decision_id,
            "input_hash": self.input_hash,
            "checks": [check.to_dict() for check in self.checks],
        }


def decide(request: Any, policy: Policy = CURRENT_POLICY) -> Decision:
    view = read_request(request)
    checks = run_checks(view, policy)
    verdict, reason_code, gate, decisive = _apply_precedence(checks)
    input_hash = fingerprint(decision_inputs(view))
    request_id = None if view.request_id is MISSING else view.request_id
    return Decision(
        request_id=request_id,
        verdict=verdict,
        reason_code=reason_code,
        reason=_reason(verdict, checks, decisive),
        gate=gate,
        policy_version=policy.version,
        policy_hash=policy.digest,
        evaluated_at=_echo_evaluated_at(view),
        decision_id=_derive_id(fingerprint(request_id), input_hash, policy.digest),
        input_hash=input_hash,
        checks=tuple(checks.values()),
    )


def decision_inputs(view: RequestView) -> dict[str, Any]:
    """Exactly what the rules read. Missing fields are omitted, which differs from null."""
    fields = {
        "action_type": view.action_type,
        "role": view.role,
        "amount": view.raw_amount,
        "currency": view.currency,
        "destination": view.destination,
        "account_balance": view.raw_balance,
        "vendor_status": view.vendor_status,
        "vendor_status_checked_at": view.raw_checked_at,
        "evaluated_at": view.raw_evaluated_at,
    }
    return {name: value for name, value in fields.items() if value is not MISSING}


def _apply_precedence(checks: Mapping[str, CheckResult]) -> tuple[Verdict, str, int, list[CheckResult]]:
    for gate, names, trigger, verdict, reason_code in GATES:
        fired = [checks[name] for name in names if checks[name].status is trigger]
        if fired:
            return verdict, reason_code, gate, fired
    return Verdict.ADMIT, "ALL_CHECKS_PASSED", ADMIT_GATE, [checks[name] for name in ("P1", "P3", "P4", "P5", "P2")]


def _reason(verdict: Verdict, checks: Mapping[str, CheckResult], decisive: list[CheckResult]) -> str:
    text = "; ".join(check.detail for check in decisive)
    if verdict is Verdict.REFER:
        open_questions = [checks[name].detail for name in ("P3", "P4", "P5") if checks[name].status is Status.UNKNOWN]
        if open_questions:
            text += ". Also for the approving authority: " + "; ".join(open_questions)
    elif verdict is Verdict.DEFER:
        text += ". Resubmit with refreshed evidence under a new request_id"
    return text


def _echo_evaluated_at(view: RequestView) -> Any:
    if view.evaluated_at is not None:
        return format_timestamp(view.evaluated_at)
    return None if view.raw_evaluated_at is MISSING else view.raw_evaluated_at


def _derive_id(*parts: str) -> str:
    return "dec_" + sha256_hex("|".join(parts))[:24]


class DecisionService:
    """decide() plus the retry ledger. Fails closed if the ledger can't be read or written."""

    def __init__(self, ledger: Ledger | None = None, policy: Policy = CURRENT_POLICY) -> None:
        self.ledger: Ledger = ledger if ledger is not None else InMemoryLedger()
        self.policy = policy

    def evaluate(self, request: Any, *, policy: Policy | None = None) -> dict[str, Any]:
        active = policy or self.policy
        decision = {**decide(request, active).to_dict(), "replayed": False}
        request_id = request.get("request_id") if isinstance(request, Mapping) else None
        if not isinstance(request_id, str) or not request_id.strip():
            return decision  # nothing to key a retry on; gate 1 has already halted it

        payload = fingerprint(request)
        fresh = LedgerRecord(request_id, payload, copy.deepcopy(decision))  # the caller keeps `decision`
        try:
            existing = self.ledger.get(request_id)
            if existing is None:
                stored = self.ledger.put_if_absent(fresh)
                if stored.payload_fingerprint == payload and stored.decision == decision:
                    return decision  # first evaluation (or an identical concurrent twin)
                existing = stored
        except Exception as error:  # noqa: BLE001 - any ledger failure must fail closed
            return self._ledger_unavailable(decision, error)

        if existing.payload_fingerprint == payload:
            return {**copy.deepcopy(dict(existing.decision)), "replayed": True}
        return self._conflict(request_id, payload, existing, decision, active)

    @staticmethod
    def _conflict(request_id: str, payload: str, existing: LedgerRecord, attempted: dict, policy: Policy) -> dict:
        original = existing.decision.get("decision_id")
        return {
            "request_id": request_id,
            "verdict": Verdict.HALT.value,
            "reason_code": "IDEMPOTENCY_CONFLICT",
            "reason": (
                f"request_id {request_id} was already decided as {original} for a different payload; "
                "that decision stands. Send a new request_id to ask again"
            ),
            "decided_at_gate": 0,
            "policy_version": policy.version,
            "policy_hash": policy.digest,
            "evaluated_at": attempted["evaluated_at"],
            "decision_id": _derive_id("conflict", request_id, payload),
            "input_hash": attempted["input_hash"],
            "checks": [],
            "replayed": False,
            "original_decision_id": original,
        }

    @staticmethod
    def _ledger_unavailable(decision: dict, error: Exception) -> dict:
        return {
            **decision,
            "verdict": Verdict.HALT.value,
            "reason_code": "LEDGER_UNAVAILABLE",
            "reason": (
                f"the retry ledger could not be reached ({type(error).__name__}), so the decision "
                f"({decision['verdict']}) could not be recorded; failing closed"
            ),
            "decided_at_gate": 0,
            "decision_id": _derive_id("ledger-unavailable", decision["decision_id"]),
            "unrecorded_verdict": decision["verdict"],
        }


_default_service = DecisionService()


def evaluate(request: Any, *, policy: Policy | None = None) -> dict[str, Any]:
    """evaluate(request) -> decision. Retries are remembered for the life of this process."""
    return _default_service.evaluate(request, policy=policy)
