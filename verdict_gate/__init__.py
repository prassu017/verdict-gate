"""Verdict Gate: decides whether an agent's proposed action may proceed.

Returns exactly one of ADMIT, HALT, DEFER or REFER from the request, the policy and the
supplied evidence alone. Deterministic, no clock reads, no LLM.
"""

from .evaluator import GATES, Decision, DecisionService, decide, evaluate
from .ledger import InMemoryLedger, Ledger, LedgerRecord, RedisLedger, ledger_from_env
from .model import CheckResult, Status, Verdict
from .policy import CURRENT_POLICY, POLICIES, POLICY_V1, POLICY_V2, Policy

__all__ = [
    "CURRENT_POLICY",
    "GATES",
    "POLICIES",
    "POLICY_V1",
    "POLICY_V2",
    "CheckResult",
    "Decision",
    "DecisionService",
    "InMemoryLedger",
    "Ledger",
    "LedgerRecord",
    "Policy",
    "RedisLedger",
    "Status",
    "Verdict",
    "decide",
    "evaluate",
    "ledger_from_env",
]
